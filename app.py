# -------------------------------------------------------------------------
# HUGGER BOT - Final Version
# Developed by Gemini, based on Mohammad's specifications.
#
# This application uses Flask for Telegram Webhook handling and SQLAlchemy
# for persistent data storage (PostgreSQL is required). It also integrates
# an external AI API (GAP API) for intelligent text summarization.
# -------------------------------------------------------------------------

import os
import json
import time
from datetime import datetime, timedelta

# External Libraries
import requests
from flask import Flask, request, jsonify
from telegram import Bot, Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters
from googletrans import Translator
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.exc import SQLAlchemyError
from random import choice

# -------------------------------------------------------------------------
# 1. CONFIGURATION & ENVIRONMENT VARIABLES
# -------------------------------------------------------------------------

# Load environment variables
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DATABASE_URL = os.environ.get('DATABASE_URL')
# Custom AI API Configuration
GAP_API_URL = os.environ.get('GAP_API_URL')
GAP_API_KEY = os.environ.get('GAP_API_KEY')

# Default User Mapping (to personalize messages)
# The application will try to load USER_NAMES_MAP from environment variables first.
# If not found, it falls back to this default map provided by Mohammad.
DEFAULT_USER_MAP = {
    "6847219190": "محمد", # XenOrion
    "7291579302": "عباس", # Comrade_amir
    "8078073721": "سهند", # Sahand Ebrahimi
    "6550959404": "ایلیا", # Iliya_r8
    "1140241105": "حمیدرضا" # Hamidreza Mousivand
}

# -------------------------------------------------------------------------
# 2. DATABASE SETUP (SQLAlchemy)
# -------------------------------------------------------------------------

if not DATABASE_URL:
    print("FATAL: DATABASE_URL is not set. The application will not work without a database connection.")

# Database Initialization
Base = declarative_base()
engine = create_engine(DATABASE_URL, echo=False)
Session = sessionmaker(bind=engine)

# Task Model (وظایف تیم)
class Task(Base):
    __tablename__ = 'tasks'
    id = Column(Integer, primary_key=True)
    title = Column(String(256), nullable=False)
    assigned_to = Column(String(64))  # Telegram User ID
    due_date = Column(DateTime)
    status = Column(String(32), default='To Do')
    created_at = Column(DateTime, default=datetime.utcnow)

# Archive Model (حافظه بلندمدت و آرشیو لینک)
class ArchiveItem(Base):
    __tablename__ = 'archive'
    id = Column(Integer, primary_key=True)
    title = Column(String(256), nullable=False)
    content = Column(Text, nullable=False) # URL or Memorized Text
    tags = Column(String(256))
    user_id = Column(String(64))
    archived_at = Column(DateTime, default=datetime.utcnow)

# Activity Log Model (ثبت کارکرد فردی)
class ActivityLog(Base):
    __tablename__ = 'activity_log'
    id = Column(Integer, primary_key=True)
    user_id = Column(String(64))
    description = Column(Text, nullable=False)
    logged_at = Column(DateTime, default=datetime.utcnow)

# Shopping List Model (مدیریت خرید)
class ShoppingItem(Base):
    __tablename__ = 'shopping_list'
    id = Column(Integer, primary_key=True)
    item_name = Column(String(256), nullable=False)
    is_bought = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    bought_at = Column(DateTime)

# Create tables in the database
Base.metadata.create_all(engine)

# -------------------------------------------------------------------------
# 3. UTILITY FUNCTIONS
# -------------------------------------------------------------------------

def get_user_name(user_id):
    """Retrieves a personalized name based on user ID."""
    try:
        user_map_json = os.environ.get('USER_NAMES_MAP')
        user_map = json.loads(user_map_json) if user_map_json else DEFAULT_USER_MAP
        return user_map.get(str(user_id), 'رفیق')
    except (json.JSONDecodeError, TypeError):
        return DEFAULT_USER_MAP.get(str(user_id), 'رفیق')


def is_valid_url(url):
    """Simple check if a string looks like a URL."""
    return url.startswith('http')


