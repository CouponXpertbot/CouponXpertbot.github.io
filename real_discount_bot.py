import requests
import re
import os
import time
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, urljoin
from playwright.sync_api import sync_playwright
from collections import deque

# ==========================
# Telegram Settings
# ==========================
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = "@Channelboottest"
POSTED_FILE = "posted_courses_realdiscount.txt"

def load_posted_links() -> set:
    if not os.path.exists(POSTED_FILE):
        return set()
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_posted_link(link: str):
    with open(POSTED_FILE, "a", encoding="utf-8") as f:
        f.write(link + "\n")

def clean_udemy_url(url: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    allowed = {}
    if "couponCode" in params:
        allowed["couponCode"] = params["couponCode"][0]
    new_query = urlencode(allowed)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

# ==========================
# Extract Udemy from a single offer page
# ==========================
def extract_udemy_from_offer_page(page, offer_url: str) -> str | None:
    print(f"   🔍 Processing offer page: {offer_url}")
    page.goto(offer_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("load", timeout=60000)
    time.sleep(2)
    
    button = page.locator("a:has-text('Get Course')").first
    if not button.count():
        print(f"   ⚠️ No 'Get Course' button found on {offer_url}")
        return None
    
    affiliate_link = button.get_attribute("href")
    if not affiliate_link:
        return None
    
    if "udemy.com/course/" in affiliate_link:
        final_url = affiliate_link
    else:
        parsed = urlparse(affiliate_link)
        params = parse_qs(parsed.query)
        if "murl" in params:
            import urllib.parse
            final_url = urllib.parse.unquote(params["murl"][0])
        else:
            return None
    
    return clean_udemy_url(final_url)

# ==========================
# Extract all offer links from any page (category or homepage)
# ==========================
def extract_offer_links_from_page(page, url: str) -> list:
    print(f"📂 Scanning page for offers: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("load", timeout=60000)
    time.sleep(2)
    page.evaluate("window.scrollBy(0, 800)")
    time.sleep(2)
    
    offer_links = set()
    links = page.query_selector_all("a")
    for link in links:
        href = link.get_attribute("href")
        if href and "/offer/" in href:
            full_url = urljoin("https://real.discount", href)
            if full_url.startswith("https://real.discount/offer/"):
                offer_links.add(full_url)
    return list(offer_links)

# ==========================
# Extract category/topic links from a page (for crawling)
# ==========================
def extract_category_links(page, base_url: str) -> list:
    """Find links that point to /topics/, /latest-coupons, etc."""
    page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("load", timeout=60000)
    time.sleep(2)
    
    category_links = set()
    links = page.query_selector_all("a")
    for link in links:
        href = link.get_attribute("href")
        if href and (
            "/topics/" in href or
            "/latest-coupons" in href or
            "/freebies" in href or
            "/category/" in href
        ):
            full_url = urljoin("https://real.discount", href)
            if full_url.startswith("https://real.discount"):
                category_links.add(full_url)
    return list(category_links)

# ==========================
# Validate Udemy page (handle cookies and popups)
# ==========================
def is_course_truly_free(page, udemy_url: str) -> bool:
    try:
        print(f"🔍 Validating: {udemy_url}")
        page.goto(udemy_url, wait_until="domcontentloaded", timeout=60000)
        try:
            accept_button = page.locator("button:has-text('Accept')").first
            if accept_button.count():
                accept_button.click()
                time.sleep(1)
        except:
            pass
        page.wait_for_load_state("networkidle", timeout=60000)
        
        price_selectors = [
            "[data-purpose='price-text']",
            ".price-text",
            ".ud-component--course-price--price-part",
            "span[data-purpose='lead-price']",
            ".price-display__price"
        ]
        price_text = None
        for sel in price_selectors:
            try:
                el = page.query_selector(sel)
                if el:
                    price_text = el.inner_text().strip().lower()
                    if price_text:
                        break
            except:
                continue
        
        if not price_text:
            body = page.inner_text("body").lower()
            if "free" in body and not re.search(r'₹\d{2,}', body):
                return True
            if "100% off" in body:
                return True
            return False
        
        if price_text in ["free", "₹0", "$0", "€0", "0", "0.00"]:
            return True
        if "100% off" in price_text:
            return True
        if "free" in price_text:
            return True
        return False
    except Exception as e:
        print(f"⚠️ Validation error: {e}")
        return False

def send_telegram_message(udemy_link: str, title: str) -> bool:
    message = f"🎓 FREE UDEMY COURSE\n\n📘 {title}\n\n🔗 {udemy_link}\n\n⚠️ Coupon may expire soon\n\n👇 More: https://t.me/CouponXpert"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": CHANNEL, "text": message}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"❌ Telegram send failed: {e}")
        return False

# ==========================
# Main Crawler
# ==========================
def main():
    print("🚀 Real.Discount Full-Site Crawler + Bot Started")
    posted = load_posted_links()
    
    # BFS queues
    category_queue = deque(["https://real.discount/"])
    visited_categories = set()
    all_offer_urls = set()
    MAX_PAGES = 100   # safety limit
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()
        
        # Phase 1: Discover all category and offer pages
        print("🌐 Phase 1: Crawling categories and offers...")
        while category_queue and len(visited_categories) < MAX_PAGES:
            cat_url = category_queue.popleft()
            if cat_url in visited_categories:
                continue
            visited_categories.add(cat_url)
            print(f"\n📁 Crawling category: {cat_url}")
            
            # Extract offers from this category page
            offers = extract_offer_links_from_page(page, cat_url)
            all_offer_urls.update(offers)
            print(f"   Found {len(offers)} offers (total unique: {len(all_offer_urls)})")
            
            # Extract more category links to follow
            new_cats = extract_category_links(page, cat_url)
            for nc in new_cats:
                if nc not in visited_categories:
                    category_queue.append(nc)
        
        print(f"\n✅ Discovered {len(all_offer_urls)} unique offer pages.")
        
        # Phase 2: Process each offer (extract Udemy link, validate, post)
        print("\n🚀 Phase 2: Processing offers...")
        new_posts = 0
        MAX_NEW = 3
        
        for offer_url in list(all_offer_urls)[:MAX_PAGES]:  # limit processing
            if new_posts >= MAX_NEW:
                break
            
            udemy = extract_udemy_from_offer_page(page, offer_url)
            if not udemy:
                print(f"⏭️ No Udemy link in {offer_url}")
                continue
            
            if udemy in posted:
                print(f"⏩ Already posted: {udemy}")
                continue
            
            if is_course_truly_free(page, udemy):
                match = re.search(r'/course/([^/?]+)', udemy)
                title = match.group(1).replace('-', ' ').title() if match else "Udemy Course"
                if send_telegram_message(udemy, title):
                    save_posted_link(udemy)
                    posted.add(udemy)
                    new_posts += 1
                    print(f"✅ Posted #{new_posts}: {title}")
                else:
                    print(f"❌ Telegram send failed for {title}")
            else:
                print(f"⏭️ Expired/paid: {udemy}")
        
        browser.close()
    
    print(f"\n🎉 Done. Posted {new_posts} new courses. Crawled {len(visited_categories)} pages, found {len(all_offer_urls)} offers.")

if __name__ == "__main__":
    main()
