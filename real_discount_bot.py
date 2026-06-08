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
# Fetch all offers from Real.discount API
# ==========================
def fetch_all_offers_from_api(max_pages: int = 20) -> list:
    """
    Calls the Real.discount JSON API and returns a list of offer dicts.
    Each dict contains: title, udemy_url (already with coupon), etc.
    """
    all_offers = []
    page = 1
    per_page = 36

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    while page <= max_pages:
        url = f"https://real.discount/wp-json/rd/v1/offers?page={page}&per_page={per_page}&topic=all&type=free"
        print(f"📡 Fetching page {page} from API...")
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                print(f"   API returned {resp.status_code}, stopping.")
                break
            data = resp.json()
            if not data or not isinstance(data, list):
                print("   No more offers or invalid response.")
                break

            # Extract relevant fields
            for offer in data:
                # The API often returns 'offer_url' which is the Real.discount offer page,
                # but also 'redirect_url' which is the actual Udemy coupon link.
                # We'll take the direct Udemy link if available.
                udemy_url = offer.get("redirect_url") or offer.get("offer_url")
                title = offer.get("title") or offer.get("name") or "Udemy Course"

                if udemy_url and "udemy.com/course/" in udemy_url:
                    all_offers.append({
                        "title": title,
                        "raw_udemy": udemy_url,
                        "cleaned_udemy": clean_udemy_url(udemy_url)
                    })

            print(f"   Found {len(data)} offers, total so far: {len(all_offers)}")
            page += 1
            time.sleep(0.5)  # be polite

        except Exception as e:
            print(f"   API error: {e}")
            break

    return all_offers

# ==========================
# Validate Udemy page (Playwright)
# ==========================
def is_course_truly_free(page, udemy_url: str) -> bool:
    try:
        print(f"🔍 Validating: {udemy_url}")
        page.goto(udemy_url, wait_until="domcontentloaded", timeout=60000)
        # Handle cookie popup
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
    print("🚀 Real.Discount API Scraper + Bot Started")
    posted = load_posted_links()

    # 1. Fetch all offers from the API (no Playwright needed for discovery)
    offers = fetch_all_offers_from_api(max_pages=30)  # ~1080 offers
    print(f"\n✅ Total offers fetched from API: {len(offers)}")

    # 2. Validate each with Playwright and post
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

            udemy = offer["cleaned_udemy"]
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
