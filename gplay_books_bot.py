import os
import re
import time
import requests
from typing import Optional
from serpapi import GoogleSearch

# ==========================
# Telegram & Storage
# ==========================
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["CHANNEL_ID"]
POSTED_FILE = "posted_books_gplay.txt"

SERPAPI_KEY = os.environ["SERPAPI_API_KEY"]

# Settings
GL = "us"
HL = "en"
MAX_PAGES = 3
CATEGORIES = []  # e.g., ["books_computers", "books_business"]
DELAY_BETWEEN_CHECKS = 1.0

def load_posted_books() -> set:
    if not os.path.exists(POSTED_FILE):
        return set()
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}

def save_posted_book(product_id: str):
    with open(POSTED_FILE, "a", encoding="utf-8") as f:
        f.write(product_id + "\n")

def send_telegram_message(book_title: str, link: str, original_price: float) -> bool:
    message = (
        f"📘 *Book Price Drop!*\n\n"
        f"*{book_title}*\n"
        f"Was ₹{original_price:.2f} → Now *FREE*\n"
        f"🔗 {link}\n\n"
        f"Claim it before the price goes up!"
    )
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": CHANNEL, "text": message, "parse_mode": "Markdown"}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"❌ Telegram send failed: {e}")
        return False

def get_search(params):
    """Helper to run a SerpApi search and return the result dict."""
    search = GoogleSearch(params)
    return search.get_dict()

def fetch_free_chart_candidates(gl: str, hl: str, max_pages: int):
    """Pull product_ids from the Top Selling Free chart."""
    seen = {}
    params = {
        "engine": "google_play_books",
        "chart": "topselling_free",
        "gl": gl,
        "hl": hl,
        "api_key": SERPAPI_KEY,
    }
    page = 0
    while page < max_pages:
        page += 1
        result = get_search(params)
        items = result.get("top_charts") or result.get("organic_results") or []
        if not items:
            break
        for item in items:
            pid = item.get("product_id")
            if pid and pid not in seen:
                seen[pid] = {
                    "title": item.get("title"),
                    "author": item.get("author") or item.get("authors"),
                    "link": item.get("link"),
                    "thumbnail": item.get("thumbnail"),
                }
        pagination = result.get("serpapi_pagination") or {}
        next_token = pagination.get("next_page_token")
        if not next_token:
            break
        params["next_page_token"] = next_token
        time.sleep(1)
    return seen

def fetch_category_candidates(gl: str, hl: str, category: str, max_pages: int):
    """Pull product_ids from a specific Play Books category."""
    seen = {}
    params = {
        "engine": "google_play_books",
        "books_category": category,
        "gl": gl,
        "hl": hl,
        "api_key": SERPAPI_KEY,
    }
    page = 0
    while page < max_pages:
        page += 1
        result = get_search(params)
        items = result.get("organic_results") or result.get("category_results") or []
        if not items:
            break
        for item in items:
            pid = item.get("product_id")
            if pid and pid not in seen:
                seen[pid] = {
                    "title": item.get("title"),
                    "author": item.get("author") or item.get("authors"),
                    "link": item.get("link"),
                    "thumbnail": item.get("thumbnail"),
                }
        pagination = result.get("serpapi_pagination") or {}
        next_token = pagination.get("next_page_token")
        if not next_token:
            break
        params["next_page_token"] = next_token
        time.sleep(1)
    return seen

PRICE_RE = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)")

def parse_offer_for_drop(offer: dict):
    text = offer.get("text", "") or ""
    extracted_price = offer.get("price_extracted") or offer.get("extracted_price")
    prices_in_text = [float(p) for p in PRICE_RE.findall(text)]
    is_free_text = "free" in text.lower()

    current_price = extracted_price
    if current_price is None:
        current_price = 0.0 if is_free_text else (prices_in_text[-1] if prices_in_text else None)

    original_price = None
    if len(prices_in_text) >= 2:
        original_price = prices_in_text[0]
    elif len(prices_in_text) == 1 and is_free_text:
        original_price = prices_in_text[0]

    is_drop = bool(
        original_price
        and original_price > 0
        and (current_price == 0.0 or current_price is None and is_free_text)
    )
    return is_drop, original_price, (0.0 if is_free_text else current_price)

def check_product_for_drop(gl: str, hl: str, product_id: str):
    params = {
        "engine": "google_play_product",
        "product_id": product_id,
        "store": "books",
        "gl": gl,
        "hl": hl,
        "api_key": SERPAPI_KEY,
    }
    result = get_search(params)
    info = result.get("product_info", {}) or {}
    offers = info.get("offers", []) or []

    best = None
    for offer in offers:
        is_drop, orig, curr = parse_offer_for_drop(offer)
        if is_drop:
            best = (orig, curr, offer.get("text"))
            break
    return info, best

def main():
    print("🚀 Google Play Books Price-Drop Bot Started")
    posted = load_posted_books()

    # 1. Collect candidates from free chart + optional categories
    candidates = fetch_free_chart_candidates(GL, HL, MAX_PAGES)
    for cat in CATEGORIES:
        print(f"Sweeping category '{cat}'...")
        candidates.update(fetch_category_candidates(GL, HL, cat, MAX_PAGES))

    print(f"Collected {len(candidates)} unique candidate books.")

    new_posts = 0
    MAX_NEW = 3

    for i, (pid, meta) in enumerate(candidates.items(), 1):
        if new_posts >= MAX_NEW:
            break
        if pid in posted:
            print(f"  [{i}/{len(candidates)}] Already posted: {pid}")
            continue

        try:
            info, drop = check_product_for_drop(GL, HL, pid)
        except Exception as e:
            print(f"  [{i}/{len(candidates)}] ERROR on {pid}: {e}")
            continue

        title = info.get("title") or meta.get("title") or "Unknown"
        if drop:
            orig, curr, offer_text = drop
            print(f"  [{i}/{len(candidates)}] DROP FOUND: {title} (${orig:.2f} -> Free)")
            link = meta.get("link") or f"https://play.google.com/store/books/details?id={pid}"
            if send_telegram_message(title, link, orig):
                save_posted_book(pid)
                posted.add(pid)
                new_posts += 1
                print(f"   ✅ Posted #{new_posts}")
        else:
            print(f"  [{i}/{len(candidates)}] no drop: {title}")

        time.sleep(DELAY_BETWEEN_CHECKS)

    print(f"\n🎉 Done. Posted {new_posts} new price-drop books.")

if __name__ == "__main__":
    main()
