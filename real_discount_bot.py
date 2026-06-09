import requests
import re
import os
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
# Fetch offers from Real.discount API (direct JSON)
# ==========================
def fetch_offers_from_api(page=1, per_page=50):
    """Fetch one page of offers from the official API."""
    url = f"https://real.discount/wp-json/rd/v1/offers?page={page}&per_page={per_page}&topic=all&type=free"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return None
        return resp.json()
    except:
        return None

def get_all_udemy_offers(max_pages=25):
    """Fetch all pages and extract Udemy links with titles."""
    all_offers = []
    for page in range(1, max_pages + 1):
        print(f"📡 Fetching page {page}...")
        data = fetch_offers_from_api(page, per_page=50)
        if not data or not isinstance(data, list) or len(data) == 0:
            print(f"   No more offers on page {page} (or invalid response).")
            break
        
        for offer in data:
            # The API returns 'redirect_url' which is the direct Udemy coupon link
            udemy = offer.get("redirect_url") or offer.get("offer_url")
            if not udemy or "udemy.com/course/" not in udemy:
                continue
            title = offer.get("title") or offer.get("name") or "Udemy Course"
            all_offers.append({
                "title": title,
                "url": clean_udemy_url(udemy)
            })
        print(f"   Found {len(data)} offers, total so far: {len(all_offers)}")
        time.sleep(0.5)  # polite
    return all_offers

# ==========================
# Validate Udemy page with Playwright
# ==========================
def is_course_truly_free(page, udemy_url: str) -> bool:
    try:
        print(f"🔍 Validating: {udemy_url}")
        page.goto(udemy_url, wait_until="domcontentloaded", timeout=60000)
        # Dismiss cookie popup if present
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
            return ("free" in body and not re.search(r'₹\d{2,}', body)) or "100% off" in body

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
    print("🚀 Real.Discount API Bot Started")
    posted = load_posted_links()

    # 1. Get all offers from API (no Playwright for discovery)
    offers = get_all_udemy_offers(max_pages=25)   # 25 pages * 50 = 1250 offers max
    print(f"\n✅ Total unique Udemy offers fetched: {len(offers)}")

    if not offers:
        print("⚠️ No offers found. The API may have changed. Exiting.")
        return

    # 2. Validate and post
    new_posts = 0
    MAX_NEW = 3

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        for offer in offers:
            if new_posts >= MAX_NEW:
                break

            udemy = offer["url"]
            if udemy in posted:
                print(f"⏩ Already posted: {udemy}")
                continue

            if is_course_truly_free(page, udemy):
                title = offer["title"]
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
