
import os
import time
import random
import string
import threading
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
from flask import Flask, request
from pymongo import MongoClient
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
import requests

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
SHORTNER_API = os.getenv("SHORTNER_API")
FLASK_URL = os.getenv("FLASK_URL")
LIKE_API_URL = os.getenv("LIKE_API_URL")
PLAYER_INFO_API = os.getenv("PLAYER_INFO_API")
HOW_TO_VERIFY_URL = os.getenv("HOW_TO_VERIFY_URL")
VIP_ACCESS_URL = os.getenv("VIP_ACCESS_URL")
REQUIRED_CHANNEL = "@dg_gaming_1m0"
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.isdigit()]

# MongoDB setup
client = MongoClient(MONGO_URI)
db = client['likebot']
users = db['verifications']
profiles = db['users']

# Flask verification app
flask_app = Flask(__name__)
@flask_app.route("/verify/<code>")
def verify(code):
    user = users.find_one({"code": code})
    if user and not user.get("verified"):
        users.update_one({"code": code}, {"$set": {"verified": True, "verified_at": datetime.utcnow()}})
        return "✅ Verification successful. You may return to the bot."
    return "❌ Link expired or already used."

# Utilities
async def check_channel_membership(user_id: int, bot: Bot) -> bool:
    try:
        member = await bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

def generate_code():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=12))

# /like command
async def like_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await check_channel_membership(user.id, context.bot):
        await update.message.reply_text(f"🚫 Please join our channel {REQUIRED_CHANNEL} first.")
        return

    try:
        args = update.message.text.split()
        uid = args[2]
    except:
        await update.message.reply_text("❌ Format: /like ind <uid>")
        return

    try:
        info = requests.get(PLAYER_INFO_API.format(uid=uid), timeout=5).json()
        name = info.get("name", f"Player-{uid[-4:]}")
    except:
        name = f"Player-{uid[-4:]}"

    code = generate_code()
    short_link = requests.get(f"https://shortner.in/api?api={SHORTNER_API}&url={FLASK_URL}/verify/{code}").json().get("shortenedUrl", f"{FLASK_URL}/verify/{code}")

    users.insert_one({
        "user_id": user.id,
        "uid": uid,
        "code": code,
        "verified": False,
        "expires_at": datetime.utcnow() + timedelta(minutes=10),
        "chat_id": update.effective_chat.id,
        "message_id": update.message.message_id
    })

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ VERIFY & SEND LIKE ✅", url=short_link)],
        [InlineKeyboardButton("❓ How to Verify ❓", url=HOW_TO_VERIFY_URL)],
        [InlineKeyboardButton("🧠 PURCHASE VIP & NO VERIFY", url=VIP_ACCESS_URL)]
    ])

    msg = (
        f"🎯 *Like Request Initiated*

"
        f"🧑 Player: {name}
"
        f"🆔 UID: {uid}
"
        f"🌍 Region: IND

"
        f"⏱️ Verify within 10 minutes."
    )
    await update.message.reply_text(msg, reply_markup=keyboard, parse_mode='Markdown')

# VIP command
async def givevip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Not authorized.")
        return
    try:
        target = int(context.args[0])
    except:
        await update.message.reply_text("❌ Use: /givevip <user_id>")
        return

    profiles.update_one({"user_id": target}, {"$set": {"is_vip": True}}, upsert=True)
    await update.message.reply_text(f"✅ VIP granted to `{target}`", parse_mode='Markdown')

# Background task to process verified likes
async def process_verified_likes(app: Application):
    while True:
        pending = users.find({"verified": True, "processed": {"$ne": True}})
        for user in pending:
            uid = user['uid']
            user_id = user['user_id']
            chat_id = user['chat_id']
            msg_id = user['message_id']
            profile = profiles.find_one({"user_id": user_id}) or {}
            is_vip = profile.get("is_vip", False)
            last_used = profile.get("last_used")

            if not is_vip and last_used:
                if datetime.utcnow() - last_used < timedelta(hours=24):
                    remaining = timedelta(hours=24) - (datetime.utcnow() - last_used)
                    hours, remainder = divmod(remaining.seconds, 3600)
                    mins = remainder // 60
                    await app.bot.send_message(chat_id=chat_id, reply_to_message_id=msg_id,
                        text=f"❌ *Daily Limit Reached*

⏳ Try again after: {hours}h {mins}m", parse_mode='Markdown')
                    users.update_one({"_id": user['_id']}, {"$set": {"processed": True}})
                    continue

            try:
                api_resp = requests.get(LIKE_API_URL.format(uid=uid)).json()
                before = api_resp.get("LikesbeforeCommand", 0)
                after = api_resp.get("LikesafterCommand", 0)
                added = api_resp.get("LikesGivenByAPI", 0)
                name = api_resp.get("PlayerNickname", f"Player-{uid[-4:]}")

                if added == 0:
                    text = "❌ Like failed or daily limit reached."
                else:
                    profiles.update_one({"user_id": user_id}, {"$set": {"last_used": datetime.utcnow()}}, upsert=True)
                    text = (
                        f"✅ *Like Sent Successfully*

"
                        f"🧑 *Player:* {name}
"
                        f"🆔 *UID:* `{uid}`
"
                        f"👍 *Likes Before:* {before}
"
                        f"✨ *Likes Added:* {added}
"
                        f"🇮🇳 *Total Now:* {after}
"
                        f"🕒 *At:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
            except Exception as e:
                text = f"❌ Error processing like.

UID: `{uid}`
Error: {str(e)}"

            await app.bot.send_message(chat_id=chat_id, reply_to_message_id=msg_id, text=text, parse_mode='Markdown')
            users.update_one({"_id": user['_id']}, {"$set": {"processed": True}})
        await asyncio.sleep(5)

# Bot Runner
def run_bot():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("like", like_command))
    app.add_handler(CommandHandler("givevip", givevip_command))

    thread = threading.Thread(target=flask_app.run, kwargs={"host": "0.0.0.0", "port": 5000})
    thread.start()

    asyncio.get_event_loop().create_task(process_verified_likes(app))
    app.run_polling()

if __name__ == '__main__':
    run_bot()
