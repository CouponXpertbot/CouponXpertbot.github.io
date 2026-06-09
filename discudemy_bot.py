import asyncio
import os
import re
import requests
from typing import Set, List
from playwright.async_api import async_playwright

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

async def get_udemy_from_go_page(page, go_url: str) -> str | None:
    """
    Visit the intermediate /go/ page, click the 'CLICK HERE TO REDEEM' button,
    and return the final Udemy URL.
    """
    try:
        print(f"   🔗 Following: {go_url}")
        await page.goto(go_url, wait_until="domcontentloaded", timeout=30000)
        # Wait for the redeem button (some pages have a countdown)
        await page.wait_for_selector("a:has-text('CLICK HERE TO REDEEM')", timeout=15000)
        redeem_btn = await page.query_selector("a:has-text('CLICK HERE TO REDEEM')")
        if redeem_btn:
            udemy_url = await redeem_btn.get_attribute("href")
            if udemy_url and "udemy.com/course/" in udemy_url:
                return udemy_url
        return None
    except Exception as e:
        print(f"   ⚠️ Failed to extract from {go_url}: {e}")
        return None

async def scrape_discudemy_listing(page, start_url: str, max_pages: int = 3) -> List[str]:
    """
    Scrape listing pages (e.g., /all or /free-udemy-courses) to get all 'GET COUPON' links.
    Then follow each to get the final Udemy URL.
    """
    all_udemy = set()
    for page_num in range(1, max_pages + 1):
        url = start_url if page_num == 1 else f"{start_url}?page={page_num}"
        print(f"📄 Crawling: {url}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_selector(".card", timeout=10000)
            
            # Find all 'GET COUPON' links
            coupon_links = await page.eval_on_selector_all(
                "a:has-text('GET COUPON')",
                "elements => elements.map(el => el.href)"
            )
            print(f"   Found {len(coupon_links)} coupon links on page {page_num}")
            
            for go_link in coupon_links:
                udemy = await get_udemy_from_go_page(page, go_link)
                if udemy:
                    all_udemy.add(udemy)
            await asyncio.sleep(1.5)
        except Exception as e:
            print(f"   ⚠️ Error on page {page_num}: {e}")
            break
    return list(all_udemy)

async def is_course_free(page, udemy_url: str) -> bool:
    try:
        await page.goto(udemy_url, wait_until="domcontentloaded", timeout=30000)
        price_el = await page.query_selector("[data-purpose='lead-price']")
        if price_el:
            price_text = (await price_el.inner_text()).strip().lower()
            return price_text in ("free", "$0", "€0", "₹0", "0", "0.00")
        body = await page.inner_text("body")
        return "free" in body.lower() and "100% off" in body.lower()
    except Exception:
        return False

async def send_telegram(udemy_url: str, title: str) -> bool:
    message = f"🎓 FREE UDEMY COURSE\n\n📘 {title}\n\n🔗 {udemy_url}\n\n⚠️ Coupon may expire soon!\n\n👇 More: https://t.me/CouponXpert"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = await asyncio.to_thread(requests.post, url, data={"chat_id": CHANNEL, "text": message}, timeout=10)
        return r.status_code == 200
    except Exception:
        return False

async def main():
    print("🚀 Discudemy Bot (Corrected) Started")
    posted = load_posted_links()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = await context.new_page()

        # Two main sections on Discudemy
        start_urls = [
            "https://www.discudemy.com/all",
            "https://www.discudemy.com/free-udemy-courses"
        ]
        all_udemy = []
        for start in start_urls:
            links = await scrape_discudemy_listing(page, start, max_pages=3)
            all_udemy.extend(links)

        all_udemy = list(set(all_udemy))
        print(f"\n✅ Total unique Udemy links found: {len(all_udemy)}")

        new_posts = 0
        MAX_NEW = 3
        for udemy in all_udemy:
            if new_posts >= MAX_NEW:
                break
            if udemy in posted:
                print(f"⏩ Already posted: {udemy}")
                continue
            if await is_course_free(page, udemy):
                title = udemy.split('/course/')[1].split('/')[0].replace('-', ' ').title()
                if await send_telegram(udemy, title):
                    save_posted_link(udemy)
                    posted.add(udemy)
                    new_posts += 1
                    print(f"✅ Posted #{new_posts}: {title}")
                else:
                    print(f"❌ Telegram failed for {title}")
            else:
                print(f"⏭️ Not free: {udemy}")

        await browser.close()
    print(f"🎉 Done. Posted {new_posts} new courses.")

if __name__ == "__main__":
    asyncio.run(main())
