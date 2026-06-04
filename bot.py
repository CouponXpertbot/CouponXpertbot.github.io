import requests

html = requests.get("https://coursefolder.net/", timeout=20).text

print(html[:5000])
