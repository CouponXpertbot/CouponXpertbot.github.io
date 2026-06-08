import requests
from bs4 import BeautifulSoup
import re
import os
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from playwright.sync_api import sync_playwright

# ==========================
# Telegram Settings (from GitHub Secrets)
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
# Extract Udemy link from a Real.Discount offer page
# ==========================
def extract_udemy_from_offer_page(page, offer_url: str) -> str | None:
    """
    Given a Real.Discount offer page (e.g., /offer/...), 
    finds the 'Get Course' button and extracts the final Udemy URL.
    Returns cleaned Udemy URL or None.
    """
    print(f"   🔍 Processing offer page: {offer_url}")
    page.goto(offer_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("networkidle")
    
    # Find the "Get Course" button
    button = page.locator("a:has-text('Get Course')").first
    if not button.count():
        print(f"   ⚠️ No 'Get Course' button found on {offer_url}")
        return None
    
    affiliate_link = button.get_attribute("href")
    if not affiliate_link:
        print(f"   ⚠️ Button has no href on {offer_url}")
        return None
    
    print(f"   🧩 Affiliate link: {affiliate_link}")
    
    # Case 1: already a Udemy link
    if "udemy.com/course/" in affiliate_link:
        final_url = affiliate_link
    else:
        # Case 2: Linksynergy / tracking link – extract murl parameter
        parsed = urlparse(affiliate_link)
        params = parse_qs(parsed.query)
        if "murl" in params:
            import urllib.parse
            final_url = urllib.parse.unquote(params["murl"][0])
        else:
            print(f"   ❌ Could not find murl parameter in {affiliate_link}")
            return None
    
    # Clean and return
    cleaned = clean_udemy_url(final_url)
    print(f"   ✨ Udemy link: {cleaned}")
    return cleaned

# ==========================
# Get all offer links from a category page
# ==========================
def extract_offer_links_from_category(page, category_url: str) -> list:
    """
    Given a category page (e.g., /topics/...), returns a list of offer URLs.
    """
    print(f"📂 Scanning category page: {category_url}")
    page.goto(category_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("networkidle")
    
    # Find all <a> tags whose href matches https://real.discount/offer/...
    offer_links = []
    links = page.query_selector_all("a[href*='/offer/']")
    for link in links:
        href = link.get_attribute("href")
        if href and href.startswith("https://real.discount/offer/"):
            offer_links.append(href)
    
    # Remove duplicates
    offer_links = list(dict.fromkeys(offer_links))
    print(f"   Found {len(offer_links)} offer links in category")
    return offer_links

# ==========================
# Main scraping logic – handles any Real.Discount URL
# ==========================
def scrape_real_discount_url(page, url: str, visited: set) -> list:
    """
    Recursively processes a Real.Discount URL.
    Returns a list of cleaned Udemy URLs.
    """
    if url in visited:
        return []
    visited.add(url)
    
    # Determine type: category or single offer?
    if "/offer/" in url:
        udemy = extract_udemy_from_offer_page(page, url)
        return [udemy] if udemy else []
    elif "/topics/" in url or "/latest-coupons" in url or "/freebies" in url:
        # Category page – get all offer links and process them
        offer_urls = extract_offer_links_from_category(page, url)
        all_udemy = []
        for offer in offer_urls:
            all_udemy.extend(scrape_real_discount_url(page, offer, visited))
        return all_udemy
    else:
        print(f"⚠️ Unknown Real.Discount URL type: {url}")
        return []

# ==========================
# Validate with Playwright (copied from your existing bot)
# ==========================
def is_course_truly_free(page, udemy_url: str) -> bool:
    try:
        print(f"🔍 Validating: {udemy_url}")
        page.goto(udemy_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_load_state("networkidle")
        price_selectors = ["[data-purpose='price-text']", ".price-text", ".ud-component--course-price--price-part"]
        price_text = None
        for sel in price_selectors:
            el = page.query_selector(sel)
            if el:
                price_text = el.inner_text().strip().lower()
                break
        if not price_text:
            body = page.inner_text("body").lower()
            return "free" in body and not re.search(r'₹\d{2,}', body)
        price_text = price_text.lower()
        if price_text in ["free", "₹0", "$0", "€0", "0", "0.00"] or "100% off" in price_text:
            return True
        return False
    except Exception as e:
        print(f"⚠️ Validation error: {e}")
        return False

# ==========================
# Send Telegram message (optional AI integration)
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
    
    # List of starting URLs (you can also make it read from a file)
    START_URLS = [
        "https://real.discount/offer/claude-ai-for-data-analysis-business-intelligence-491",
        "https://real.discount/topics/top_free_courses/Python",
        # Add more category or offer links here
    ]
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = context.new_page()
        visited = set()
        
        # Collect all Udemy links from all starting URLs
        all_udemy_links = []
        for start in START_URLS:
            links = scrape_real_discount_url(page, start, visited)
            all_udemy_links.extend(links)
        
        # Deduplicate
        all_udemy_links = list(dict.fromkeys(all_udemy_links))
        print(f"\n📊 Total unique Udemy links found: {len(all_udemy_links)}")
        
        # Now validate each link and post if free
        new_posts = 0
        MAX_NEW = 3
        for udemy in all_udemy_links:
            if new_posts >= MAX_NEW:
                break
            if udemy in posted:
                print(f"⏩ Already posted: {udemy}")
                continue
            if is_course_truly_free(page, udemy):
                # Extract title from URL
                title = udemy.split('/course/')[1].split('/')[0].replace('-', ' ').title()
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
