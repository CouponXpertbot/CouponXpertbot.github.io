import os
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = "@channelboottest"

POSTED_FILE = "posted_courses.txt"


def load_posted():
    try:
        with open(POSTED_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f)
    except FileNotFoundError:
        return set()


def save_posted(title):
    with open(POSTED_FILE, "a", encoding="utf-8") as f:
        f.write(title + "\n")


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    requests.post(
        url,
        data={
            "chat_id": CHANNEL,
            "text": message,
            "disable_web_page_preview": False
        }
    )


posted = load_posted()

url = "https://coursefolder.net/"
html = requests.get(url, timeout=20).text

soup = BeautifulSoup(html, "html.parser")

articles = soup.find_all("article")

if not articles:
    print("No courses found")
    exit()

latest = articles[0]

title = latest.get_text(strip=True)

link_tag = latest.find("a")

if not link_tag:
    print("No link found")
    exit()

course_link = link_tag["href"]

if title in posted:
    print("Already posted")
    exit()

message = f"""
🤖 FREE UDEMY COURSE (100% OFF)

📘 Course:
{title}

🔗 Enroll Here:
{course_link}

⚠️ Coupon may expire anytime.

👇 Join for daily FREE courses
👉 https://t.me/CouponXpert
"""

send_telegram(message)

save_posted(title)

print("Posted:", title)
