import asyncio
import os
import re
import requests
from typing import List, Set
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from playwright.async_api import async_playwright, TimeoutError as PwTimeout

# ==========================
# Telegram & Storage Settings
# ==========================
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = "@channelboottest"
POSTED_FILE = "posted_courses_discudemy.txt"

# Bot behaviour
MAX_LISTING_PAGES = 5       # how many pages of /all to scrape (each has ~20 courses)
CONCURRENCY = 3             # parallel /go page fetches
HEADLESS = True

def load_posted_links() -> Set[str]:
    if not os.path.exists(POSTED_FILE):
        return set()
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}

def save_posted_link(link: str):
    with open(POSTED_FILE, "a", encoding="utf-8") as f:
        f.write(link + "\n")

def clean_udemy_url(url: str) -> str:
    """Keep only the couponCode parameter, discard tracking garbage."""
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
# Scraper logic (adapted from the working script)
# ==========================
async def scrape_listing_page(page, page_num: int) -> list:
    """
    Scrapes one listing page (/all or /all/2, etc.)
    Returns list of course stubs with detail_url and go_url.
    """
    url = "https://couponami.com/all" if page_num == 1 else f"https://couponami.com/all/{page_num}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)
    except PwTimeout:
        print(f"  ⚠️ Timeout on listing page {page_num}")
        return []

    anchors = await page.query_selector_all("a[href]")
    courses = []
    seen_slugs = set()

    for anchor in anchors:
        href = await anchor.get_attribute("href") or ""
        match = re.match(r"https?://(?:www\.)?couponami\.com/([^/]+)/([^/?#]+)$", href)
        if not match:
            continue
        category, slug = match.group(1), match.group(2)
        skip = {"go", "category", "language", "all", "search", "contact", "about"}
        if category in skip:
            continue
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        title = (await anchor.inner_text()).strip()
        if not title or len(title) < 5:
            continue

        # Try to extract price text from parent container (e.g., "$84->$0")
        price_text = ""
        container = await anchor.evaluate_handle("el => el.closest('div, li, section, article') || el.parentElement")
        if container:
            raw = await container.inner_text()
            pm = re.search(r"\$[\d,]+\s*->\s*\$[\d,]+", raw)
            if pm:
                price_text = pm.group(0)

        courses.append({
            "title": title,
            "category": category,
            "slug": slug,
            "detail_url": href,
            "go_url": f"https://couponami.com/go/{slug}",
            "price_text": price_text,
        })
    return courses

