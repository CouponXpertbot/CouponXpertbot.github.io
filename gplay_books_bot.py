import os
import re
import time
import requests
from typing import Optional, List, Dict
from serpapi import GoogleSearch
import google.generativeai as genai

# ==========================
# Telegram & Storage
# ==========================
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["CHANNEL_ID"]
POSTED_FILE = "posted_books_gplay.txt"

# API Keys
SERPAPI_KEY = os.environ["SERPAPI_API_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# ==========================
# Gemini Configuration
# ==========================
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# ==========================
# Settings
# ==========================
GL = "us"
HL = "en"
MAX_PAGES = 3
CATEGORIES = []  # e.g., ["books_computers", "books_business"]
DELAY_BETWEEN_CHECKS = 1.0
MAX_NEW_POSTS = 3

# ==========================
# Helpers
# ==========================
def load_posted_books() -> set:
    if not os.path.exists(POSTED_FILE):
        return set()
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}

def save_posted_book(product_id: str):
    with open(POSTED_FILE, "a", encoding="utf-8") as f:
        f.write(product_id + "\n")

# ==========================
# Gemini Message Generator
# ==========================
def generate_ai_book_message(
    title: str,
    author: str,
    description: str,
    original_price: float,
    link: str,
    categories: List[str] = None
) -> str:
    """
    Uses Gemini to craft a formatted Telegram message like the example.
    """
    # If no description, provide a placeholder
    if not description or len(description) < 20:
        description = f"Discover '{title}' – now available for free on Google Play Books."

    # Build prompt
    categories_str = ", ".join(categories[:3]) if categories else "Various"
    prompt = f"""
You are a book enthusiast writing a Telegram post to announce that a book has become FREE on Google Play Books.

Write a short, engaging message (max 250 characters) using the format below. Use emojis where appropriate.

Book details:
- Title: {title}
- Author: {author or 'Unknown'}
- Description: {description[:200]}...
- Original price: ₹{original_price:.2f} → now FREE
- Link: {link}
- Categories: {categories_str}

Format requirements:
- Start with "🔍 FREE GOOGLE PLAY BOOK"
- Next line: "📘 {Title}" (with emoji)
- Next: "✍️ {Author}" (if available)
- Next: a one‑sentence summary of the description
- Next: "🎯 Perfect For:" followed by 3‑4 tags (e.g., "✔ Psychological Thriller Fans", "✔ Mystery Lovers", etc.) derived from categories/description
- Next: "🚀 Packed with..." (a short catchy line)
- End with "🔗 Read Now:" and the link (the link should be on its own line).

Do NOT include any price or availability warnings – I will add those later.
Do NOT include the channel promo – I will add that later.

Write only the message content, no extra commentary.
"""

    try:
        response = model.generate_content(prompt)
        ai_text = response.text.strip()
        # Ensure the link is present
        if link not in ai_text:
            ai_text += f"\n\n🔗 Read Now:\n{link}"
        return ai_text
    except Exception as e:
        print(f"⚠️ Gemini generation failed: {e}. Using fallback.")
        return f"""🔍 FREE GOOGLE PLAY BOOK

📘 {title}
✍️ {author or 'Unknown'}

{description[:100]}...

🔗 Read Now:
{link}"""

# ==========================
# Telegram Sender (with Gemini)
# ==========================
def send_telegram_message(
    title: str,
    author: str,
    description: str,
    original_price: float,
    link: str,
    categories: List[str] = None
) -> bool:
    # Generate the core message using Gemini
    core_message = generate_ai_book_message(
        title, author, description, original_price, link, categories
    )

    # Static footer (as in your example)
    footer = f"""

⚠️ Price & availability may vary by account/region. Offer may end anytime.

👇 Join for daily FREE books & courses
👉 https://t.me/CouponXpert"""

    full_message = core_message + footer

    # Send to Telegram
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": CHANNEL, "text": full_message}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"❌ Telegram send failed: {e}")
        return False

# ==========================
# SerpApi functions (unchanged from working version)
# ==========================
def get_search(params):
    search = GoogleSearch(params)
    return search.get_dict()

def fetch_free_chart_candidates(gl: str, hl: str, max_pages: int):
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

# ==========================
# Main
# ==========================
def main():
    print("🚀 Google Play Books Price-Drop Bot (with Gemini) Started")
    posted = load_posted_books()

    # 1. Collect candidates
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

            # Extract additional data
            author = info.get("authors")
            if author and isinstance(author, list):
                author = ", ".join([a.get("name", "") for a in author if a.get("name")])
            else:
                author = meta.get("author") or "Unknown"

            description = info.get("description", "")
            categories = info.get("categories", [])  # list of category names

            link = meta.get("link") or f"https://play.google.com/store/books/details?id={pid}"

            # Send with Gemini-generated message
            if send_telegram_message(title, author, description, orig, link, categories):
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