def _call_external_ai_api_for_summary(text_to_summarize):
    """Calls the configured external AI API (e.g., GAP API) for summarization."""
    if not GAP_API_KEY or not GAP_API_URL:
        return "⚠️ دسترسی به API هوش مصنوعی قطع است. لطفاً متغیرهای محیطی GAP_API_KEY و GAP_API_URL را تنظیم کنید."

    try:
        # We need a generic payload structure that many AI models use
        headers = {
            'Authorization': f'Bearer {GAP_API_KEY}',
            'Content-Type': 'application/json',
        }
        
        # System instruction in Persian for the AI model
        system_prompt = "تو یک دستیار هوشمند، حرفه‌ای و خلاصه نویس هستی. متن فارسی یا انگلیسی زیر را بخوان و خلاصه‌ای دقیق، مختصر و کاملاً به زبان فارسی از آن تهیه کن."
        
        payload = {
            # Assuming a standard chat completion endpoint structure
            "model": "gpt-3.5-turbo", # Common model name
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"خلاصه‌ای از این متن بده:\n\n{text_to_summarize}"}
            ],
            "max_tokens": 500
        }
        
        # Exponential Backoff for stability
        max_retries = 3
        for i in range(max_retries):
            response = requests.post(GAP_API_URL + '/chat/completions', headers=headers, json=payload, timeout=30)
            if response.status_code == 200:
                result = response.json()
                # Assuming the response structure contains choices[0].message.content
                summary = result['choices'][0]['message']['content']
                return f"🧠 خلاصه‌سازی هوشمند:\n\n{summary}"
            elif response.status_code == 429: # Rate limit
                time.sleep(2 ** i)
            else:
                return f"❌ خطای API: {response.status_code} - پیام: {response.text[:100]}"
                
        return "❌ خطای ناشی از محدودیت در تعداد درخواست‌ها. لطفاً دوباره تلاش کنید."

    except requests.exceptions.RequestException as e:
        return f"❌ خطای اتصال به سرور GAP API: {e}"


# -------------------------------------------------------------------------
# 4. HANDLERS (Telegram Commands)
# -------------------------------------------------------------------------

def start(update: Update, context):
    """Handles the /start command and welcomes the user personally."""
    user_id = update.effective_user.id
    user_name = get_user_name(user_id)
    
    message = (
        f"سلام {user_name} جان! 😎 من ربات هوگر هستم، دستیار هوشمند گروه شما.\n\n"
        f"مأموریت من: مدیریت تسک‌ها، آرشیو کردن دانش و کمک‌های سریع.\n\n"
        f"با /help می‌تونی تمام کارهایی که بلدم رو ببینی. بزن بریم!"
    )
    update.message.reply_text(message)


def help_command(update: Update, context):
    """Provides a detailed list of all available commands."""
    message = (
        "📚 راهنمای جامع ربات هوگر:\n\n"
        "**مدیریت کارها (تسک):**\n"
        "• `/addtask <عنوان> /to @نام_کاربر /due YYYY-MM-DD` : ثبت یک کار جدید.\n"
        "• `/tasks` : نمایش لیست کارهای فعال.\n"
        "• `/done <شماره_تسک>` : انجام‌شده علامت زدن یک کار.\n\n"
        
        "**حافظه بلندمدت و دانش:**\n"
        "• `/memorize` : روی یک پیام مهم ریپلای کن تا ربات اون رو به حافظه بلندمدت اضافه کنه.\n"
        "• `/archive <لینک> #تگ1 #تگ2` : ذخیره لینک‌ها و مستندات مهم.\n"
        "• `/search <کلمه کلیدی>` : جستجو در آرشیو و حافظه ربات.\n\n"
        
        "**مدیریت خرید و فعالیت:**\n"
        "• `/buy add <آیتم>` : افزودن یک قلم به لیست خرید.\n"
        "• `/buy list` : نمایش اقلام مورد نیاز و خریداری شده.\n"
        "• `/buy done <شماره_آیتم>` : علامت زدن یک قلم به عنوان خریداری شده.\n"
        "• `/logwork <شرح کار>` : ثبت فعالیتی که انجام دادی.\n\n"
        
        "**ابزارهای هوشمند:**\n"
        "• `/summary` : گزارش آماری هفتگی (و اگر با `#خلاصه_کن` ریپلای کنی، پیام رو با AI خلاصه می‌کنه).\n"
        "• `/countdown` : روزشمار تا شروع هوگر.\n"
        "• `/translate <متن انگلیسی>` : ترجمه سریع متن به فارسی.\n"
        "• `/commands` : لیست سریع دستورات."
    )
    update.message.reply_text(message)


