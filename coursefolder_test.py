import requests
from bs4 import BeautifulSoup
import re
import os
from typing import List, Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, urljoin
from playwright.sync_api import sync_playwright

# ==========================
# Telegram Settings (from GitHub Secrets)
# ==========================
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = "@Channelboottest"      # e.g., "@Channelboottest"

# ==========================
# Persistent Storage
# ==========================
POSTED_FILE = "posted_courses.txt"

def load_posted_links() -> set:
    if not os.path.exists(POSTED_FILE):
        return set()
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_posted_link(link: str):
    with open(POSTED_FILE, "a", encoding="utf-8") as f:
        f.write(link + "\n")

# ==========================
# URL cleaning: keep only couponCode
# ==========================
def clean_udemy_url(url: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    allowed = {}
    if "couponCode" in params:
        allowed["couponCode"] = params["couponCode"][0]
    new_query = urlencode(allowed)
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment
    ))

# ==========================
# Robust HTTP client (avoid 406)
# ==========================
def get_html(url: str) -> Optional[str]:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
    }
    try:
        resp = requests.get(url, headers=headers, timeout=45)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"❌ Failed to fetch {url}: {e}")
        return None

# ==========================
# NEW: Extract Udemy link using Playwright (handles dynamic content)
# ==========================
def extract_udemy_link_playwright(page, cf_url: str) -> Optional[str]:
    """
    Navigate to coursefolder.net and extract the Udemy URL from the 'Get Free Coupon' button.
    Uses Playwright to handle dynamic content and attribute extraction.
    """
    print(f"🌐 Loading {cf_url}")
    page.goto(cf_url, wait_until="networkidle")
    page.wait_for_timeout(1000)  # let any final JS settle

    # 1. Find the button/link with the text "Get Free Coupon"
    button = page.query_selector("a:has-text('Get Free Coupon'), button:has-text('Get Free Coupon')")
    if not button:
        # Fallback: any element with text containing "free coupon"
        button = page.query_selector("*:has-text('Get Free Coupon')")

    if button:
        # Try to extract from href
        href = button.get_attribute("href")
        if href:
            # Resolve relative URLs
            if not href.startswith("http"):
                href = urljoin(cf_url, href)   # Playwright's page.urljoin also works, but use urljoin for safety
            if "udemy.com" in href:
                return href

        # Try onclick attribute
        onclick = button.get_attribute("onclick")
        if onclick:
            match = re.search(r"https?://(?:www\.)?udemy\.com/course/[^'\"]+", onclick)
            if match:
                return match.group(0)

        # Try data-* attributes (common: data-link, data-url, data-href)
        for attr in ["data-link", "data-url", "data-href", "data-course-url"]:
            val = button.get_attribute(attr)
            if val and "udemy.com" in val:
                return val

    # 2. If button not found or no link, search the entire page for any Udemy URL
    content = page.content()
    match = re.search(r"https?://(?:www\.)?udemy\.com/course/[^'\"]+", content)
    if match:
        return match.group(0)

    return None

