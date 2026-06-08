import requests
import re
import os
import time
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, urljoin
from playwright.sync_api import sync_playwright

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
    time.sleep(2)  # Let JS populate the button
    
    # Look for "Get Course" button – try different text variations
    button = page.locator("a:has-text('Get Course')").first
    if not button.count():
        # Fallback: any link containing "Get Course"
        button = page.locator("a:has-text('Get Course')").first
        if not button.count():
            print(f"   ⚠️ No 'Get Course' button found on {offer_url}")
            return None
    
    affiliate_link = button.get_attribute("href")
    if not affiliate_link:
        print(f"   ⚠️ Button has no href on {offer_url}")
        return None
    
    print(f"   🧩 Affiliate link: {affiliate_link}")
    
    if "udemy.com/course/" in affiliate_link:
        final_url = affiliate_link
    else:
        parsed = urlparse(affiliate_link)
        params = parse_qs(parsed.query)
        if "murl" in params:
            import urllib.parse
            final_url = urllib.parse.unquote(params["murl"][0])
        else:
            print(f"   ❌ Could not find murl parameter in {affiliate_link}")
            return None
    
    cleaned = clean_udemy_url(final_url)
    print(f"   ✨ Udemy link: {cleaned}")
    return cleaned

# ==========================
# Extract all offer links from a category page
# ==========================
def extract_offer_links_from_category(page, category_url: str) -> list:
    print(f"📂 Scanning category page: {category_url}")
    page.goto(category_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("load", timeout=60000)
    time.sleep(2)
    # Scroll to trigger lazy loading
    page.evaluate("window.scrollBy(0, 800)")
    time.sleep(2)
    
    # Get all <a> tags and extract hrefs that contain '/offer/'
    offer_links = set()
    links = page.query_selector_all("a")
    for link in links:
        href = link.get_attribute("href")
        if href and "/offer/" in href:
            # Make absolute URL
            full_url = urljoin("https://real.discount", href)
            if full_url.startswith("https://real.discount/offer/"):
                offer_links.add(full_url)
    
    print(f"   Found {len(offer_links)} offer links in category")
    return list(offer_links)

# ==========================
# Recursive dispatcher
# ==========================
def scrape_real_discount_url(page, url: str, visited: set) -> list:
    if url in visited:
        return []
    visited.add(url)
    
    if "/offer/" in url:
        udemy = extract_udemy_from_offer_page(page, url)
        return [udemy] if udemy else []
    elif "/topics/" in url or "/latest-coupons" in url or "/freebies" in url:
        offer_urls = extract_offer_links_from_category(page, url)
        all_udemy = []
        for offer in offer_urls:
            all_udemy.extend(scrape_real_discount_url(page, offer, visited))
        return all_udemy
    else:
        print(f"⚠️ Unknown Real.Discount URL type: {url}")
        return []

# ==========================
# Validate Udemy page (handle cookies and popups)
# ==========================
def is_course_truly_free(page, udemy_url: str) -> bool:
    try:
        print(f"🔍 Validating: {udemy_url}")
        page.goto(udemy_url, wait_until="domcontentloaded", timeout=60000)
        
        # Handle cookie consent popup if present
        try:
            accept_button = page.locator("button:has-text('Accept')").first
            if accept_button.count():
                accept_button.click()
                time.sleep(1)
        except:
            pass
        
        page.wait_for_load_state("networkidle", timeout=60000)
        
        # Many Udemy price selectors
        price_selectors = [
            "[data-purpose='price-text']",
            ".price-text",
            ".ud-component--course-price--price-part",
            "span[data-purpose='lead-price']",
            ".price-display__price",
            ".course-price-text",
            ".price-part__current-price",
            ".price-text--current-price"
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
            # Fallback: entire body text
            body = page.inner_text("body").lower()
            if "free" in body and not re.search(r'₹\d{2,}', body):
                return True
            if "100% off" in body:
                return True
            return False
        
        if price_text in ["free", "₹0", "$0", "€0", "0", "0.00", "0.0"]:
            return True
        if "100% off" in price_text:
            return True
        if "free" in price_text:
            return True
        
        # Negative checks
        if re.search(r'₹\d+', price_text):
            return False
        if re.search(r'\$\d+', price_text):
            return False
        if re.search(r'\d+% off', price_text) and "100%" not in price_text:
            return False
        if re.search(r'[0-9]+(\.[0-9]{2})?', price_text):
            return False
        
        return False
    except Exception as e:
        print(f"⚠️ Validation error: {e}")
        return False

# ==========================
# Send Telegram message
# ==========================
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
# Main
# ==========================
def main():
    print("🚀 Real.Discount Scraper + Telegram Bot Started")
    posted = load_posted_links()
    
    START_URLS = [
        "https://real.discount/offer/claude-ai-for-data-analysis-business-intelligence-491",
        "https://real.discount/topics/top_free_courses/Python",
    ]
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()
        visited = set()
        
        all_udemy_links = []
        for start in START_URLS:
            links = scrape_real_discount_url(page, start, visited)
            all_udemy_links.extend(links)
        
        all_udemy_links = list(dict.fromkeys(all_udemy_links))
        print(f"\n📊 Total unique Udemy links found: {len(all_udemy_links)}")
        
        new_posts = 0
        MAX_NEW = 3
        for udemy in all_udemy_links:
            if new_posts >= MAX_NEW:
                break
            if udemy in posted:
                print(f"⏩ Already posted: {udemy}")
                continue
            if is_course_truly_free(page, udemy):
                # Extract title from URL slug
                match = re.search(r'/course/([^/?]+)', udemy)
                if match:
                    title = match.group(1).replace('-', ' ').title()
                else:
                    title = "Udemy Course"
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
    
    print(f"\n🎉 Done. Posted {new_posts} new courses.")

if __name__ == "__main__":
    main()
