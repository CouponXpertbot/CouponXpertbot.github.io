import requests
from bs4 import BeautifulSoup
import re
import os
from typing import List, Optional

# ==========================
# Telegram Settings (from GitHub Secrets)
# ==========================
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = "@Channelboottest"     # e.g., "@Channelboottest"

# ==========================
# Persistent Storage
# ==========================
POSTED_FILE = "posted_courses.txt"

def load_posted_links() -> set:
    if not os.path.exists(POSTED_FILE):
        return set()
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_posted_link(link: str):
    with open(POSTED_FILE, "a", encoding="utf-8") as f:
        f.write(link + "\n")

# ==========================
# Robust HTTP client (avoid 406)
# ==========================
def get_html(url: str) -> Optional[str]:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
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
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"❌ Failed to fetch {url}: {e}")
        return None

# ==========================
# Extract Udemy link from coursefolder.net page
# ==========================
def extract_udemy_link(coursefolder_url: str) -> Optional[str]:
    html = get_html(coursefolder_url)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # 1. Look for "Get Free Coupon" button/link
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        if "get free coupon" in text:
            href = a["href"]
            if "udemy.com" in href:
                return href

    # 2. Fallback: any Udemy course link
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "udemy.com/course/" in href:
            return href

    # 3. Check JSON-LD structured data
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            import json
            data = json.loads(script.string)
            if isinstance(data, dict):
                # Typical offer structure
                if "offers" in data and isinstance(data["offers"], dict):
                    url = data["offers"].get("url")
                    if url and "udemy.com/course/" in url:
                        return url
                # Direct URL property
                if "url" in data and "udemy.com/course/" in data["url"]:
                    return data["url"]
        except:
            continue

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
            if re.match(r"https?://coursefolder\.net/", href):
                coursefolder_urls.append(href)

    # Remove duplicates
    return list(dict.fromkeys(coursefolder_urls))

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

    new_posts = 0
    MAX_NEW = 3   # post at most 3 new courses per run

    for cf_url in cf_links:
        if new_posts >= MAX_NEW:
            break

        udemy = extract_udemy_link(cf_url)
        if not udemy:
            print(f"⏭️ No Udemy link in {cf_url}")
            continue
        if udemy in posted:
            print(f"⏩ Already posted: {udemy}")
            continue

        # Generate a nice title from the URL slug
        slug = cf_url.rstrip('/').split('/')[-1]
        title = slug.replace('-', ' ').title()

        if send_telegram_message(udemy, title):
            save_posted_link(udemy)
            posted.add(udemy)
            new_posts += 1
            print(f"✅ Posted #{new_posts}: {title}")
        else:
            print(f"❌ Failed to send {title}")

    print(f"🎉 Done. Posted {new_posts} new courses.")

if __name__ == "__main__":
    main()
