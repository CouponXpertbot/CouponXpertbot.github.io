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
# Extract all offer links from a page with infinite scroll
# ==========================
def extract_all_offer_links(page, start_url: str, max_scrolls: int = 30) -> list:
    """Loads a page, scrolls repeatedly to load more offers, returns all /offer/ links."""
    print(f"📂 Loading: {start_url}")
    page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("load", timeout=60000)
    time.sleep(3)

    last_count = 0
    same_count_streak = 0
    offer_links = set()

    for scroll_round in range(max_scrolls):
        # Scroll down
        page.evaluate("window.scrollBy(0, 1000)")
        time.sleep(2)

        # Extract current offer links
        links = page.query_selector_all("a")
        new_offers = set()
        for link in links:
            href = link.get_attribute("href")
            if href and "/offer/" in href:
                full = urljoin("https://real.discount", href)
                if full.startswith("https://real.discount/offer/"):
                    new_offers.add(full)
        offer_links.update(new_offers)

        print(f"   Scroll {scroll_round+1}: found {len(offer_links)} unique offers")

        if len(offer_links) == last_count:
            same_count_streak += 1
            if same_count_streak >= 3:
                print("   No new offers after 3 scrolls – stopping scroll.")
                break
        else:
            same_count_streak = 0
        last_count = len(offer_links)

    return list(offer_links)

# ==========================
# Extract Udemy link from a single offer page
# ==========================
def extract_udemy_from_offer_page(page, offer_url: str) -> str | None:
    print(f"   🔍 Processing offer page: {offer_url}")
    page.goto(offer_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("load", timeout=60000)
    time.sleep(2)

    # Try to find the "Get Course" button
    button = page.locator("a:has-text('Get Course')").first
    if not button.count():
        print(f"   ⚠️ No 'Get Course' button found")
        return None

    affiliate_link = button.get_attribute("href")
    if not affiliate_link:
        return None

    if "udemy.com/course/" in affiliate_link:
        final = affiliate_link
    else:
        # Possibly a tracking link with murl parameter
        parsed = urlparse(affiliate_link)
        params = parse_qs(parsed.query)
        if "murl" in params:
            import urllib.parse
            final = urllib.parse.unquote(params["murl"][0])
        else:
            return None

    return clean_udemy_url(final)

# ==========================
# Validate Udemy price using Playwright
# ==========================
def is_course_truly_free(page, udemy_url: str) -> bool:
    try:
        print(f"🔍 Validating: {udemy_url}")
        page.goto(udemy_url, wait_until="domcontentloaded", timeout=60000)
        # Dismiss cookie popup
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
    print("🚀 Real.Discount Full Crawler + Bot Started")
    posted = load_posted_links()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        # Step 1: Get all offer links from the homepage (infinite scroll)
        offer_links = extract_all_offer_links(page, "https://real.discount/", max_scrolls=30)
        print(f"\n✅ Total offer links found: {len(offer_links)}")

        # Step 2: Process each offer
        new_posts = 0
        MAX_NEW = 3

        for offer_url in offer_links[:100]:   # limit to first 100 offers
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
                # Try to get title from the offer page (or fallback)
                try:
                    title = page.title().replace(" | Real.Discount", "").strip()
                except:
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
