import requests

url = "https://elearn.interviewgig.com/free-online-courses-coupons/"

html = requests.get(
    url,
    headers={"User-Agent": "Mozilla/5.0"},
    timeout=30
)

text = html.text

keyword = "Microsoft DP-203"

pos = text.find(keyword)

print(text[pos-1000:pos+3000])