def command_functions(update: Update, context):
    """Provides a quick, simple list of command functions."""
    quick_list = (
        "📋 لیست سریع دستورات:\n\n"
        "• `/memorize`: ثبت پیام مهم در حافظه (ریپلای لازم).\n"
        "• `/search`: جستجو در آرشیو و حافظه.\n"
        "• `/archive`: ذخیره لینک‌های مهم.\n"
        "• `/buy`: مدیریت لیست خرید.\n"
        "• `/addtask`: ثبت کار جدید.\n"
        "• `/tasks`: لیست کارهای باقی‌مانده.\n"
        "• `/done`: اتمام یک کار.\n"
        "• `/summary`: گزارش آماری یا خلاصه‌سازی AI.\n"
        "• `/logwork`: ثبت کارکرد فردی.\n"
        "• `/countdown`: روزشمار هوگر.\n"
        "• `/translate`: ترجمه متن انگلیسی."
    )
    update.message.reply_text(quick_list)


def add_task(update: Update, context):
    """Handles the /addtask command to add a new task."""
    user_id = update.effective_user.id
    user_name = get_user_name(user_id)
    text = update.message.text
    
    if not context.args:
        update.message.reply_text(f"اوه {user_name} جان، یادم بده! باید عنوان کار رو هم بنویسی.\nفرمت صحیح: `/addtask <عنوان> /to @نام_کاربر /due YYYY-MM-DD`")
        return

    # Simple parsing logic
    try:
        parts = ' '.join(context.args).split('/')
        title_part = parts[0].strip()
        
        assigned_to = next((p.strip() for p in parts if p.strip().startswith('to')), 'Nobody')
        due_date_str = next((p.strip() for p in parts if p.strip().startswith('due')), None)

        # Extract assignment and date
        assigned_to = assigned_to.split(' ')[1].strip() if assigned_to != 'Nobody' and len(assigned_to.split(' ')) > 1 else 'N/A'
        
        due_date = None
        if due_date_str and len(due_date_str.split(' ')) > 1:
            date_str = due_date_str.split(' ')[1].strip()
            due_date = datetime.strptime(date_str, '%Y-%m-%d')
            
        if not title_part:
            raise ValueError("عنوان کار خالی است.")
            
        session = Session()
        new_task = Task(
            title=title_part,
            assigned_to=assigned_to,
            due_date=due_date,
            status='To Do'
        )
        session.add(new_task)
        session.commit()
        
        due_info = f"تا تاریخ: {due_date.strftime('%Y-%m-%d')}" if due_date else "مهلت: نامشخص"
        update.message.reply_text(
            f"✅ کار جدید ثبت شد!\n"
            f"عنوان: **{title_part}**\n"
            f"مسئول: {assigned_to}\n"
            f"{due_info}\n"
            f"شماره تسک: `{new_task.id}`\n\n"
            f"حواست باشه {assigned_to}، باید زود تمومش کنی! 😉"
        )
        
    except ValueError as e:
        update.message.reply_text(f"❌ خطای فرمت! مطمئنی تاریخ رو درست زدی؟\nفرمت تاریخ: YYYY-MM-DD (مثلاً 2026-03-01)\nجزئیات خطا: {e}")
    except SQLAlchemyError:
        update.message.reply_text("❌ خطای دیتابیس در ثبت کار جدید. تیم فنی باید سرور رو چک کنه.")
        session.rollback()
    finally:
        session.close()


def list_tasks(update: Update, context):
    """Handles the /tasks command to show active tasks."""
    user_id = update.effective_user.id
    user_name = get_user_name(user_id)
    session = Session()
    try:
        active_tasks = session.query(Task).filter(Task.status.in_(['To Do', 'In Progress'])).all()
        
        if not active_tasks:
            update.message.reply_text(
                f"🎉 آفرین به تیم هوگر! {user_name} جان، در حال حاضر هیچ کار فعالی نداریم. "
                "بریم سراغ چالش بعدی! 😎"
            )
            return

        tasks_list = "📋 لیست کارهای باقی‌مانده:\n\n"
        for task in active_tasks:
            due_info = f"({task.due_date.strftime('%Y-%m-%d')})" if task.due_date else ""
            tasks_list += (
                f"**#{task.id}** [وضعیت: {task.status}]\n"
                f"عنوان: {task.title}\n"
                f"مسئول: {task.assigned_to} {due_info}\n"
                "----------------------------------\n"
            )
        
        update.message.reply_text(tasks_list)
        
    except SQLAlchemyError:
        update.message.reply_text("❌ خطای دیتابیس در دریافت لیست کارها.")
    finally:
        session.close()


