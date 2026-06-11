"""
couponami.com Free Course Scraper
==================================
Scrapes 100% FREE courses from https://couponami.com using Playwright.

SETUP (run once):
    pip install playwright asyncio
    playwright install chromium

RUN:
    python couponami_scraper.py
"""

import asyncio
import json
import csv
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout


# ─── CONFIG ──────────────────────────────────────────────────────────────────
BASE_URL       = "https://couponami.com"
MAX_PAGES      = 10          # how many pages to scrape (set None for all)
HEADLESS       = True        # False = watch the browser live
SLOW_MO        = 100         # ms delay between actions (helps bypass bot checks)
OUTPUT_JSON    = "free_courses.json"
OUTPUT_CSV     = "free_courses.csv"
# ─────────────────────────────────────────────────────────────────────────────


async def scrape_couponami():
    all_courses = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            slow_mo=SLOW_MO,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="en-US",
        )

        # Stealth: remove 'webdriver' flag
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        page = await context.new_page()
        page_num = 1

        print(f"🚀 Starting scraper on {BASE_URL}\n")

        while True:
            if MAX_PAGES and page_num > MAX_PAGES:
                print(f"✅ Reached max page limit ({MAX_PAGES}). Stopping.")
                break

            url = BASE_URL if page_num == 1 else f"{BASE_URL}/page/{page_num}/"
            print(f"📄 Scraping page {page_num}: {url}")

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(2000)   # let JS settle
            except PlaywrightTimeout:
                print(f"  ⚠️  Timeout on page {page_num}. Stopping.")
                break

            # ── Locate course cards ──────────────────────────────────────────
            # Couponami uses article cards; adjust selector if the site changes
            cards = await page.query_selector_all("article.post, .course-card, .post-item, .entry")

            if not cards:
                # Fallback: try generic article selector
                cards = await page.query_selector_all("article")

            if not cards:
                print(f"  ℹ️  No course cards found on page {page_num}. End of pages.")
                break

            page_courses = []

            for card in cards:
                try:
                    course = await extract_course(card, page)
                    if course:
                        # ── FILTER: only 100% free ───────────────────────────
                        if is_free(course):
                            page_courses.append(course)
                except Exception as e:
                    print(f"  ⚠️  Error parsing a card: {e}")
                    continue

            print(f"  ✅ Found {len(page_courses)} free course(s) on page {page_num}")
            all_courses.extend(page_courses)

            # ── Check for "Next" button ──────────────────────────────────────
            next_btn = await page.query_selector(
                "a.next, a[rel='next'], .pagination .next, "
                "nav.navigation a:has-text('Next'), "
                ".nav-links a.next"
            )
            if not next_btn:
                print(f"\n🏁 No more pages after page {page_num}.")
                break

            page_num += 1

        await browser.close()

    return all_courses


async def extract_course(card, page) -> dict | None:
    """Extract course data from a single card element."""

    # Title
    title_el = await card.query_selector("h2, h3, .entry-title, .post-title")
    title = (await title_el.inner_text()).strip() if title_el else "N/A"

    # Link
    link_el = await card.query_selector("a[href]")
    link = await link_el.get_attribute("href") if link_el else "N/A"
    if link and link.startswith("/"):
        link = BASE_URL + link

    # Thumbnail
    img_el = await card.query_selector("img")
    image = await img_el.get_attribute("src") if img_el else "N/A"

    # Category / tags
    cat_el = await card.query_selector(".cat-links a, .category a, .tag a")
    category = (await cat_el.inner_text()).strip() if cat_el else "N/A"

    # Price / coupon text
    price_el = await card.query_selector(
        ".price, .coupon-price, .free-badge, "
        "[class*='free'], [class*='price']"
    )
    price_text = (await price_el.inner_text()).strip() if price_el else ""

    # Date
    date_el = await card.query_selector("time, .entry-date, .post-date")
    date = ""
    if date_el:
        date = await date_el.get_attribute("datetime") or (await date_el.inner_text()).strip()

    # Description excerpt
    desc_el = await card.query_selector(".entry-summary, .excerpt, p")
    description = (await desc_el.inner_text()).strip() if desc_el else ""

    if title == "N/A" and link == "N/A":
        return None

    return {
        "title":       title,
        "link":        link,
        "category":    category,
        "price_text":  price_text,
        "image":       image,
        "date":        date,
        "description": description[:300],   # trim long descriptions
    }


def is_free(course: dict) -> bool:
    """Return True only if the course appears to be 100% free."""
    text = (
        course.get("title", "") + " " +
        course.get("price_text", "") + " " +
        course.get("description", "")
    ).lower()

    free_signals   = ["free", "100% off", "100% free", "$0", "£0", "coupon"]
    paid_signals   = ["$", "£", "€", "paid", "discount"]

    has_free = any(sig in text for sig in free_signals)
    has_paid = any(sig in text for sig in paid_signals)

    # Free if it has a free signal AND no conflicting paid price
    return has_free and not has_paid


async def visit_course_page(link: str, context) -> dict:
    """
    (Optional) Visit the individual course page to grab the Udemy coupon URL.
    Call this if you want the direct Udemy link with coupon applied.
    """
    page = await context.new_page()
    udemy_url = ""
    try:
        await page.goto(link, wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_timeout(1500)

        # Look for 'Get Coupon' / 'Enroll Now' button that links to Udemy
        btn = await page.query_selector(
            "a[href*='udemy.com'], "
            "a:has-text('Get Coupon'), "
            "a:has-text('Enroll'), "
            "a:has-text('Get Course')"
        )
        if btn:
            udemy_url = await btn.get_attribute("href") or ""
    except Exception:
        pass
    finally:
        await page.close()
    return {"udemy_url": udemy_url}


def save_results(courses: list):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── JSON ──────────────────────────────────────────────────────────────────
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(
            {"scraped_at": timestamp, "total": len(courses), "courses": courses},
            f, indent=2, ensure_ascii=False
        )
    print(f"\n💾 JSON saved → {OUTPUT_JSON}")

    # ── CSV ───────────────────────────────────────────────────────────────────
    if courses:
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=courses[0].keys())
            writer.writeheader()
            writer.writerows(courses)
        print(f"💾 CSV  saved → {OUTPUT_CSV}")


def print_summary(courses: list):
    print("\n" + "═" * 60)
    print(f"  🎓 TOTAL FREE COURSES FOUND: {len(courses)}")
    print("═" * 60)
    for i, c in enumerate(courses[:10], 1):   # preview first 10
        print(f"\n  [{i}] {c['title']}")
        print(f"      🔗 {c['link']}")
        print(f"      📂 {c['category']}  |  📅 {c['date']}")
    if len(courses) > 10:
        print(f"\n  … and {len(courses) - 10} more (see {OUTPUT_JSON})")


if __name__ == "__main__":
    courses = asyncio.run(scrape_couponami())
    save_results(courses)
    print_summary(courses)
