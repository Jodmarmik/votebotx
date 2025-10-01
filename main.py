#!/usr/bin/env python3
import os
import logging
import asyncio
from typing import Optional

from pymongo import MongoClient
from pymongo.errors import CollectionInvalid
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MessageEntity,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables (Heroku config vars)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = os.environ.get("API_ID")        # optional, provided because you asked for api_id/hash
API_HASH = os.environ.get("API_HASH")    # optional
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))  # the owner Telegram user id (int)
MONGO_URI = os.environ.get("MONGO_URI")
SUPPORT_GROUP_URL = os.environ.get("SUPPORT_GROUP_URL", "https://t.me/your_support_group")
SUPPORT_CHAT_URL = os.environ.get("SUPPORT_CHAT_URL", "https://t.me/your_support_chat")
DEFAULT_DELAY = int(os.environ.get("DEFAULT_DELAY", "300"))  # seconds (default 5 minutes)

if not BOT_TOKEN or not MONGO_URI:
    logger.error("BOT_TOKEN and MONGO_URI environment variables are required.")
    raise SystemExit("BOT_TOKEN and MONGO_URI required")

# Mongo setup
mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = mongo.get_database("media_deleter_bot_db")
groups_col = db.get_collection("groups")  # stores { chat_id: int, enabled: bool, delay: int }

# Helpers
def get_group_settings(chat_id: int) -> dict:
    doc = groups_col.find_one({"chat_id": chat_id})
    if not doc:
        doc = {"chat_id": chat_id, "enabled": True, "delay": DEFAULT_DELAY}
        groups_col.insert_one(doc)
    return doc

def set_group_enabled(chat_id: int, enabled: bool):
    groups_col.update_one({"chat_id": chat_id}, {"$set": {"enabled": enabled}}, upsert=True)

def set_group_delay(chat_id: int, delay: int):
    groups_col.update_one({"chat_id": chat_id}, {"$set": {"delay": delay}}, upsert=True)

async def is_user_admin(app, chat_id: int, user_id: int) -> bool:
    try:
        member = await app.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in ("administrator", "creator")
    except Exception as e:
        logger.warning("is_user_admin check failed: %s", e)
        return False

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Support Group", url=SUPPORT_GROUP_URL),
                InlineKeyboardButton("Support Chat", url=SUPPORT_CHAT_URL),
            ],
        ]
    )
    text = (
        f"Hi {user.first_name if user else 'there'}!\n\n"
        "I'm a Media Deleter Bot: I can delete media in groups after a set delay (default 5 minutes).\n\n"
        "• Add me to your group and give me the *Delete messages* permission.\n"
        "• In group, use /mediaoff or /mediaon (admins only) to disable/enable deletion.\n\n"
        "Owner-only commands: /broadcast (owner).\n"
    )
    await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def media_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == "private":
        return await update.message.reply_text("This command works in groups only.")
    if not await is_user_admin(context.application, chat.id, user.id):
        return await update.message.reply_text("Only group admins can use this command.")
    set_group_enabled(chat.id, True)
    await update.message.reply_text("✅ Media auto-deletion is now *ENABLED* for this group.", parse_mode=ParseMode.MARKDOWN)

async def media_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == "private":
        return await update.message.reply_text("This command works in groups only.")
    if not await is_user_admin(context.application, chat.id, user.id):
        return await update.message.reply_text("Only group admins can use this command.")
    set_group_enabled(chat.id, False)
    await update.message.reply_text("⛔️ Media auto-deletion is now *DISABLED* for this group.", parse_mode=ParseMode.MARKDOWN)

# Optional: set delay (admins only). Not required but convenient.
async def setdelay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == "private":
        return await update.message.reply_text("This command works in groups only.")
    if not await is_user_admin(context.application, chat.id, user.id):
        return await update.message.reply_text("Only group admins can use this command.")
    if not context.args:
        return await update.message.reply_text("Usage: /setdelay <seconds>  (e.g. /setdelay 300 )")
    try:
        delay = int(context.args[0])
        if delay < 10 or delay > 86400:
            return await update.message.reply_text("Please provide delay between 10 and 86400 seconds.")
        set_group_delay(chat.id, delay)
        await update.message.reply_text(f"✅ Deletion delay set to {delay} seconds.")
    except ValueError:
        await update.message.reply_text("Provide an integer number of seconds, e.g. /setdelay 300")

