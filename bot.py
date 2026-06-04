import requests
import os

BOT_TOKEN = os.environ["BOT_TOKEN"]

CHANNEL = "@CouponXpert"

message = """
🎓 FREE UDEMY COURSE (100% OFF)

📘 Course: Microsoft DP-203 Certified: Azure Data Engineer Associate

🔗 Enroll Here:
https://www.udemy.com/course/microsoft-dp-203-certified-azure-data-engineer-associate/?couponCode=AFE1CAB8F61CF05D52EA

⚠️ Coupon may expire anytime

👇 More FREE books & courses
👉 https://t.me/CouponXpert
"""

requests.post(
    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
    data={
        "chat_id": CHANNEL,
        "text": message
    }
)

print("sent")