async def fetch_udemy_from_go_page(context, course: dict):
    """
    Visits /go/{slug}, extracts the final Udemy URL and checks for expiration.
    Returns the course dict with udemy_url and is_expired, or None if no link.
    """
    page = await context.new_page()
    try:
        await page.goto(course["go_url"], wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(1200)

        body = await page.inner_text("body")
        is_expired = "expired" in body.lower()

        udemy_anchor = await page.query_selector("a[href*='udemy.com/course']")
        if not udemy_anchor:
            return None

        udemy_url = await udemy_anchor.get_attribute("href") or ""
        coupon_code = re.search(r"couponCode=([A-Z0-9]+)", udemy_url, re.IGNORECASE)
        return {
            **course,
            "udemy_url": clean_udemy_url(udemy_url),
            "coupon_code": coupon_code.group(1) if coupon_code else "",
            "is_expired": is_expired,
        }
    except Exception as e:
        print(f"  ⚠️ Error on {course['slug']}: {e}")
        return None
    finally:
        await page.close()

# ==========================
# Price validation on Udemy (double-check)
# ==========================
async def is_course_still_free(validation_page, udemy_url: str) -> bool:
    try:
        print(f"   🔍 Validating: {udemy_url[:80]}...")
        await validation_page.goto(udemy_url, wait_until="domcontentloaded", timeout=45000)
        
        # 1. Handle Cookie Consent (more comprehensive)
        try:
            accept_btn = await validation_page.query_selector("button:has-text('Accept all'), button:has-text('Accept'), button:has-text('OK')")
            if accept_btn:
                await accept_btn.click()
                await asyncio.sleep(2)
        except:
            pass
        
        # 2. Wait for the price element to appear (longer timeout)
        try:
            await validation_page.wait_for_selector(".price-text--price-part, [data-purpose='lead-price'], .price-text__current", timeout=30000)
        except Exception as e:
            print(f"   ⚠️ Price element not found after waiting: {e}")
            return False

        # 3. Try multiple selectors for the price
        price_selectors = [
            "[data-purpose='lead-price']",
            ".price-text--price-part",
            ".price-text__current",
            ".price-text",
            ".ud-component--course-price--price-part"
        ]
        
        price_text = None
        for selector in price_selectors:
            element = await validation_page.query_selector(selector)
            if element:
                price_text = await element.inner_text()
                if price_text:
                    price_text = price_text.strip().lower()
                    break
        
        # 4. If no price text found, check the whole body (final fallback)
        if not price_text:
            body = await validation_page.inner_text("body")
            body_lower = body.lower()
            if "free" in body_lower or "100% off" in body_lower or "$0" in body_lower or "₹0" in body_lower:
                return True
            return False
        
        # 5. Evaluate the price text
        if price_text in ("free", "$0", "€0", "₹0", "0", "0.00"):
            return True
        if "100% off" in price_text:
            return True
        if "free" in price_text:
            return True
        
        # 6. If the price contains a positive amount, consider it paid
        if re.search(r'[$€₹]\s*\d+', price_text):
            return False
        if re.search(r'\d+% off', price_text) and "100%" not in price_text:
            return False
        
        return False
        
    except Exception as e:
        print(f"   ⚠️ Udemy validation error: {e}")
        return False

# ==========================
# Send to Telegram
# ==========================
async def send_telegram(udemy_url: str, title: str) -> bool:
    message = f"🎓 FREE UDEMY COURSE\n\n📘 {title}\n\n🔗 {udemy_url}\n\n⚠️ Coupon may expire soon – enroll quickly!\n\n👇 More: https://t.me/CouponXpert"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = await asyncio.to_thread(requests.post, url, data={"chat_id": CHANNEL, "text": message}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"❌ Telegram send failed: {e}")
        return False

# ==========================
# Main
# ==========================
async def main():
    print("🚀 CouponAmi Bot (full scraper) Started")
    posted = load_posted_links()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        # Context for listing pages
        list_ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1366, "height": 768}
        )
        listing_page = await list_ctx.new_page()

        # Step 1: collect course stubs from listing pages
        all_stubs = []
        for page_num in range(1, MAX_LISTING_PAGES + 1):
            print(f"📄 Listing page {page_num} ...", end=" ", flush=True)
            stubs = await scrape_listing_page(listing_page, page_num)
            if not stubs:
                print("no courses – stopping")
                break
            all_stubs.extend(stubs)
            print(f"{len(stubs)} courses (total {len(all_stubs)})")
            await asyncio.sleep(1)

        await listing_page.close()
        print(f"\n✅ Collected {len(all_stubs)} course stubs")

        # Step 2: fetch Udemy URLs from /go pages (with concurrency)
        detail_ctx = await browser.new_context()
        sem = asyncio.Semaphore(CONCURRENCY)
        async def limited_fetch(course):
            async with sem:
                return await fetch_udemy_from_go_page(detail_ctx, course)

        tasks = [limited_fetch(c) for c in all_stubs]
        results = await asyncio.gather(*tasks)

        valid_courses = [r for r in results if r and not r.get("is_expired") and r.get("udemy_url")]
        print(f"🎯 Active (non-expired) courses: {len(valid_courses)}")

        # Step 3: validate price on Udemy and post
        validation_page = await detail_ctx.new_page()
        new_posts = 0
        MAX_NEW = 3

        for course in valid_courses:
            if new_posts >= MAX_NEW:
                break
            if course["udemy_url"] in posted:
                print(f"⏩ Already posted: {course['udemy_url']}")
                continue

            if await is_course_still_free(validation_page, course["udemy_url"]):
                title = course["title"]
                if await send_telegram(course["udemy_url"], title):
                    save_posted_link(course["udemy_url"])
                    posted.add(course["udemy_url"])
                    new_posts += 1
                    print(f"✅ Posted #{new_posts}: {title}")
                else:
                    print(f"❌ Telegram send failed for {title}")
            else:
                print(f"⏭️ Not free (Udemy says paid): {course['udemy_url']}")

        await browser.close()

    print(f"\n🎉 Done. Posted {new_posts} new courses.")

if __name__ == "__main__":
    asyncio.run(main())
