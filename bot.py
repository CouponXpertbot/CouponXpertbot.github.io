import requests
from bs4 import BeautifulSoup

url = "https://elearn.interviewgig.com/free-online-courses-coupons/"

html = requests.get(
    url,
    headers={"User-Agent": "Mozilla/5.0"},
    timeout=30
)

soup = BeautifulSoup(html.text, "html.parser")

courses = soup.select("div.rehub_bordered_block.rh_listitem")

print(f"Found {len(courses)} courses\n")

for course in courses[:10]:
    title_tag = course.select_one(
        "div.font120.fontbold.rehub-main-font.lineheight20"
    )

    link_tag = course.select_one("a.re_track_btn.btn_offer_block")

    if title_tag and link_tag:
        title = title_tag.get_text(strip=True)
        link = link_tag["href"]

        print("TITLE:", title)
        print("LINK :", link)
        print("-" * 50)
