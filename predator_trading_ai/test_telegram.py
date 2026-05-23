import os
from dotenv import load_dotenv
import requests

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID_1 = os.getenv("TELEGRAM_CHAT_ID_1")
CHAT_ID_2 = os.getenv("TELEGRAM_CHAT_ID_2")

if not BOT_TOKEN or not CHAT_ID_1 or not CHAT_ID_2:
    print("❌ Missing credentials")
    exit(1)

url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

# Send to both
for chat_id in [CHAT_ID_1, CHAT_ID_2]:
    data = {"chat_id": chat_id, "text": "✅ Test successful!"}
    response = requests.post(url, json=data)
    print(f"Chat {chat_id}: {response.text}")


if not BOT_TOKEN or not CHAT_ID:
    print("❌ No Telegram credentials")
    exit(1)

url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
data = {"chat_id": CHAT_ID, "text": "✅ Test successful!"}

response = requests.post(url, json=data)
print(response.text)
