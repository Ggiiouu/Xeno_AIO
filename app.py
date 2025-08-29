import os
from flask import Flask, request, jsonify
from telegram import Bot, Update

# توکن بات رو از متغیرهای محیطی می‌خونیم تا امن باشه
BOT_TOKEN = os.environ.get("BOT_TOKEN")

app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)

@app.route('/')
def home():
    return "Hello, I am Xeno's backend!"

@app.route('/webhook', methods=['POST'])
async def webhook_handler():
    # داده‌های ارسالی از تلگرام رو دریافت می‌کنه
    update = Update.de_json(request.get_json(), bot)
    
    # اینجا می‌تونی منطق اصلی بات رو اضافه کنی
    # مثلاً می‌تونی پیام‌ها رو بر اساس محتواشون پردازش کنی
    if update.message:
        chat_id = update.message.chat.id
        text = update.message.text
        
        # این خط اصلاح شده تا منتظر ارسال پیام بمونه
        await bot.send_message(chat_id=chat_id, text=f"You said: {text}")
    
    return jsonify(success=True)

if __name__ == '__main__':
    app.run(port=os.environ.get("PORT", 5000))