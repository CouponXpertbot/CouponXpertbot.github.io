import requests

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
}

response = requests.get(
    "https://coursefolder.net/",
    headers=headers,
    timeout=20
)

print(response.status_code)
print(response.text[:1000])