# ==========================
# Scrape Telegram channel (public web preview)
# ==========================
def scrape_telegram_channel_links(channel: str = "coursefolder", limit: int = 20) -> List[str]:
    url = f"https://t.me/s/{channel}"
    html = get_html(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    messages = soup.find_all("div", class_="tgme_widget_message")

    coursefolder_urls = []
    for msg in messages[:limit]:
        for a in msg.find_all("a", href=True):
            href = a["href"]
            if not re.match(r"https?://coursefolder\.net/", href):
                continue

            # Filter out useless links
            if "/liveLanguage/" in href:
                continue
            if "/liveCategory/" in href:
                continue
            if "live-free-udemy-coupon" in href:
                continue

            coursefolder_urls.append(href)

    return list(dict.fromkeys(coursefolder_urls))

# ==========================
# Validate Udemy coupon using Playwright (reuses page)
# ==========================
def is_course_truly_free(page, udemy_url: str) -> bool:
    """
    Uses an already-opened Playwright page to check if the Udemy course is free.
    Returns True if free (₹0, $0, Free, 100% off), False otherwise.
    """
    try:
        print(f"🔍 Validating: {udemy_url}")
        page.goto(udemy_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_load_state("networkidle")

        # Try multiple selectors for price text
        price_selectors = [
            "[data-purpose='price-text']",
            ".price-text",
            ".ud-component--course-price--price-part",
            "span[data-purpose='lead-price']",
            ".price-display__price",
            ".course-price-text"
        ]

        price_text = None
        for selector in price_selectors:
            element = page.query_selector(selector)
            if element:
                price_text = element.inner_text().strip().lower()
                break

        if not price_text:
            # Fallback: check whole page body for price patterns
            body = page.inner_text("body").lower()
            if "free" in body and not re.search(r'₹\d{2,}', body):
                return True
            return False

        # Analyze price_text
        price_text = price_text.lower()

        # Positive indicators (free)
        if price_text in ["free", "₹0", "$0", "€0", "0", "0.00"]:
            return True
        if "100% off" in price_text:
            return True
        if "free" in price_text:
            return True

        # Negative indicators (paid)
        if re.search(r'₹\d+', price_text):
            return False
        if re.search(r'\$\d+', price_text):
            return False
        if re.search(r'\d+% off', price_text) and "100%" not in price_text:
            return False
        if re.search(r'[0-9]+(\.[0-9]{2})?', price_text):
            return False

        # If no clear price but also no free indicator, assume not free
        return False

    except Exception as e:
        print(f"⚠️ Playwright validation error: {e}")
        return False

# ==========================
# Send message to your Telegram channel
# ==========================
def send_telegram_message(udemy_link: str, title: str) -> bool:
    message = f"""🎓 FREE UDEMY COURSE (100% OFF)

📘 Course: {title}

🚀 Enroll before the coupon expires!

🔗 Enroll Here:
{udemy_link}

⚠️ Coupon may expire anytime

👇 More FREE books & courses
👉 https://t.me/CouponXpert"""

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHANNEL, "text": message}
    try:
        r = requests.post(url, data=data, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"❌ Telegram send failed: {e}")
        return False

# ==========================
# Main routine
# ==========================
def main():
    print("🔍 Starting bot...")
    posted = load_posted_links()
    print(f"Already posted: {len(posted)} Udemy links")

    cf_links = scrape_telegram_channel_links()
    print(f"Found {len(cf_links)} coursefolder.net links in channel")

    # Launch browser once
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        new_posts = 0
        MAX_NEW = 3   # post at most 3 new courses per run

        for cf_url in cf_links:
            if new_posts >= MAX_NEW:
                break

            # NEW: Use Playwright-based extractor
            udemy = extract_udemy_link_playwright(page, cf_url)
            if not udemy:
                print(f"⏭️ No Udemy link in {cf_url}")
                continue

            # Clean the URL: keep only couponCode parameter
            udemy = clean_udemy_url(udemy)

            if udemy in posted:
                print(f"⏩ Already posted: {udemy}")
                continue

            # Validate coupon with Playwright (pass the page)
            if not is_course_truly_free(page, udemy):
                print(f"⚠️ Coupon not fully free or expired: {udemy} -> skipping")
                continue

            # Generate a nice title from the URL slug
            slug = cf_url.rstrip('/').split('/')[-1]
            title = slug.replace('-', ' ').title()

            if send_telegram_message(udemy, title):
                save_posted_link(udemy)
                posted.add(udemy)
                new_posts += 1
                print(f"✅ Posted #{new_posts}: {title}")
            else:
                print(f"❌ Failed to send {title}")

        browser.close()

    print(f"🎉 Done. Posted {new_posts} new courses.")

if __name__ == "__main__":
    main()
