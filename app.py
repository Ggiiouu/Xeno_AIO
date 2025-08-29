import os
import requests
from flask import Flask, request, jsonify

# توکن بات رو از متغیرهای محیطی می‌خونیم تا امن باشه
BOT_TOKEN = os.environ.get("BOT_TOKEN")

app = Flask(__name__)

@app.route('/')
def home():
    return "Hello, I am Xeno's backend!"

@app.route('/webhook', methods=['POST'])
def webhook_handler():
    # داده‌های ارسالی از تلگرام رو دریافت می‌کنه
    data = request.get_json()
    
    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"]["text"]
        
        # آدرس API تلگرام برای ارسال پیام
        api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        
        # داده‌های پیام رو آماده می‌کنه
        payload = {
            "chat_id": chat_id,
            "text": f"You said: {text}"
        }
        
        # پیام رو مستقیماً به تلگرام می‌فرسته
        requests.post(api_url, json=payload)
    
    return jsonify(success=True)

if __name__ == '__main__':
    app.run(port=os.environ.get("PORT", 5000))
