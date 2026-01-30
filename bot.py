# bot.py
import logging
import os
from dotenv import load_dotenv
from telebot import TeleBot, types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request, abort
import psycopg2
from psycopg2.extras import RealDictCursor
from openai import OpenAI, OpenAIError, APIError, APITimeoutError
import asyncio
import time

load_dotenv()

# تنظیمات از .env یا پنل Leapcell
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPPORT_ID = int(os.getenv("SUPPORT_ID", "1596192209"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "@aibotchannel")
DATABASE_URL = os.getenv("DATABASE_URL")
HF_API_KEY = os.getenv("HF_API_KEY")

if not BOT_TOKEN or not DATABASE_URL:
    raise ValueError("BOT_TOKEN یا DATABASE_URL تعریف نشده")

bot = TeleBot(BOT_TOKEN, threaded=False)

app = Flask(__name__)

HF_CLIENT = OpenAI(
    base_url="https://router.huggingface.co/v1",
    api_key=HF_API_KEY,
)

SYSTEM_PROMPT = """
تو یک دوست صمیمی، باهوش و کمک‌کننده هستی.
با لحن گرم، مهربون و کمی شوخ‌طبع حرف بزن.
هر سوالی پرسیدن، با حوصله، دقیق و مفید جواب بده.
از ایموجی هم گاهی استفاده کن که صمیمی‌تر بشه 😊
هر موضوعی بود با مهربونی کامل جواب بده.
"""

# اتصال به PostgreSQL
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# ایجاد جدول
def init_db():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    language TEXT DEFAULT 'fa'
                )
            ''')
        conn.commit()

init_db()

# ذخیره یا بروزرسانی کاربر
def save_or_update_user(user_id, username=None, first_name=None, last_name=None, language=None):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM users WHERE user_id = %s', (user_id,))
            if not cur.fetchone():
                cur.execute('''
                    INSERT INTO users (user_id, username, first_name, last_name, language)
                    VALUES (%s, %s, %s, %s, %s)
                ''', (user_id, username, first_name, last_name, language or 'fa'))
            elif language:
                cur.execute('UPDATE users SET language = %s WHERE user_id = %s', (language, user_id))
        conn.commit()

# گرفتن زبان کاربر
def get_user_language(user_id):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT language FROM users WHERE user_id = %s', (user_id,))
            result = cur.fetchone()
            return result['language'] if result else 'fa'

def translate(user_id, fa, en):
    return fa if get_user_language(user_id) == 'fa' else en

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

user_states = {}  # user_id → {"state": "support"|"chatbot", "messages": [...]}

# ────────────────────────────────────────────────
# شروع ربات
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    last_name = message.from_user.last_name

    save_or_update_user(user_id, username, first_name, last_name)

    if get_user_language(user_id) in ['fa', 'en']:
        check_membership(message)
        return

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("فارسی", callback_data="lang_fa"),
        types.InlineKeyboardButton("English", callback_data="lang_en")
    )

    bot.send_message(message.chat.id, "لطفاً زبان خود را انتخاب کنید / Please choose your language:", reply_markup=markup)


# چک عضویت
def check_membership(message):
    user_id = message.from_user.id
    try:
        member = bot.get_chat_member(CHANNEL_ID, user_id)
        if member.status in ["member", "administrator", "creator"]:
            show_main_menu(message)
            return
    except Exception as e:
        logger.error(f"خطا چک عضویت: {e}")

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("کانال هوش مصنوعی", url=f"https://t.me/aibotchannel"))
    markup.add(types.InlineKeyboardButton(translate(user_id, "ثبت و بررسی عضویت", "Check Membership"), callback_data="check_join"))

    bot.send_message(message.chat.id, translate(user_id,
                                                "لطفاً برای استفاده از ربات در کانال زیر عضو شوید:",
                                                "Please join the channel below to use the bot:"), reply_markup=markup)


# منوی اصلی
def show_main_menu(message):
    user_id = message.from_user.id

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(translate(user_id, "چت‌بات", "Chat Bot"), callback_data="chatbot"))
    markup.add(types.InlineKeyboardButton(translate(user_id, "پشتیبانی", "Support"), callback_data="support_open"))
    markup.add(types.InlineKeyboardButton(translate(user_id, "درباره ربات", "About Bot"), callback_data="about_bot"))
    markup.add(types.InlineKeyboardButton(translate(user_id, "تغییر زبان", "Change Language"), callback_data="change_lang"))

    bot.send_message(message.chat.id, translate(user_id,
                                                "سلام! 👋\nبه ربات هوشمند خوش آمدید!\nهر سؤالی داری، بگو تا کمکت کنم 🚀",
                                                "Hi! 👋\nWelcome to the smart bot!\nAsk anything, I'm here to help 🚀"), reply_markup=markup)


# مدیریت callbackها
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    data = call.data

    if data in ["lang_fa", "lang_en"]:
        lang = "fa" if data == "lang_fa" else "en"
        save_or_update_user(user_id, language=lang)
        check_membership(call.message)

    elif data == "check_join":
        check_membership(call.message)

    elif data == "change_lang":
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("فارسی", callback_data="lang_fa"),
                   types.InlineKeyboardButton("English", callback_data="lang_en"))
        bot.edit_message_text(translate(user_id, "زبان جدید را انتخاب کنید:", "Choose new language:"),
                              call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data == "about_bot":
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(translate(user_id, "بازگشت", "Back"), callback_data="main_menu"))
        bot.edit_message_text(translate(user_id,
                                        "🤖 این ربات با هوش مصنوعی واقعی کار می‌کنه و می‌تونه به هر سؤالی جواب بده.\nبا لحن دوستانه و صمیمی باهات حرف می‌زنه 😊",
                                        "🤖 This bot uses real AI and can answer any question.\nFriendly and warm tone 😊"),
                              call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data == "main_menu":
        show_main_menu(call.message)

    elif data == "support_open":
        user_states[user_id] = {"state": "support", "messages": []}
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(translate(user_id, "ارسال به پشتیبانی", "Send to Support"), callback_data="send_support"))
        markup.add(types.InlineKeyboardButton(translate(user_id, "برگشت به منو", "Back to Menu"), callback_data="main_menu"))
        bot.edit_message_text(translate(user_id,
                                        "هر چی می‌خوای به پشتیبانی بفرستی همین‌جا بفرست.\nوقتی تموم شد دکمه زیر رو بزن:",
                                        "Send anything to support here.\nWhen done click below:"),
                              call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data == "send_support":
        if user_id not in user_states or not user_states[user_id].get("messages"):
            bot.edit_message_text(translate(user_id, "هیچ پیامی برای ارسال وجود ندارد!", "No message to send!"),
                                  call.message.chat.id, call.message.message_id)
            return

        messages = user_states[user_id]["messages"]
        del user_states[user_id]

        header = f"پیام جدید از کاربر {user_id} (@{call.from_user.username or 'ندارد'})"

        bot.send_message(SUPPORT_ID, header)
        for msg in messages:
            bot.forward_message(SUPPORT_ID, call.message.chat.id, msg.message_id)

        bot.send_message(SUPPORT_ID, "برای پاسخ مستقیم ریپلای کن.")
        bot.edit_message_text(translate(user_id, "پیامت با موفقیت ارسال شد ✓", "Message sent ✓"),
                              call.message.chat.id, call.message.message_id)

    elif data == "chatbot":
        user_states[user_id] = {"state": "chatbot"}
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(translate(user_id, "خروج از چت‌بات", "Exit Chatbot"), callback_data="exit_chatbot"))
        bot.edit_message_text(translate(user_id,
                                        "الان می‌تونی هر سؤالی داری تایپ کنی، هوش مصنوعی جواب می‌ده 😊\nبرای خروج:",
                                        "Ask anything now, AI will answer 😊\nTo exit:"),
                              call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data == "exit_chatbot":
        if user_id in user_states:
            del user_states[user_id]
        show_main_menu(call.message)


# جمع‌آوری پیام‌ها برای پشتیبانی
@bot.message_handler(func=lambda m: True)
def collect_messages(message):
    user_id = message.from_user.id
    state = user_states.get(user_id, {}).get("state")

    if state in ["support", "reply"]:
        if "messages" not in user_states[user_id]:
            user_states[user_id]["messages"] = []
        user_states[user_id]["messages"].append(message)
        bot.reply_to(message, translate(user_id, "دریافت شد ✅", "Received ✅"))
        return True

    if state == "chatbot":
        user_message = message.text.strip()
        if not user_message:
            return

        try:
            start_time = time.time()
            completion = HF_CLIENT.chat.completions.create(
                model="zai-org/GLM-4.7:novita",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.75,
                max_tokens=2048,
                top_p=0.9
            )

            if time.time() - start_time > 45:
                bot.reply_to(message, translate(user_id,
                                                "متأسفم، پاسخ هوش مصنوعی بیش از ۴۵ ثانیه طول کشید 😔\nبعداً امتحان کن.",
                                                "Sorry, AI took longer than 45 seconds 😔\nTry later."))
                return

            answer = completion.choices[0].message.content.strip()

            if len(answer) > 4000:
                for i in range(0, len(answer), 4000):
                    bot.reply_to(message, answer[i:i+4000])
            else:
                bot.reply_to(message, answer)

        except Exception as e:
            logger.error(f"AI error: {e}")
            bot.reply_to(message, translate(user_id,
                                            "متأسفانه مشکلی در ارتباط با هوش مصنوعی پیش آمد 😅\nبعداً امتحان کن.",
                                            "Problem connecting to AI 😅\nTry later."))

    # اگر هیچ حالتی نبود، نادیده بگیر یا پیام بده
    # bot.reply_to(message, "از منوی اصلی استفاده کن یا /start بزن 😊")


# webhook برای Flask
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return "OK", 200
    abort(403)


@app.route("/")
def index():
    return "Bot is running!"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
