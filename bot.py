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

# Print all links containing "coursefolder.net/"
for a in soup.find_all("a", href=True):
    href = a["href"]

    if "coursefolder.net/" in href:
        print("TEXT:", a.get_text(strip=True))
        print("LINK:", href)
        print("-" * 50)