def mark_done(update: Update, context):
    """Handles the /done command to complete a task."""
    user_id = update.effective_user.id
    user_name = get_user_name(user_id)

    if not context.args or not context.args[0].isdigit():
        update.message.reply_text(f"عزیزم {user_name} جان! باید شماره تسک رو بعد از `/done` بزنی. مثلا: `/done 15`")
        return
        
    task_id = int(context.args[0])
    session = Session()
    try:
        task = session.query(Task).filter(Task.id == task_id).first()
        
        if not task:
            update.message.reply_text(f"❌ تسکی با شماره `{task_id}` پیدا نشد. مطمئنی درسته؟")
            return
            
        task.status = 'Done'
        session.commit()
        update.message.reply_text(
            f"✅ دمت گرم {user_name}!\n"
            f"کار **'{task.title}'** با موفقیت به وضعیت 'انجام‌شده' منتقل شد. "
            "بریم سراغ کار بعدی؟ 😉"
        )
        
    except SQLAlchemyError:
        update.message.reply_text("❌ خطای دیتابیس در به‌روزرسانی وضعیت کار.")
        session.rollback()
    finally:
        session.close()


def archive_item(update: Update, context):
    """Handles /archive for links and /memorize for important texts."""
    user_id = update.effective_user.id
    user_name = get_user_name(user_id)

    # Check for /memorize logic (handled by archive_item function)
    is_memorize = update.message.text.startswith('/memorize')

    if is_memorize:
        # MEMORIZE LOGIC
        if not update.message.reply_to_message:
            update.message.reply_text(f"عزیزم {user_name}، برای `/memorize` باید روی پیام مورد نظرت **ریپلای** کنی.")
            return

        original_message = update.message.reply_to_message.text
        if not original_message:
            update.message.reply_text(f"پیام ریپلای شده {user_name} جان متنی نیست که بشه آرشیو کرد.")
            return

        original_user = update.message.reply_to_message.from_user
        original_user_name = get_user_name(original_user.id)
        
        title = f"پیام مهم از {original_user_name} (@{original_user.username or original_user.first_name})"
        content = original_message
        tags = "حافظه_بلند_مدت, پیام_مهم"
        confirmation_msg = "🧠 پیام با موفقیت در حافظه بلندمدت ربات ثبت شد."
        
    else:
        # ARCHIVE LOGIC (for links)
        if not context.args:
            update.message.reply_text(
                f"اوه {user_name}، هیچی نفرستادی!\n"
                "برای آرشیو لینک باید بنویسی: `/archive <لینک> #تگ1 #تگ2 ...`"
            )
            return

        # Simple parsing for link and tags
        input_text = ' '.join(context.args)
        parts = input_text.split()
        
        link = next((p for p in parts if is_valid_url(p)), None)
        tags = ','.join(p[1:] for p in parts if p.startswith('#'))
        
        if not link:
            update.message.reply_text(f"لینک معتبری پیدا نکردم {user_name} جان. مطمئن شو با `http` یا `https` شروع می‌شه.")
            return

        title = link # Use link as title if not provided
        content = link
        confirmation_msg = f"🔗 لینک **{link}** با موفقیت در آرشیو ذخیره شد."


    session = Session()
    try:
        new_archive = ArchiveItem(
            title=title,
            content=content,
            tags=tags,
            user_id=str(user_id)
        )
        session.add(new_archive)
        session.commit()
        update.message.reply_text(confirmation_msg + f"\nتگ‌ها: {tags}")
        
    except SQLAlchemyError:
        update.message.reply_text("❌ خطای دیتابیس در ذخیره آرشیو. لطفاً وضعیت دیتابیس را بررسی کنید.")
        session.rollback()
    finally:
        session.close()

# Alias the /memorize command to the archive_item handler
memorize_command = CommandHandler("memorize", archive_item)


