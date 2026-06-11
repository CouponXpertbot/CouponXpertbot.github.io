"""
couponami.com — Playwright Scraper for 100% Free Udemy Courses
==============================================================
Understands the EXACT 3-page flow of couponami.com:

  Page 1 → /all, /all/2, /all/3 ...  (listings, price shown as $X->$0)
  Page 2 → /{category}/{slug}         (course detail, checks if coupon active)
  Page 3 → /go/{slug}                 (final page with real Udemy coupon URL)

SETUP (one-time):
    pip install playwright
    playwright install chromium

RUN:
    python couponami_scraper.py
"""

import asyncio
import json
import csv
import re
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PwTimeout

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BASE_URL        = "https://www.couponami.com"
MAX_PAGES       = 5          # listing pages to scrape (total pages = 166; set None for all)
HEADLESS        = True       # set False to watch the browser
SLOW_MO         = 80         # ms between actions
CONCURRENCY     = 3          # parallel detail-page fetches
OUTPUT_JSON     = "free_courses.json"
OUTPUT_CSV      = "free_courses.csv"
# ──────────────────────────────────────────────────────────────────────────────

STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    window.chrome = { runtime: {} };
"""


async def make_context(browser):
    ctx = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1366, "height": 768},
        locale="en-US",
    )
    await ctx.add_init_script(STEALTH_SCRIPT)
    return ctx


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Scrape the listing pages  (/all, /all/2, /all/3 …)
# ═══════════════════════════════════════════════════════════════════════════════
async def scrape_listing_page(page, page_num: int) -> list[dict]:
    """Returns raw course stubs from one listing page."""
    url = f"{BASE_URL}/all" if page_num == 1 else f"{BASE_URL}/all/{page_num}"

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(1500)
    except PwTimeout:
        print(f"  ⚠️  Timeout on listing page {page_num}")
        return []

    # ── Parse all course anchor links on the page ─────────────────────────────
    # Course links look like:  https://www.couponami.com/{category}/{slug}
    # We exclude nav/footer/popular-sidebar links by requiring they have an
    # accompanying price string "$X->$0" nearby in the same container.

    # Grab all <a> tags that match the course URL pattern
    anchors = await page.query_selector_all("a[href]")

    courses = []
    seen_slugs = set()

    for anchor in anchors:
        href = await anchor.get_attribute("href") or ""

        # Must be an internal path with exactly 2 segments: /category/slug
        match = re.match(
            r"https?://(?:www\.)?couponami\.com/([^/]+)/([^/?#]+)$", href
        )
        if not match:
            continue

        category_slug = match.group(1)
        course_slug   = match.group(2)

        # Skip nav/meta pages
        SKIP_CATS = {"go", "category", "language", "all", "search",
                     "contact", "about", "review", "policies",
                     "frequently-asked-question", "feed"}
        if category_slug in SKIP_CATS:
            continue

        # Deduplicate
        if course_slug in seen_slugs:
            continue
        seen_slugs.add(course_slug)

        title = (await anchor.inner_text()).strip()
        if not title or len(title) < 5:
            continue

        # Try to read price from parent/sibling container
        price_text = ""
        try:
            # Walk up a few levels to find the price text "$X->$0"
            container = await anchor.evaluate_handle(
                "el => el.closest('div, li, section, article') || el.parentElement"
            )
            if container:
                raw = await container.inner_text()
                # Look for price pattern
                pm = re.search(r"\$[\d,]+\s*->\s*\$[\d,]+", raw)
                if pm:
                    price_text = pm.group(0)
        except Exception:
            pass

        # Build course stub
        course_detail_url = href  # page 2
        # page 3 URL: /go/{slug}
        go_url = f"{BASE_URL}/go/{course_slug}"

        courses.append({
            "title":        title,
            "category":     category_slug,
            "slug":         course_slug,
            "detail_url":   course_detail_url,
            "go_url":       go_url,
            "price_text":   price_text,
        })

    return courses


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Visit /go/{slug}  to get the real Udemy coupon URL
#           Also checks whether coupon is still active (not expired)
# ═══════════════════════════════════════════════════════════════════════════════
async def fetch_udemy_link(context, course: dict) -> dict | None:
    """
    Visits /go/{slug} and extracts the Udemy URL with coupon code.
    Returns None if coupon is expired.
    """
    page = await context.new_page()
    result = None

    try:
        await page.goto(course["go_url"], wait_until="domcontentloaded", timeout=25_000)
        await page.wait_for_timeout(1200)

        page_text = await page.inner_text("body")

        # Check for expired coupon
        if "expired" in page_text.lower():
            # Still try to get link — sometimes "expired" is shown but link works
            pass

        # Find the Udemy anchor link
        udemy_anchor = await page.query_selector("a[href*='udemy.com/course']")

        if udemy_anchor:
            udemy_url = await udemy_anchor.get_attribute("href") or ""
            link_text = (await udemy_anchor.inner_text()).strip()

            is_expired = "expired" in page_text.lower() and "expired" in page_text[:500].lower()

            result = {
                **course,
                "udemy_url":   udemy_url,
                "coupon_code": extract_coupon_code(udemy_url),
                "is_expired":  is_expired,
                "link_text":   link_text,
            }
        else:
            # Coupon page has no Udemy link → fully expired/removed
            pass

    except PwTimeout:
        print(f"  ⚠️  Timeout on /go/ page for: {course['slug'][:40]}")
    except Exception as e:
        print(f"  ⚠️  Error on {course['slug'][:40]}: {e}")
    finally:
        await page.close()

    return result


def extract_coupon_code(udemy_url: str) -> str:
    m = re.search(r"couponCode=([A-Z0-9]+)", udemy_url, re.IGNORECASE)
    return m.group(1) if m else ""


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════
async def main():
    all_stubs = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            slow_mo=SLOW_MO,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        listing_ctx = await make_context(browser)
        listing_page = await listing_ctx.new_page()

        # ── STEP 1: Collect all course stubs from listing pages ───────────────
        print(f"\n🔍  STEP 1 — Scraping listing pages ({BASE_URL}/all)")
        print("─" * 55)

        page_num = 1
        while True:
            if MAX_PAGES and page_num > MAX_PAGES:
                print(f"  ✅ Reached MAX_PAGES={MAX_PAGES} limit.")
                break

            print(f"  📄 Listing page {page_num} ...", end=" ", flush=True)
            stubs = await scrape_listing_page(listing_page, page_num)

            if not stubs:
                print("no courses found — stopping.")
                break

            all_stubs.extend(stubs)
            print(f"{len(stubs)} courses found  (total so far: {len(all_stubs)})")
            page_num += 1

        await listing_page.close()

        # ── STEP 2: Visit /go/{slug} for each course to get Udemy links ───────
        print(f"\n🔗  STEP 2 — Fetching Udemy coupon links ({len(all_stubs)} courses)")
        print("─" * 55)

        detail_ctx = await make_context(browser)
        final_courses = []
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def fetch_with_limit(course):
            async with semaphore:
                return await fetch_udemy_link(detail_ctx, course)

        tasks = [fetch_with_limit(s) for s in all_stubs]
        results = await asyncio.gather(*tasks)

        for r in results:
            if r and not r.get("is_expired") and r.get("udemy_url"):
                final_courses.append(r)

        print(f"\n  ✅ Active (non-expired) free courses: {len(final_courses)}")

        await browser.close()

    # ── STEP 3: Save ──────────────────────────────────────────────────────────
    save_results(final_courses)
    print_summary(final_courses)
    return final_courses


# ═══════════════════════════════════════════════════════════════════════════════
# OUTPUT HELPERS
# ═══════════════════════════════════════════════════════════════════════════════
def save_results(courses: list):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(
            {"scraped_at": timestamp, "total": len(courses), "courses": courses},
            f, indent=2, ensure_ascii=False,
        )
    print(f"\n💾 JSON  →  {OUTPUT_JSON}")

    if courses:
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=courses[0].keys())
            writer.writeheader()
            writer.writerows(courses)
        print(f"💾 CSV   →  {OUTPUT_CSV}")


def print_summary(courses: list):
    print("\n" + "═" * 65)
    print(f"  🎓  TOTAL FREE COURSES WITH ACTIVE COUPONS: {len(courses)}")
    print("═" * 65)
    for i, c in enumerate(courses[:15], 1):
        code = f"  🎟  Coupon: {c['coupon_code']}" if c.get("coupon_code") else ""
        print(f"\n  [{i:02d}] {c['title']}")
        print(f"       📂 {c['category']}   💰 {c.get('price_text','')}")
        print(f"       🔗 {c['udemy_url'][:80]}...")
        if code:
            print(f"       {code}")
    if len(courses) > 15:
        print(f"\n  … and {len(courses) - 15} more in {OUTPUT_JSON}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
