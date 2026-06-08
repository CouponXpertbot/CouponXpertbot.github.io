import requests
import re
import os
import json
import time
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
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
# Extract Udemy offers by intercepting network responses
# ==========================
def fetch_offers_via_playwright():
    """
    Uses Playwright to load real.discount and capture the JSON response
    that contains all offer data. Returns list of (title, udemy_url).
    """
    offers = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        # Set up a listener to capture JSON responses
        def handle_response(response):
            # Look for URLs that match the pattern of the offer API
            if "wp-json/rd/v1/offers" in response.url:
                try:
                    data = response.json()
                    if isinstance(data, list):
                        for item in data:
                            title = item.get("title") or item.get("name")
                            udemy = item.get("redirect_url") or item.get("offer_url")
                            if udemy and "udemy.com/course/" in udemy:
                                offers.append((title, clean_udemy_url(udemy)))
                except:
                    pass

        page.on("response", handle_response)

        # Load the homepage and scroll to trigger API calls
        print("🌐 Loading real.discount...")
        page.goto("https://real.discount/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_load_state("load", timeout=60000)
        # Scroll several times to load more offers
        for _ in range(5):
            page.evaluate("window.scrollBy(0, 800)")
            time.sleep(2)

        # Wait a bit for all network requests to finish
        time.sleep(5)
        browser.close()

    return offers

# ==========================
# Validate Udemy page (Playwright)
# ==========================
def is_course_truly_free(page, udemy_url: str) -> bool:
    try:
        print(f"🔍 Validating: {udemy_url}")
        page.goto(udemy_url, wait_until="domcontentloaded", timeout=60000)
        try:
            accept = page.locator("button:has-text('Accept')").first
            if accept.count():
                accept.click()
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

def main():
    print("🚀 Real.Discount Network Interceptor Bot Started")
    posted = load_posted_links()

    # Fetch offers using Playwright network interception
    offers = fetch_offers_via_playwright()
    print(f"✅ Found {len(offers)} offers via network capture.")

    if not offers:
        print("⚠️ No offers captured. The website may have changed its API pattern.")
        return

    # Deduplicate by Udemy URL
    unique_offers = {}
    for title, url in offers:
        if url not in unique_offers:
            unique_offers[url] = title

    print(f"📊 Unique Udemy links: {len(unique_offers)}")

    new_posts = 0
    MAX_NEW = 3

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        for udemy_url, title in unique_offers.items():
            if new_posts >= MAX_NEW:
                break
            if udemy_url in posted:
                print(f"⏩ Already posted: {udemy_url}")
                continue
            if is_course_truly_free(page, udemy_url):
                if send_telegram_message(udemy_url, title):
                    save_posted_link(udemy_url)
                    posted.add(udemy_url)
                    new_posts += 1
                    print(f"✅ Posted #{new_posts}: {title}")
                else:
                    print(f"❌ Telegram send failed for {title}")
            else:
                print(f"⏭️ Expired/paid: {udemy_url}")

        browser.close()

    print(f"\n🎉 Done. Posted {new_posts} new courses.")

if __name__ == "__main__":
    main()
