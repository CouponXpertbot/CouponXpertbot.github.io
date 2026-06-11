import asyncio
import os
import re
import requests
from typing import Set, List
from playwright.async_api import async_playwright

# ==========================
# Telegram & Storage Settings
# ==========================
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = "@channelboottest"          # e.g., "@Channelboottest"
POSTED_FILE = "posted_courses_discudemy.txt"

def load_posted_links() -> Set[str]:
    if not os.path.exists(POSTED_FILE):
        return set()
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}

def save_posted_link(link: str):
    with open(POSTED_FILE, "a", encoding="utf-8") as f:
        f.write(link + "\n")

# ==========================
# Step 1: Extract course page URLs from the listing page
# ==========================
async def get_course_page_links(page, base_listing_url: str, max_pages: int = 3) -> List[str]:
    """
    Scrapes the CouponAmi listing page(s) and returns a list of course page URLs.
    """
    course_page_urls = set()
    page_num = 1

    while page_num <= max_pages:
        url = base_listing_url if page_num == 1 else f"{base_listing_url}?page={page_num}"
        print(f"📄 Crawling listing page: {url}")

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=30000)

            # Find all links that point to a course page (e.g., /academic/... or /business/...)
            links = await page.eval_on_selector_all(
                "a[href^='/']",
                "elements => elements.map(el => el.href).filter(href => href.includes('/academic/') || href.includes('/business/') || href.includes('/design/') || href.includes('/development/') || href.includes('/health/') || href.includes('/it/') || href.includes('/marketing/') || href.includes('/music/') || href.includes('/photography/') || href.includes('/teaching/'))"
            )
            course_page_urls.update(links)
            print(f"   Found {len(links)} course links on page {page_num} (total: {len(course_page_urls)})")
            page_num += 1
            await asyncio.sleep(1.5)
        except Exception as e:
            print(f"   ⚠️ Error on page {page_num}: {e}")
            break

    return list(course_page_urls)

# ==========================
# Step 2: From a course page, extract the "/go/" URL (the "Take Course" button)
# ==========================
async def get_go_url_from_course_page(page, course_page_url: str) -> str | None:
    try:
        await page.goto(course_page_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_selector("a:has-text('Take Course')", timeout=10000)
        go_button = await page.query_selector("a:has-text('Take Course')")
        if go_button:
            go_url = await go_button.get_attribute("href")
            if go_url and go_url.startswith("https://www.couponami.com/go/"):
                return go_url
    except Exception as e:
        print(f"   ⚠️ Could not extract go URL from {course_page_url}: {e}")
    return None

# ==========================
# Step 3: From a "/go/" page, extract the final Udemy coupon URL
# ==========================
async def get_udemy_from_go_page(page, go_url: str) -> str | None:
    try:
        await page.goto(go_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_load_state("networkidle", timeout=30000)

        # Look for the "Get course" button that contains the Udemy link
        udemy_button = await page.query_selector("a[href*='udemy.com/course/']")
        if udemy_button:
            udemy_url = await udemy_button.get_attribute("href")
            if udemy_url and "udemy.com/course/" in udemy_url:
                # Keep only the coupon code parameter, remove tracking garbage
                return clean_udemy_url(udemy_url)
    except Exception as e:
        print(f"   ⚠️ Could not extract Udemy URL from {go_url}: {e}")
    return None

def clean_udemy_url(url: str) -> str:
    """Keep only the couponCode parameter."""
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
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
# Step 4: Validate the Udemy page is still free
# ==========================
async def is_course_still_free(page, udemy_url: str) -> bool:
    try:
        await page.goto(udemy_url, wait_until="domcontentloaded", timeout=30000)
        price_el = await page.query_selector("[data-purpose='lead-price']")
        if price_el:
            price_text = (await price_el.inner_text()).strip().lower()
            return price_text in ("free", "$0", "€0", "₹0", "0", "0.00")
        body = await page.inner_text("body")
        return "free" in body.lower()
    except Exception as e:
        print(f"   ⚠️ Udemy validation error: {e}")
        return False

# ==========================
# Step 5: Telegram sender
# ==========================
async def send_telegram_message(udemy_link: str, title: str) -> bool:
    message = f"🎓 FREE UDEMY COURSE\n\n📘 {title}\n\n🔗 {udemy_link}\n\n⚠️ Coupon may expire soon – enroll quickly!\n\n👇 More: https://t.me/CouponXpert"
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
    print("🚀 CouponAmi Bot Started")
    posted = load_posted_links()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = await context.new_page()

        # 1. Get all course page URLs
        listing_url = "https://www.couponami.com/all"
        course_pages = await get_course_page_links(page, listing_url, max_pages=3)
        print(f"\n✅ Found {len(course_pages)} course pages.")

        # 2. Process each course page
        new_posts = 0
        MAX_NEW = 3
        udemy_links_processed = set()

        for course_page in course_pages:
            if new_posts >= MAX_NEW:
                break

            # Step 2: Get the "/go/" URL
            go_url = await get_go_url_from_course_page(page, course_page)
            if not go_url:
                continue

            # Step 3: Get the final Udemy URL from the "/go/" page
            udemy_url = await get_udemy_from_go_page(page, go_url)
            if not udemy_url:
                continue

            if udemy_url in udemy_links_processed:
                continue
            udemy_links_processed.add(udemy_url)

            if udemy_url in posted:
                print(f"⏩ Already posted: {udemy_url}")
                continue

            # Step 4: Validate the coupon on Udemy
            if await is_course_still_free(page, udemy_url):
                # Extract a readable title from the URL slug
                match = re.search(r'/course/([^/?]+)', udemy_url)
                title = match.group(1).replace('-', ' ').title() if match else "Udemy Course"
                if await send_telegram_message(udemy_url, title):
                    save_posted_link(udemy_url)
                    posted.add(udemy_url)
                    new_posts += 1
                    print(f"✅ Posted #{new_posts}: {title}")
                else:
                    print(f"❌ Telegram send failed for {title}")
            else:
                print(f"⏭️ Not free (or error): {udemy_url}")

        await browser.close()
    print(f"\n🎉 Done. Posted {new_posts} new courses.")

if __name__ == "__main__":
    asyncio.run(main())
