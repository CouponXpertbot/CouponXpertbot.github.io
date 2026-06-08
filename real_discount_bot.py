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
# Extract all offer links from a page (with scrolling and pagination)
# ==========================
def extract_all_offer_links_from_page(page, start_url: str, max_scrolls: int = 10) -> list:
    """
    Loads a page, scrolls to trigger lazy loading, then extracts all /offer/ links.
    Also follows pagination (Next page) and collects links across multiple pages.
    """
    print(f"📂 Scanning: {start_url}")
    page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("load", timeout=60000)
    time.sleep(2)

    all_offers = set()
    current_url = start_url
    page_number = 1

    while True:
        print(f"   Scrolling page {page_number} to load content...")
        # Scroll repeatedly to load dynamic content
        last_height = page.evaluate("document.body.scrollHeight")
        for _ in range(max_scrolls):
            page.evaluate("window.scrollBy(0, 800)")
            time.sleep(1.5)
            new_height = page.evaluate("document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

        # Extract all offer links currently on the page
        links = page.query_selector_all("a")
        for link in links:
            href = link.get_attribute("href")
            if href and "/offer/" in href:
                full_url = urljoin("https://real.discount", href)
                if full_url.startswith("https://real.discount/offer/"):
                    all_offers.add(full_url)

        print(f"   Found {len(all_offers)} unique offers so far on page {page_number}")

        # Look for "Next" pagination link
        next_link = None
        # Common selectors for next page
        next_selectors = [
            "a:has-text('Next')",
            "a:has-text('next')",
            "a[rel='next']",
            ".pagination .next a",
            ".next-page a"
        ]
        for sel in next_selectors:
            try:
                elem = page.query_selector(sel)
                if elem:
                    next_link = elem.get_attribute("href")
                    if next_link:
                        break
            except:
                continue

        if not next_link:
            print("   No more pages found.")
            break

        # Go to next page
        next_url = urljoin(current_url, next_link)
        if next_url == current_url:
            break
        print(f"   Going to next page: {next_url}")
        page.goto(next_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_load_state("load", timeout=60000)
        time.sleep(2)
        current_url = next_url
        page_number += 1

    return list(all_offers)

# ==========================
# Discover category/topic pages from the homepage
# ==========================
def discover_category_pages(page) -> list:
    """Scans the homepage for links to category/topic pages."""
    print("🌐 Discovering category pages from homepage...")
    page.goto("https://real.discount/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("load", timeout=60000)
    time.sleep(2)
    # Scroll a bit to reveal all nav links
    page.evaluate("window.scrollBy(0, 300)")
    time.sleep(1)

    category_urls = set()
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
            if full_url.startswith("https://real.discount") and not full_url.endswith("#"):
                category_urls.add(full_url)

    print(f"   Found {len(category_urls)} category pages: {list(category_urls)[:5]}...")
    return list(category_urls)

# ==========================
# Extract Udemy from a single offer page (same as before)
# ==========================
def extract_udemy_from_offer_page(page, offer_url: str) -> str | None:
    print(f"   🔍 Processing offer page: {offer_url}")
    page.goto(offer_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("load", timeout=60000)
    time.sleep(2)
    
    button = page.locator("a:has-text('Get Course')").first
    if not button.count():
        print(f"   ⚠️ No 'Get Course' button found")
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
# Main crawler
# ==========================
def main():
    print("🚀 Real.Discount Full-Site Crawler + Bot Started")
    posted = load_posted_links()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        # Step 1: Discover all category pages from homepage
        category_pages = discover_category_pages(page)
        # Also include the homepage itself (it contains offers)
        all_pages_to_crawl = ["https://real.discount/"] + category_pages
        all_pages_to_crawl = list(dict.fromkeys(all_pages_to_crawl))  # deduplicate
        print(f"\n📁 Total pages to crawl for offers: {len(all_pages_to_crawl)}")

        # Step 2: Crawl each page (with scrolling & pagination) to collect all offer links
        all_offer_urls = set()
        for page_url in all_pages_to_crawl[:20]:  # Limit to 20 categories to avoid too many requests
            offers = extract_all_offer_links_from_page(page, page_url, max_scrolls=8)
            all_offer_urls.update(offers)
            print(f"   Running total unique offers: {len(all_offer_urls)}")

        print(f"\n✅ Discovered {len(all_offer_urls)} unique offer pages.")

        # Step 3: Process each offer (extract Udemy link, validate, post)
        print("\n🚀 Processing offers...")
        new_posts = 0
        MAX_NEW = 3

        for offer_url in list(all_offer_urls):
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

    print(f"\n🎉 Done. Posted {new_posts} new courses. Found {len(all_offer_urls)} total offers.")

if __name__ == "__main__":
    main()
