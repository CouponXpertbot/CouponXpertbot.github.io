import requests
from bs4 import BeautifulSoup
import re
import os
from typing import List, Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ==========================
# Telegram Settings (from GitHub Secrets)
# ==========================
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = "@Channelboottest"      # e.g., "@Channelboottest"

# ==========================
# Debug toggle — prints extra diagnostics when extraction fails
# ==========================
DEBUG = True

# ==========================
# Persistent Storage
# ==========================
POSTED_FILE = "posted_courses_coursefolder.txt"

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
# Robust HTTP client (avoid 406) — used only for Telegram preview page
# ==========================
def get_html(url: str) -> Optional[str]:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
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
# Stealth context helper — masks common automation fingerprints
# ==========================
def make_stealth_context(browser):
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1366, "height": 768},
        locale="en-US",
    )
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
    """)
    return context

# ==========================
# Extract Udemy link from a coursefolder.net page (FIXED)
# ==========================
UDEMY_LINK_RE = re.compile(
    r"https?://(?:www\.)?udemy\.com/course/[^\s'\"<>]+couponCode=[A-Za-z0-9]+[^\s'\"<>]*",
    re.IGNORECASE,
)

def extract_udemy_link_playwright(page, cf_url: str) -> Optional[str]:
    """
    Navigate to coursefolder.net and extract the Udemy URL.

    Key fixes vs the original version:
      - wait_until='domcontentloaded' instead of 'networkidle'. Coupon sites
        run continuous ad/analytics requests that often never go idle, which
        made the old code hang until Playwright's default 30s timeout fired
        and raised an UNCAUGHT TimeoutError, killing the whole run.
      - All navigation/selector calls are wrapped in try/except so a single
        bad page is skipped (and logged) instead of crashing the script.
      - A regex fallback scans the raw page HTML for the udemy.com/course
        link directly — this is what actually saves the day, since the
        "Get Free Coupon" link is plain static HTML on this site, no JS
        rendering required.
    """
    print(f"🌐 Loading {cf_url}")

    try:
        page.goto(cf_url, wait_until="domcontentloaded", timeout=25_000)
    except PWTimeout:
        print(f"  ⏱️  Timeout loading page (skipping): {cf_url}")
        return None
    except Exception as e:
        print(f"  ⚠️  Navigation error (skipping): {e}")
        return None

    # Let any lazy-loaded widgets settle, but don't block on networkidle
    page.wait_for_timeout(800)

    # 1. Try to find the "Get Free Coupon" anchor directly
    try:
        anchor = page.wait_for_selector(
            "a:has-text('Get Free Coupon')", timeout=8_000
        )
        href = anchor.get_attribute("href")
        if href and "udemy.com/course" in href:
            return href
    except PWTimeout:
        pass  # fall through to regex fallback
    except Exception as e:
        if DEBUG:
            print(f"  ⚠️  Selector error: {e}")

    # 2. Fallback: regex-scan the rendered HTML for any udemy.com/course link
    try:
        html = page.content()
    except Exception as e:
        print(f"  ⚠️  Could not read page content: {e}")
        return None

    match = UDEMY_LINK_RE.search(html)
    if match:
        return match.group(0)

    # 3. Nothing found — print diagnostics to help spot anti-bot walls
    if DEBUG:
        try:
            title = page.title()
        except Exception:
            title = "<unknown>"
        blocked_markers = ["just a moment", "attention required",
                            "checking your browser", "access denied",
                            "are you human"]
        if any(m in title.lower() for m in blocked_markers):
            print(f"  🚧 Possible anti-bot block page. Title: '{title}'")
        else:
            print(f"  ❓ No Udemy link found. Page title: '{title}'")

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

    Fixes vs original:
      - domcontentloaded instead of networkidle (Udemy pages are tracker-heavy
        too, same issue as coursefolder.net).
      - Checks embedded JSON-LD structured data first (most reliable, doesn't
        depend on CSS classes that change with UI redesigns).
      - try/except wraps the whole thing; any failure -> False (skip), logged.
    """
    try:
        print(f"🔍 Validating: {udemy_url}")
        page.goto(udemy_url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(1500)

        html = page.content()

        # Strategy 1: JSON-LD structured data (most stable across redesigns)
        ld_blocks = re.findall(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.DOTALL | re.IGNORECASE,
        )
        for block in ld_blocks:
            price_match = re.search(r'"price"\s*:\s*"?0(\.0+)?"?', block)
            if price_match:
                return True

        # Strategy 2: CSS selectors for the visible price
        price_selectors = [
            "[data-purpose='price-text']",
            ".price-text",
            ".ud-component--course-price--price-part",
            "span[data-purpose='lead-price']",
            ".price-display__price",
            ".course-price-text",
        ]
        price_text = None
        for selector in price_selectors:
            element = page.query_selector(selector)
            if element:
                price_text = element.inner_text().strip().lower()
                break

        if price_text:
            if price_text in ["free", "₹0", "$0", "€0", "0", "0.00"]:
                return True
            if "100% off" in price_text or "free" in price_text:
                return True
            if re.search(r'[₹$€]\s?\d', price_text):
                return False
            if re.search(r'\d+%\s*off', price_text) and "100%" not in price_text:
                return False
            return False

        # Strategy 3: fallback to whole-body text
        body = page.inner_text("body").lower()
        if "free" in body and not re.search(r'[₹$€]\s?\d{2,}', body):
            return True

        if DEBUG:
            print(f"  ❓ Could not determine price for {udemy_url}")
        return False

    except PWTimeout:
        print(f"  ⏱️  Timeout validating {udemy_url}")
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
        if r.status_code != 200:
            print(f"❌ Telegram API error {r.status_code}: {r.text[:200]}")
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

    if not cf_links:
        print("⚠️  No links found from Telegram preview. The page layout "
              "may have changed, or t.me/s/<channel> is blocking this request.")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = make_stealth_context(browser)
        page = context.new_page()

        new_posts = 0
        MAX_NEW = 3   # post at most 3 new courses per run

        for cf_url in cf_links:
            if new_posts >= MAX_NEW:
                break

            try:
                udemy = extract_udemy_link_playwright(page, cf_url)
            except Exception as e:
                print(f"  ⚠️  Unexpected error extracting from {cf_url}: {e}")
                continue

            if not udemy:
                print(f"⏭️  No Udemy link in {cf_url}")
                continue

            udemy = clean_udemy_url(udemy)

            if udemy in posted:
                print(f"⏩ Already posted: {udemy}")
                continue

            try:
                free_ok = is_course_truly_free(page, udemy)
            except Exception as e:
                print(f"  ⚠️  Unexpected error validating {udemy}: {e}")
                continue

            if not free_ok:
                print(f"⚠️ Coupon not fully free or expired: {udemy} -> skipping")
                continue

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
