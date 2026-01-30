import logging
import os
import asyncio
from dotenv import load_dotenv
from flask import Flask, request, abort
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.error import BadRequest
from openai import OpenAI, OpenAIError, APIError, APITimeoutError
import psycopg2
from psycopg2.extras import RealDictCursor

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPPORT_ID = int(os.getenv("SUPPORT_ID"))
CHANNEL_ID = os.getenv("CHANNEL_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
HF_API_KEY = os.getenv("HF_API_KEY")

BASE_URL = "https://tapi.bale.ai/bot"

if not BOT_TOKEN or not DATABASE_URL:
    raise ValueError("BOT_TOKEN یا DATABASE_URL در .env تعریف نشده است")

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

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

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

def get_user_language(user_id):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT language FROM users WHERE user_id = %s', (user_id,))
            result = cur.fetchone()
            return result['language'] if result else 'fa'

def translate(user_id, fa_text, en_text):
    lang = get_user_language(user_id)
    return fa_text if lang == 'fa' else en_text

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

user_states = {}

app = Flask(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    save_or_update_user(
        user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )

    if get_user_language(user_id) in ['fa', 'en']:
        await check_membership(update, context)
        return

    text = translate(user_id,
                     "لطفاً زبان خود را انتخاب کنید:",
                     "Please choose your language:")

    keyboard = [
        [InlineKeyboardButton("فارسی", callback_data="lang_fa"),
         InlineKeyboardButton("English", callback_data="lang_en")]
    ]

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query if update.callback_query else None
    user_id = update.effective_user.id if not query else query.from_user.id

    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        if member.status in ["member", "administrator", "creator"]:
            await show_main_menu(update, context)
            return
    except Exception as e:
        logger.error(f"خطا چک عضویت: {e}")

    text = translate(user_id,
                     "لطفاً برای استفاده از ربات در کانال زیر عضو شوید:",
                     "Please join the channel below to use the bot:")

    keyboard = [
        [InlineKeyboardButton("کانال هوش مصنوعی", url=f"https://t.me/aibotchannel")],
        [InlineKeyboardButton(translate(user_id, "ثبت و بررسی عضویت", "Check Membership"), callback_data="check_join")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        if query:
            await query.edit_message_text(text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(text, reply_markup=reply_markup)
    except BadRequest:
        if query:
            await query.message.reply_text(text, reply_markup=reply_markup)


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query if update.callback_query else None
    user_id = update.effective_user.id if not query else query.from_user.id

    text = translate(user_id,
                     "سلام! 👋\nبه ربات هوشمند خوش آمدید!\nهر سؤالی داری، بگو تا کمکت کنم 🚀",
                     "Hi! 👋\nWelcome to the smart bot!\nAsk anything, I'm here to help 🚀")

    keyboard = [
        [InlineKeyboardButton(translate(user_id, "چت‌بات", "Chat Bot"), callback_data="chatbot")],
        [InlineKeyboardButton(translate(user_id, "پشتیبانی", "Support"), callback_data="support_open")],
        [InlineKeyboardButton(translate(user_id, "درباره ربات", "About Bot"), callback_data="about_bot")],
        [InlineKeyboardButton(translate(user_id, "تغییر زبان", "Change Language"), callback_data="change_lang")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        if query:
            await query.edit_message_text(text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(text, reply_markup=reply_markup)
    except BadRequest:
        if query:
            await query.message.reply_text(text, reply_markup=reply_markup)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except BadRequest:
        pass

    user_id = query.from_user.id
    data = query.data

    try:
        if data in ["lang_fa", "lang_en"]:
            lang = "fa" if data == "lang_fa" else "en"
            save_or_update_user(user_id, language=lang)
            await check_membership(update, context)

        elif data == "change_lang":
            text = translate(user_id, "زبان جدید را انتخاب کنید:", "Choose new language:")
            keyboard = [
                [InlineKeyboardButton("فارسی", callback_data="lang_fa"),
                 InlineKeyboardButton("English", callback_data="lang_en")]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "check_join":
            await check_membership(update, context)

        elif data == "about_bot":
            text = translate(user_id,
                             "🤖 این ربات با هوش مصنوعی واقعی کار می‌کنه و می‌تونه به هر سؤالی جواب بده.\nبا لحن دوستانه و صمیمی باهات حرف می‌زنه 😊",
                             "🤖 This bot uses real AI and can answer any question.\nFriendly and warm tone 😊")
            keyboard = [[InlineKeyboardButton(translate(user_id, "بازگشت", "Back"), callback_data="main_menu")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "main_menu":
            await show_main_menu(update, context)

        elif data == "support_open":
            user_states[user_id] = {"state": "support", "messages": []}
            text = translate(user_id,
                             "هر چی می‌خوای به پشتیبانی بفرستی همین‌جا بفرست.\nوقتی تموم شد دکمه زیر رو بزن:",
                             "Send anything to support here.\nWhen done click below:")
            keyboard = [
                [InlineKeyboardButton(translate(user_id, "ارسال به پشتیبانی", "Send to Support"), callback_data="send_support")],
                [InlineKeyboardButton(translate(user_id, "برگشت به منو", "Back to Menu"), callback_data="main_menu")]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "send_support":
            if user_id not in user_states or not user_states[user_id].get("messages"):
                await query.edit_message_text(translate(user_id, "هیچ پیامی برای ارسال وجود ندارد!", "No message to send!"))
                return

            messages = user_states[user_id]["messages"]
            del user_states[user_id]

            header = f"پیام جدید از کاربر {user_id} (@{query.from_user.username or 'ندارد'})"

            await context.bot.send_message(SUPPORT_ID, header)
            for msg in messages:
                await msg.forward(SUPPORT_ID)

            await context.bot.send_message(
                SUPPORT_ID,
                "برای پاسخ مستقیم ریپلای کن.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("ارسال پاسخ", callback_data=f"reply_support_{user_id}")]
                ])
            )

            await query.edit_message_text(
                translate(user_id, "پیامت با موفقیت ارسال شد ✓", "Message sent ✓")
            )

        elif data.startswith("reply_support_"):
            target_user_id = int(data.split("_")[-1])
            user_states[SUPPORT_ID] = {"state": "reply", "target_user": target_user_id, "messages": []}
            text = translate(SUPPORT_ID,
                             f"در حال پاسخ به کاربر {target_user_id}\nپیام خود را به پیام کاربر ریپلای کنید:",
                             f"Replying to user {target_user_id}\nWrite your message:")
            keyboard = [[InlineKeyboardButton(translate(SUPPORT_ID, "ارسال پاسخ", "Send Reply"), callback_data=f"send_reply_{target_user_id}")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

        elif data.startswith("send_reply_"):
            target_user_id = int(data.split("_")[-1])
            if SUPPORT_ID not in user_states or not user_states[SUPPORT_ID].get("messages"):
                await query.edit_message_text("هیچ پاسخی برای ارسال وجود ندارد!")
                return

            messages = user_states[SUPPORT_ID]["messages"]
            del user_states[SUPPORT_ID]

            for msg in messages:
                await msg.forward(target_user_id)

            await query.edit_message_text(translate(SUPPORT_ID, "پاسخ ارسال شد.", "Reply sent."))
            await context.bot.send_message(target_user_id, translate(target_user_id, "پاسخ پشتیبانی دریافت شد ✓", "Support reply received ✓"))

        elif data == "chatbot":
            user_states[user_id] = {"state": "chatbot"}
            text = translate(user_id,
                             "الان می‌تونی هر سؤالی داری تایپ کنی، هوش مصنوعی جواب می‌ده 😊\nبرای خروج:",
                             "Ask anything now, AI will answer 😊\nTo exit:")
            keyboard = [[InlineKeyboardButton(translate(user_id, "خروج از چت‌بات", "Exit Chatbot"), callback_data="exit_chatbot")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "exit_chatbot":
            if user_id in user_states:
                del user_states[user_id]
            await show_main_menu(update, context)

    except BadRequest as e:
        if "query is too old" in str(e).lower():
            await query.message.reply_text(translate(user_id,
                                                    "دکمه قدیمی شده، لطفاً دوباره /start بزنید 😅",
                                                    "Button is old, please /start again 😅"))
        else:
            logger.error(f"BadRequest: {e}")
    except Exception as e:
        logger.error(f"Error in button_handler: {e}")
        await query.message.reply_text(translate(user_id,
                                                "یه مشکلی پیش اومد... دوباره امتحان کن 😅",
                                                "Something went wrong... Try again 😅"))


async def collect_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = user_states.get(user_id, {}).get("state")

    if state in ["support", "reply"]:
        messages = user_states[user_id].get("messages", [])
        if update.message.message_id not in [m.message_id for m in messages]:
            user_states[user_id]["messages"].append(update.message)
            await update.message.reply_text(translate(user_id, "دریافت شد ✅", "Received ✅"), quote=False)
        return True
    return False


async def ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_states.get(user_id, {}).get("state") != "chatbot":
        await collect_messages(update, context)
        return

    user_message = update.message.text.strip()
    if not user_message:
        return

    try:
        async with asyncio.timeout(45):
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

        answer = completion.choices[0].message.content.strip()

        if len(answer) > 4000:
            parts = [answer[i:i+4000] for i in range(0, len(answer), 4000)]
            for part in parts:
                await update.message.reply_text(part, quote=False)
        else:
            await update.message.reply_text(answer, quote=False)

    except asyncio.TimeoutError:
        await update.message.reply_text(
            translate(user_id,
                      "متأسفم، پاسخ هوش مصنوعی بیش از ۴۵ ثانیه طول کشید 😔\nلطفاً بعداً دوباره امتحان کنید.",
                      "Sorry, AI response took longer than 45 seconds 😔\nPlease try again later.")
        )
    except (APIError, APITimeoutError, OpenAIError) as e:
        logger.error(f"API error: {e}")
        await update.message.reply_text(
            translate(user_id,
                      "متأسفانه مشکلی در ارتباط با هوش مصنوعی پیش آمد 😅\nبعداً دوباره امتحان کن.",
                      "Unfortunately there was a problem connecting to AI 😅\nTry again later.")
        )
    except Exception as e:
        logger.error(f"General error in ai_chat: {e}")
        await update.message.reply_text(
            translate(user_id,
                      "یه اتفاق غیرمنتظره افتاد... دوباره بگو ببینم چی بود؟ 😅",
                      "Something unexpected happened... Tell me again? 😅")
        )


@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data().decode("utf-8")
        update = Update.de_json(json_string, application.bot)
        application.update_queue.put_nowait(update)
        return "OK", 200
    abort(403)


@app.route("/")
def index():
    return "Bot is running!"


if __name__ == "__main__":
    application = ApplicationBuilder() \
        .token(BOT_TOKEN) \
        .base_url(BASE_URL) \
        .base_file_url("https://tapi.bale.ai/file/bot") \
        .job_queue(None) \
        .build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, collect_messages), group=0)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_chat), group=1)

    webhook_url = f"https://your-leapcell-app.leapcell.dev/{BOT_TOKEN}"
    application.bot.set_webhook(url=webhook_url)

    print("Webhook set and server starting...")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))