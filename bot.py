import requests

url = "https://elearn.interviewgig.com/free-online-courses-coupons/"

html = requests.get(
    url,
    headers={"User-Agent": "Mozilla/5.0"},
    timeout=30
)

print(html.status_code)

with open("page.html", "w", encoding="utf-8") as f:
    f.write(html.text)

print("saved")