def search_archive(update: Update, context):
    """Handles the /search command for finding items in the archive."""
    user_id = update.effective_user.id
    user_name = get_user_name(user_id)

    if not context.args:
        update.message.reply_text(f"چیزی برای جستجو نگفتی {user_name} جان. یه کلمه کلیدی یا تگ بهم بده.")
        return

    query_text = ' '.join(context.args).lower()
    session = Session()
    try:
        # Search by title, content (link/text), or tags
        results = session.query(ArchiveItem).filter(
            (ArchiveItem.title.ilike(f'%{query_text}%')) |
            (ArchiveItem.content.ilike(f'%{query_text}%')) |
            (ArchiveItem.tags.ilike(f'%{query_text}%'))
        ).order_by(ArchiveItem.archived_at.desc()).limit(10).all()

        if not results:
            update.message.reply_text(f"متأسفانه {user_name} جان، چیزی با عبارت **'{query_text}'** در حافظه پیدا نشد. 🧐")
            return

        result_list = f"🔍 نتایج جستجو برای '{query_text}' (جدیدترین‌ها):\n\n"
        for i, item in enumerate(results):
            content_preview = item.content[:50] + '...' if len(item.content) > 50 else item.content
            result_list += (
                f"**#{item.id}** - **{item.title}**\n"
                f"محتوا: {content_preview}\n"
                f"تگ‌ها: {item.tags or 'ندارد'}\n"
                "----------------------------------\n"
            )

        update.message.reply_text(result_list)

    except SQLAlchemyError:
        update.message.reply_text("❌ خطای دیتابیس در اجرای جستجو.")
    finally:
        session.close()


def log_work(update: Update, context):
    """Handles the /logwork command to archive individual activities."""
    user_id = update.effective_user.id
    user_name = get_user_name(user_id)
    
    if not context.args:
        update.message.reply_text(f"خب {user_name}، بگو چه کاری انجام دادی تا ثبت کنم. فرمت: `/logwork <شرح کامل کار>`")
        return

    description = ' '.join(context.args)
    session = Session()
    try:
        new_log = ActivityLog(
            user_id=str(user_id),
            description=description
        )
        session.add(new_log)
        session.commit()
        update.message.reply_text(
            f"📝 آفرین {user_name} جان! کارکرد شما با شرح:\n"
            f"**'{description[:100]}...'**\n"
            f"با موفقیت در آرشیو فعالیت‌های فردی ثبت شد. دمت گرم! 💪"
        )
        
    except SQLAlchemyError:
        update.message.reply_text("❌ خطای دیتابیس در ثبت فعالیت.")
        session.rollback()
    finally:
        session.close()


def countdown_to_hugger(update: Update, context):
    """Handles the /countdown command to show days remaining until the next Esfand 10."""
    user_id = update.effective_user.id
    user_name = get_user_name(user_id)
    
    now = datetime.now()
    # 10 Esfand corresponds to March 1st (approximately) in the Gregorian calendar
    target_date = datetime(now.year, 3, 1)

    # If March 1st has already passed this year, set the target for next year
    if now > target_date:
        target_date = datetime(now.year + 1, 3, 1)

    # Calculate difference
    time_remaining = target_date - now
    days_remaining = time_remaining.days
    
    message = (
        f"⏳ هی {user_name} جان، گوش کن!\n\n"
        f"تا شروع بزرگ هوگر (**۱۰ اسفند** یا **{target_date.year}-{target_date.month}-{target_date.day}**)\n\n"
        f"🔥 فقط **{days_remaining} روز** باقی مونده! 🔥\n\n"
        "بجنبید! وقت برای قهوه خوردن نیست. باید به دنیا ثابت کنیم چی داریم!"
    )
    update.message.reply_text(message)


def translate_command(update: Update, context):
    """Handles the /translate command for English to Persian translation."""
    user_id = update.effective_user.id
    user_name = get_user_name(user_id)

    if not context.args:
        update.message.reply_text(f"متن انگلیسی رو بده {user_name} جان. مثل: `/translate This is a great project.`")
        return

    text_to_translate = ' '.join(context.args)
    
    # Use googletrans library (unofficial Google Translate API usage)
    try:
        translator = Translator()
        # Translate from auto-detected source (usually English) to Persian ('fa')
        translation = translator.translate(text_to_translate, dest='fa')
        
        message = (
            f"🌍 ترجمه سریع برای {user_name}:\n"
            f"**متن انگلیسی:** {text_to_translate}\n"
            f"**ترجمه فارسی:** {translation.text}"
        )
        update.message.reply_text(message)
        
    except Exception as e:
        update.message.reply_text(f"❌ مشکل در اتصال به مترجم. اینم دلیلش: {e}")


