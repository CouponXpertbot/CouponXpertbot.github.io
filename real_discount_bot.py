import requests
from bs4 import BeautifulSoup
import re
import os
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from playwright.sync_api import sync_playwright

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = "@Channelboottest"
POSTED_FILE = "posted_courses.txt"

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

def get_html(url: str) -> str:
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text

def scrape_real_discount() -> list:
    udemy_urls = []
    try:
        url = "https://real.discount/"
        print("🌐 Scraping Real.Discount")
        soup = BeautifulSoup(get_html(url), "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "udemy.com/course/" in href and "couponCode" in href:
                udemy_urls.append(clean_udemy_url(href))
        if not udemy_urls:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/category/" in href and href.startswith("https://real.discount"):
                    cat_soup = BeautifulSoup(get_html(href), "html.parser")
                    for ca in cat_soup.find_all("a", href=True):
                        ch = ca["href"]
                        if "udemy.com/course/" in ch and "couponCode" in ch:
                            udemy_urls.append(clean_udemy_url(ch))
        print(f"   Found {len(udemy_urls)} Udemy links")
    except Exception as e:
        print(f"⚠️ Real.Discount error: {e}")
    return list(dict.fromkeys(udemy_urls))

def is_course_truly_free(page, udemy_url: str) -> bool:
    try:
        print(f"🔍 Validating: {udemy_url}")
        page.goto(udemy_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_load_state("networkidle")
        price_selectors = ["[data-purpose='price-text']", ".price-text", ".ud-component--course-price--price-part"]
        price_text = None
        for sel in price_selectors:
            el = page.query_selector(sel)
            if el:
                price_text = el.inner_text().strip().lower()
                break
        if not price_text:
            body = page.inner_text("body").lower()
            return "free" in body and not re.search(r'₹\d{2,}', body)
        price_text = price_text.lower()
        if price_text in ["free", "₹0", "$0", "€0", "0", "0.00"] or "100% off" in price_text:
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
    print("🔍 Bot (Real.Discount) started")
    posted = load_posted_links()
    udemy_links = scrape_real_discount()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        new_posts = 0
        for url in udemy_links:
            if new_posts >= 3: break
            if url in posted: continue
            if is_course_truly_free(page, url):
                title = url.split('/course/')[1].split('/')[0].replace('-', ' ').title()
                if send_telegram_message(url, title):
                    save_posted_link(url)
                    posted.add(url)
                    new_posts += 1
                    print(f"✅ Posted #{new_posts}")
            else:
                print(f"⏭️ Expired/paid: {url}")
        browser.close()
    print(f"Done. Posted {new_posts}")

if __name__ == "__main__":
    main()
