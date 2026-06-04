import requests
from bs4 import BeautifulSoup

headers = {
    "User-Agent": "Mozilla/5.0"
}

html = requests.get(
    "https://coursefolder.net/",
    headers=headers,
    timeout=20
).text

soup = BeautifulSoup(html, "html.parser")

print("TITLE:", soup.title)

print("\nFIRST 20 LINKS:\n")

for i, a in enumerate(soup.find_all("a", href=True)[:20]):
    print(i + 1)
    print("TEXT:", a.get_text(strip=True))
    print("HREF:", a["href"])
    print("-" * 40)
