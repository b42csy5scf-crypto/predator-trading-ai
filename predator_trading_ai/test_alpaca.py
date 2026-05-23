import os
from dotenv import load_dotenv
import requests

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
BASE_URL = os.getenv("ALPACA_BASE_URL")

print("Testing Alpaca API...")
print(f"API Key: {API_KEY[:10]}...")
print(f"Base URL: {BASE_URL}")

headers = {"APCA-API-KEY-ID": API_KEY}

try:
    response = requests.get(f"{BASE_URL}/account", headers=headers)
    if response.status_code == 200:
        print("✅ SUCCESS!")
        account = response.json()
        print(f"Buying Power: {account.get('buying_power')}")
    else:
        print(f"❌ FAILED: {response.status_code}")
except Exception as e:
    print(f"❌ ERROR: {e}")