def weekly_summary(update: Update, context):
    """
    Handles /summary. It checks for a reply with #خلاصه_کن for AI summarization,
    otherwise, it provides the taunting weekly statistical report.
    """
    user_id = update.effective_user.id
    user_name = get_user_name(user_id)
    
    # AI SUMMARIZATION LOGIC (If reply and #خلاصه_کن is present)
    if update.message.reply_to_message and ('#خلاصه_کن' in update.message.text or '#خلاصه_کن' in update.message.caption or 'خلاصه_کن' in context.args):
        text_to_summarize = update.message.reply_to_message.text
        if not text_to_summarize:
            update.message.reply_text(f"{user_name} جان، برای خلاصه‌سازی هوشمند باید روی یک پیام متنی ریپلای کنی.")
            return
            
        # Call the external AI API
        summary_result = _call_external_ai_api_for_summary(text_to_summarize)
        update.message.reply_text(summary_result)
        return

    # STATISTICAL SUMMARY LOGIC (Default behavior)
    session = Session()
    try:
        one_week_ago = datetime.utcnow() - timedelta(days=7)
        
        # 1. New Tasks in the last 7 days
        new_tasks = session.query(Task).filter(Task.created_at >= one_week_ago).count()
        
        # 2. Done Tasks in the last 7 days
        done_tasks = session.query(Task).filter(Task.status == 'Done', Task.created_at >= one_week_ago).count()

        # 3. Remaining active tasks
        remaining_tasks = session.query(Task).filter(Task.status.in_(['To Do', 'In Progress'])).count()
        
        # 4. New Archive Items in the last 7 days
        new_archives = session.query(ArchiveItem).filter(ArchiveItem.archived_at >= one_week_ago).count()
        
        # 5. New Activity Logs in the last 7 days
        new_logs = session.query(ActivityLog).filter(ActivityLog.logged_at >= one_week_ago).count()
        
        # 6. Weekly Report Formatting with Taunting/Motivational Tone
        
        taunting_phrases = [
            "تا کی می‌خواید چایی بخورید؟",
            "انگار یه لاک‌پشت مدیر پروژه‌تونه!",
            "بجنبید! وقت برای قهوه خوردن نیست.",
            "این حجم از عقب موندگی قابل تقدیره!"
        ]
        
        if remaining_tasks > 0:
            tone = choice(taunting_phrases)
        else:
            tone = "هیچ کار فعال باقی نمونده؟ یا کارهای سخت‌تری بگیرید، یا دارین دروغ می‌گید! 😉"


        message = (
            f"📊 گزارش هفتگی عملکرد (از دید من):\n\n"
            f"**تسک‌ها:**\n"
            f"• **جدید این هفته:** {new_tasks} کار\n"
            f"• **انجام شده این هفته:** {done_tasks} کار\n"
            f"• **باقی‌مانده فعال:** {remaining_tasks} کار\n"
            f"**دانش و آرشیو:**\n"
            f"• **آرشیو جدید:** {new_archives} آیتم\n"
            f"• **ثبت فعالیت (Log):** {new_logs} مورد\n\n"
            f"**پیام من به تیم هوگر:**\n"
            f"📢 **{tone}**"
        )
        
        update.message.reply_text(message)

    except SQLAlchemyError:
        update.message.reply_text("❌ خطای دیتابیس در تهیه گزارش آماری.")
    finally:
        session.close()


# -------------------------------------------------------------------------
# 5. SHOPPING LIST HANDLERS (/buy)
# -------------------------------------------------------------------------

