import asyncio
import os
import re
import requests
from typing import Set, List
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, urljoin
from playwright.async_api import async_playwright

# ==========================
# Telegram & Storage Settings
# ==========================
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = "@channelboottest"
POSTED_FILE = "posted_courses_discudemy.txt"

def load_posted_links() -> Set[str]:
    if not os.path.exists(POSTED_FILE):
        return set()
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}

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
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment
    ))

# ==========================
# Step 1: Extract all course page URLs from the listing page (simpler)
# ==========================
async def get_course_page_links(page, base_listing_url: str, max_pages: int = 5) -> List[str]:
    """
    Scrapes CouponAmi listing page(s) for course URLs.
    A course URL is any internal link that is NOT /all, /go/, /category, or external.
    """
    course_page_urls = set()
    current_url = base_listing_url
    pages_processed = 0

    while pages_processed < max_pages:
        print(f"📄 Crawling listing page: {current_url}")
        try:
            await page.goto(current_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=30000)
            await asyncio.sleep(1.5)  # let any lazy content settle

            # Get all <a> tags
            links = await page.eval_on_selector_all(
                "a",
                """elements => elements
                    .map(el => el.href)
                    .filter(href => {
                        if (!href) return false;
                        const url = new URL(href, 'https://couponami.com');
                        // Must be same domain
                        if (url.hostname !== 'couponami.com') return false;
                        const path = url.pathname;
                        // Exclude non-course pages
                        if (path === '/' ||
                            path === '/all' ||
                            path.startsWith('/go/') ||
                            path.startsWith('/category') ||
                            path.startsWith('/page/') ||
                            path.includes('?') ||
                            path.includes('#')) return false;
                        // Must have at least two slashes (e.g., /category/slug)
                        return path.split('/').length >= 3;
                    })
                    .map(href => href)"""
            )
            course_page_urls.update(links)
            print(f"   Found {len(links)} course links on this page (total: {len(course_page_urls)})")

            # Look for "Next" button (pagination)
            next_btn = await page.query_selector("a:has-text('Next')")
            if not next_btn:
                print("   No 'Next' button found – stopping.")
                break

            # Get the href of the next button
            next_url = await next_btn.get_attribute("href")
            if not next_url:
                break
            next_url = urljoin("https://couponami.com", next_url)
            if next_url == current_url:
                break
            current_url = next_url
            pages_processed += 1
            await asyncio.sleep(1)

        except Exception as e:
            print(f"   ⚠️ Error on page: {e}")
            break

    return list(course_page_urls)

# ==========================
# Step 2: From a course page, extract the "/go/" URL (the "Take Course" button)
# ==========================
async def get_go_url_from_course_page(page, course_page_url: str) -> str | None:
    try:
        await page.goto(course_page_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_load_state("networkidle", timeout=20000)
        await asyncio.sleep(1)

        take_btn = await page.query_selector("a:has-text('Take Course')")
        if not take_btn:
            take_btn = await page.query_selector("button:has-text('Take Course')")
        if take_btn:
            go_url = await take_btn.get_attribute("href")
            if go_url and go_url.startswith("https://couponami.com/go/"):
                return go_url
    except Exception as e:
        print(f"   ⚠️ Could not extract go URL: {e}")
    return None

# ==========================
# Step 3: From a "/go/" page, extract the final Udemy coupon URL
# ==========================
async def get_udemy_from_go_page(page, go_url: str) -> str | None:
    try:
        await page.goto(go_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_load_state("networkidle", timeout=30000)
        await asyncio.sleep(1)

        udemy_btn = await page.query_selector("a[href*='udemy.com/course/']")
        if udemy_btn:
            udemy_url = await udemy_btn.get_attribute("href")
            if udemy_url and "udemy.com/course/" in udemy_url:
                return clean_udemy_url(udemy_url)
    except Exception as e:
        print(f"   ⚠️ Could not extract Udemy URL: {e}")
    return None

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

        # Step 1: Get all course page URLs from the listing page
        listing_url = "https://couponami.com/all"
        course_pages = await get_course_page_links(page, listing_url, max_pages=5)
        print(f"\n✅ Found {len(course_pages)} course pages.")

        # Step 2-4: Process each course page
        new_posts = 0
        MAX_NEW = 3
        processed_udemy_links = set()

        for course_page in course_pages:
            if new_posts >= MAX_NEW:
                break

            go_url = await get_go_url_from_course_page(page, course_page)
            if not go_url:
                continue

            udemy_url = await get_udemy_from_go_page(page, go_url)
            if not udemy_url:
                continue

            if udemy_url in processed_udemy_links or udemy_url in posted:
                if udemy_url in posted:
                    print(f"⏩ Already posted: {udemy_url}")
                continue

            processed_udemy_links.add(udemy_url)

            if await is_course_still_free(page, udemy_url):
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
