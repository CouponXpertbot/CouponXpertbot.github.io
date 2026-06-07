import requests
from bs4 import BeautifulSoup
import re
import os
from typing import List, Optional

# ==========================
# Telegram Settings (from Secrets)
# ==========================
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["@Channelboottest"]  # e.g., "@Channelboottest" or "-1001234567890"

# ==========================
# Load Already Posted Udemy Links
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
# Scrape Telegram Channel Web Preview (no login)
# ==========================
def scrape_telegram_channel_links(channel: str = "coursefolder", limit: int = 20) -> List[str]:
    """
    Returns list of coursefolder.net URLs found in recent channel posts.
    """
    url = f"https://t.me/s/{channel}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"❌ Failed to fetch channel page: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    messages = soup.find_all("div", class_="tgme_widget_message")

    coursefolder_urls = []
    for msg in messages[:limit]:
        for a in msg.find_all("a", href=True):
            href = a["href"]
            if re.match(r"https?://coursefolder\.net/", href):
                coursefolder_urls.append(href)
    return list(dict.fromkeys(coursefolder_urls))  # deduplicate

# ==========================
# Extract Udemy Link from coursefolder.net Page
# ==========================
def extract_udemy_link(coursefolder_url: str) -> Optional[str]:
    try:
        resp = requests.get(coursefolder_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        resp.raise_for_status()
        html = resp.text
        
        # Find JSON-LD "offers" block
        pattern = r'"offers":\s*{[^}]*"url":\s*"([^"]+)"'
        match = re.search(pattern, html)
        if match and "udemy.com/course/" in match.group(1):
            return match.group(1)
    except Exception as e:
        print(f"⚠️ Error extracting from {coursefolder_url}: {e}")
    return None

# ==========================
# Send Message to Telegram Channel
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
# Main Bot Logic
# ==========================
def main():
    print("🔍 Starting bot...")
    posted = load_posted_links()
    print(f"Already posted: {len(posted)} Udemy links")
    
    # 1. Get coursefolder.net links from Telegram channel preview
    cf_links = scrape_telegram_channel_links()
    print(f"Found {len(cf_links)} coursefolder.net links in channel")
    
    new_posts = 0
    MAX_NEW = 3  # limit per run
    
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
        
        # Derive title from the coursefolder URL slug
        slug = cf_url.rstrip('/').split('/')[-1]
        title = slug.replace('-', ' ').title()
        
        # Send to Telegram
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
