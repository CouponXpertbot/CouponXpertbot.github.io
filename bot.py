import requests
from bs4 import BeautifulSoup
import os

# ==========================
# Telegram Settings
# ==========================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = "@Channelboottest"

# ==========================
# Load Posted Courses
# ==========================

try:
    with open("posted_courses.txt", "r", encoding="utf-8") as f:
        posted = set(f.read().splitlines())
except FileNotFoundError:
    posted = set()

# ==========================
# Scrape InterviewGIG
# ==========================

url = "https://elearn.interviewgig.com/free-online-courses-coupons/"

response = requests.get(
    url,
    headers={"User-Agent": "Mozilla/5.0"},
    timeout=30
)

soup = BeautifulSoup(response.text, "html.parser")

courses = soup.select("div.rehub_bordered_block.rh_listitem")

print(f"Found {len(courses)} courses")

# ==========================
# Find First New Course
# ==========================
posted_count = 0

for course in courses:

    title_tag = course.select_one(
        "div.font120.fontbold.rehub-main-font.lineheight20"
    )

    link_tag = course.select_one(
        "a.re_track_btn.btn_offer_block"
    )

    if not title_tag or not link_tag:
        continue

    title = title_tag.get_text(strip=True)
    link = link_tag["href"]

    # Skip non-Udemy links
    if "udemy.com" not in link:
        continue

    # Skip already posted courses
    if link in posted:
        continue

    print("Posting:", title)

    # ==========================
    # CouponXpert Format
    # ==========================

    message = f"""
🎓 FREE UDEMY COURSE (100% OFF)

📘 Course: {title}

🚀 Enroll before the coupon expires!

🔗 Enroll Here:
{link}

⚠️ Coupon may expire anytime

👇 More FREE books & courses
👉 https://t.me/CouponXpert
"""

    # ==========================
    # Send To Telegram
    # ==========================

    telegram_response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": CHANNEL,
            "text": message
        }
    )

    print(telegram_response.text)

    # ==========================
    # Save Posted Link
    # ==========================

    with open("posted_courses.txt", "a", encoding="utf-8") as f:
        f.write(link + "\n")

    posted.add(link)

    print("Saved to posted_courses.txt")

    posted_count += 1

    if posted_count >= 3:  
        break

if posted_count == 0:
    print("No new courses found")
else:
    print(f"Posted {posted_count} new courses")