# Message handler for media - schedule deletion
async def media_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    if chat is None or message is None:
        return
    # Only act in groups/supergroups
    if chat.type not in ("group", "supergroup"):
        return

    settings = get_group_settings(chat.id)
    if not settings.get("enabled", True):
        logger.debug("Media deletion disabled for chat %s", chat.id)
        return

    # If message has any media types, schedule deletion
    # Check message contains any media-like attributes
    has_media = any(
        [
            message.photo,
            message.video,
            message.document,
            message.sticker,
            message.voice,
            message.audio,
            message.animation,
            message.video_note,
        ]
    )
    if not has_media:
        return

    delay = int(settings.get("delay", DEFAULT_DELAY))

    # Capture identifiers needed in job
    chat_id = chat.id
    message_id = message.message_id

    logger.info("Scheduling deletion of message %s in chat %s after %s seconds", message_id, chat_id, delay)

    # Schedule a Job to delete this message after `delay` seconds
    context.job_queue.run_once(
        delete_media_job,
        when=delay,
        data={"chat_id": chat_id, "message_id": message_id},
        name=f"del-{chat_id}-{message_id}",
    )

async def delete_media_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    data = job.data or {}
    chat_id = data.get("chat_id")
    message_id = data.get("message_id")
    if not chat_id or not message_id:
        return
    try:
        await context.application.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info("Deleted message %s in chat %s", message_id, chat_id)
    except Exception as e:
        logger.warning("Failed to delete message %s in chat %s: %s", message_id, chat_id, e)
        # Message might be already deleted or bot lacks permissions; continue.

    # After attempting to delete, send confirmation message to the group.
    try:
        await context.application.bot.send_message(chat_id=chat_id, text="All media cleared — I am doing my job properly.")
    except Exception as e:
        logger.warning("Failed to send confirmation message to chat %s: %s", chat_id, e)

# owner-only broadcast
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user is None or user.id != OWNER_ID:
        return await update.message.reply_text("Only the owner can use /broadcast.")
    text = ""
    # Support both: /broadcast <text> or reply to message with /broadcast
    if context.args:
        text = " ".join(context.args)
    elif update.message.reply_to_message:
        # forward the replied message to all chats
        text = None
    else:
        return await update.message.reply_text("Usage: /broadcast <message> OR reply to a message with /broadcast")

    # Build list of target chats from DB
    cursor = groups_col.find({})
    # Keep a list to avoid duplicates
    chats = [d["chat_id"] for d in cursor]

    sent = 0
    failed = 0
    await update.message.reply_text(f"Broadcasting to {len(chats)} chats... (this may take a while)")
    for chat_id in chats:
        try:
            if text is None:
                # forward replied message
                await context.application.bot.forward_message(chat_id=chat_id, from_chat_id=update.effective_chat.id, message_id=update.message.reply_to_message.message_id)
            else:
                await context.application.bot.send_message(chat_id=chat_id, text=text)
            sent += 1
            await asyncio.sleep(0.2)  # small pause to avoid hitting limits
        except Exception as e:
            logger.warning("Failed to broadcast to %s: %s", chat_id, e)
            failed += 1
            await asyncio.sleep(0.4)

    await update.message.reply_text(f"Broadcast finished. Sent: {sent}. Failed: {failed}.")

# Keep chat id when bot sees join or when people use it privately to collect targets (optional)
async def collect_chat_on_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # When the bot is added to a group, ensure a settings doc exists
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        get_group_settings(chat.id)
        logger.info("Bot added to chat %s, default settings created", chat.id)

# Bot startup
def build_app():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mediaon", media_on))
    app.add_handler(CommandHandler("mediaoff", media_off))
    app.add_handler(CommandHandler("setdelay", setdelay))
    app.add_handler(CommandHandler("broadcast", broadcast))

    # When bot added to group
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, collect_chat_on_join))

    # Catch media messages
    media_filter = (
        filters.PHOTO
        | filters.VIDEO
        | filters.VIDEO_NOTE
        | filters.ANIMATION
        | filters.DOCUMENT
        | filters.STICKER
        | filters.AUDIO
        | filters.VOICE
    )
    app.add_handler(MessageHandler(media_filter & ~filters.COMMAND, media_message_handler))

    return app

if __name__ == "__main__":
    app = build_app()
    logger.info("Starting Media Deleter Bot...")
    app.run_polling()
