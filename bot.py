import os
import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]

CHANNEL = "@YOUR_TEST_CHANNEL"

message = """
🤖 CouponXpert Bot Test

GitHub Actions is working successfully.
"""

url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

requests.post(
    url,
    data={
        "chat_id": CHANNEL,
        "text": message
    }
)

print("Message sent successfully")