def buy_command(update: Update, context):
    """Handles the /buy command with sub-commands: add, done, list."""
    user_id = update.effective_user.id
    user_name = get_user_name(user_id)

    if not context.args:
        update.message.reply_text(
            f"لطفاً یکی از دستورهای خرید را وارد کنید:\n"
            f"• `/buy add <آیتم>`\n"
            f"• `/buy done <شماره آیتم>`\n"
            f"• `/buy list`"
        )
        return

    sub_command = context.args[0].lower()
    
    session = Session()
    try:
        if sub_command == 'add':
            if len(context.args) < 2:
                update.message.reply_text(f"چی رو باید بخریم {user_name}؟")
                return
            item_name = ' '.join(context.args[1:])
            new_item = ShoppingItem(item_name=item_name)
            session.add(new_item)
            session.commit()
            update.message.reply_text(f"🛒 **'{item_name}'** به لیست خرید اضافه شد. ممنون {user_name}!")
            
        elif sub_command == 'done':
            if len(context.args) < 2 or not context.args[1].isdigit():
                update.message.reply_text(f"شماره آیتم رو برای `/buy done` وارد کن.")
                return
            item_id = int(context.args[1])
            item = session.query(ShoppingItem).filter(ShoppingItem.id == item_id).first()
            
            if item and not item.is_bought:
                item.is_bought = True
                item.bought_at = datetime.utcnow()
                session.commit()
                update.message.reply_text(f"✅ **'{item.item_name}'** خریداری شد. {user_name}، دمت گرم!")
            elif item and item.is_bought:
                update.message.reply_text(f"این آیتم ({item.item_name}) قبلاً خریداری شده بود!")
            else:
                update.message.reply_text(f"آیتمی با شماره `{item_id}` در لیست خرید پیدا نشد.")

        elif sub_command == 'list':
            required_items = session.query(ShoppingItem).filter(ShoppingItem.is_bought == False).order_by(ShoppingItem.created_at).all()
            bought_items = session.query(ShoppingItem).filter(ShoppingItem.is_bought == True).order_by(ShoppingItem.bought_at.desc()).limit(5).all()
            
            output = f"🛒 لیست خرید گروه هوگر:\n\n"
            
            # Required Items
            if required_items:
                output += "🛑 **مورد نیاز (هنوز نخریدیم):**\n"
                for item in required_items:
                    output += f"**#{item.id}** - {item.item_name}\n"
            else:
                output += "✅ چیزی برای خرید نمونده. انبار پره! 😉\n"

            # Bought Items
            if bought_items:
                output += "\n👍 **اخیراً خریداری شده:**\n"
                for item in bought_items:
                    time_ago = (datetime.utcnow() - item.bought_at).days
                    output += f"**{item.item_name}** (توسط تیم در {time_ago} روز پیش)\n"

            update.message.reply_text(output)
            
        else:
            update.message.reply_text(f"دستور `{sub_command}` معتبر نیست. از `add`، `done` یا `list` استفاده کن.")

    except SQLAlchemyError:
        update.message.reply_text("❌ خطای دیتابیس در مدیریت لیست خرید.")
        session.rollback()
    finally:
        session.close()


# -------------------------------------------------------------------------
# 6. FLASK & BOT SETUP
# -------------------------------------------------------------------------

app = Flask(__name__)

# Initialize Telegram Bot
if BOT_TOKEN:
    bot = Bot(BOT_TOKEN)
    dispatcher = Dispatcher(bot, None, use_context=True)
else:
    print("FATAL: BOT_TOKEN is not set. Bot will not function.")
    dispatcher = None # Ensure dispatcher is None if BOT_TOKEN is missing

# Add Handlers to Dispatcher
if dispatcher:
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("help", help_command))
    dispatcher.add_handler(CommandHandler("commands", command_functions))
    
    # Task Management
    dispatcher.add_handler(CommandHandler("addtask", add_task))
    dispatcher.add_handler(CommandHandler("tasks", list_tasks))
    dispatcher.add_handler(CommandHandler("done", mark_done))
    
    # Knowledge Management
    dispatcher.add_handler(CommandHandler("archive", archive_item))
    dispatcher.add_handler(CommandHandler("memorize", archive_item)) # Same handler used for /memorize
    dispatcher.add_handler(CommandHandler("search", search_archive))
    
    # Utility and Summary
    dispatcher.add_handler(CommandHandler("logwork", log_work))
    dispatcher.add_handler(CommandHandler("countdown", countdown_to_hugger))
    dispatcher.add_handler(CommandHandler("translate", translate_command))
    dispatcher.add_handler(CommandHandler("summary", weekly_summary))
    
    # Shopping List
    dispatcher.add_handler(CommandHandler("buy", buy_command))

    # Error Handler (Basic)
    # def error_handler(update, context):
    #     print(f"Update {update} caused error {context.error}")
    # dispatcher.add_error_handler(error_handler)


@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    """Main Webhook endpoint for Telegram."""
    if not BOT_TOKEN:
        return "BOT_TOKEN is missing.", 500
        
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), bot)
        dispatcher.process_update(update)
        return 'ok', 200
    return 'ok', 200

# Vercel requires a default endpoint check
@app.route('/')
def home():
    """Simple health check endpoint."""
    return f"Hugger Bot is running. Database Status: {'Connected' if DATABASE_URL else 'Missing URI'}", 200


# The Flask application instance (app) is used by Vercel for deployment.
# In a local environment, you would run app.run() here.
