import os
import uuid
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from pymongo import MongoClient

# Environment Variables
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")

# Initialize Bot & Database
client = Client("vote_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
mongo = MongoClient(MONGO_URI)
db = mongo["vote_bot"]
votes_collection = db["votes"]

# 🟢 START Command
@client.on_message(filters.command("start"))
async def start(_, message: Message):
    await message.reply_text(
        "**👋 Welcome to the Vote Bot!**\n\n"
        "Use /vote to create a voting post in your channel.\n"
        "Only channel subscribers can vote. Leaving the channel after voting will show ❌ Left.\n\n"
        "__Make sure the bot is admin in your channel!__"
    )

@client.on_message(filters.command("vote"))
async def vote_command(_, message: Message):
    await message.reply("📢 Send me your **channel username** or **invite link** (without @):")

    response = await client.ask(message.chat.id, timeout=60)  # ✅ FIXED (listen -> ask)
    if not response:
        return await message.reply("❌ You didn't respond in time.")

    channel_input = response.text.strip().replace("@", "")
    try:
        chat = await client.get_chat(channel_input)
        member = await client.get_chat_member(chat.id, "me")
        if not member.can_post_messages:
            return await message.reply("❌ I must be an admin in that channel with post rights.")
    except:
        return await message.reply("❌ Invalid channel or I'm not an admin there.")

    vote_id = str(uuid.uuid4())[:8]
    vote_data = {
        "vote_id": vote_id,
        "channel_id": chat.id,
        "creator_id": message.from_user.id,
        "created_at": datetime.utcnow(),
        "votes": {},  # user_id: {name, username}
        "left_users": []
    }
    votes_collection.insert_one(vote_data)

    vote_link = f"https://t.me/{client.me.username}?start={vote_id}"
    await message.reply(f"✅ Vote link created:\n`{vote_link}`")

    buttons = [
        [InlineKeyboardButton("👍 Vote", callback_data=f"vote_{vote_id}")],
        [InlineKeyboardButton("❌ Left: 0", callback_data="noop")]
    ]
    await client.send_message(
        chat.id,
        f"🗳️ **New Vote Started!**\n\nUse the link to vote.\nOnly channel subscribers can vote.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# 🎫 Handling Start with Vote Link
@client.on_message(filters.command("start") & filters.private)
async def handle_vote_link(_, message: Message):
    parts = message.text.strip().split()
    if len(parts) != 2:
        return

    vote_id = parts[1]
    vote = votes_collection.find_one({"vote_id": vote_id})
    if not vote:
        return await message.reply("❌ Invalid vote link.")

    user_id = message.from_user.id
    channel_id = vote["channel_id"]

    try:
        member = await client.get_chat_member(channel_id, user_id)
        if member.status in ["left", "kicked"]:
            return await message.reply("🚫 You must join the channel to vote.")
    except:
        return await message.reply("🚫 You must join the channel to vote.")

    if str(user_id) in vote["votes"]:
        return await message.reply("✅ You have already voted!")

    vote["votes"][str(user_id)] = {
        "name": message.from_user.first_name,
        "username": message.from_user.username or "",
        "user_id": user_id
    }
    votes_collection.update_one({"vote_id": vote_id}, {"$set": {"votes": vote["votes"]}})

    await message.reply("🗳️ Thank you for voting!")

# ✅ Inline Vote Button
@client.on_callback_query(filters.regex(r"^vote_"))
async def handle_vote_button(_, query: CallbackQuery):
    vote_id = query.data.split("_")[1]
    vote = votes_collection.find_one({"vote_id": vote_id})
    if not vote:
        return await query.answer("Invalid vote ID.", show_alert=True)

    user_id = query.from_user.id
    channel_id = vote["channel_id"]

    try:
        member = await client.get_chat_member(channel_id, user_id)
        if member.status in ["left", "kicked"]:
            return await query.answer("❌ Only subscribers can vote!", show_alert=True)
    except:
        return await query.answer("❌ Only subscribers can vote!", show_alert=True)

    if str(user_id) in vote["votes"]:
        return await query.answer("✅ You already voted!", show_alert=True)

    vote["votes"][str(user_id)] = {
        "name": query.from_user.first_name,
        "username": query.from_user.username or "",
        "user_id": user_id
    }
    votes_collection.update_one({"vote_id": vote_id}, {"$set": {"votes": vote["votes"]}})

    await query.answer("✅ Vote counted!")

# ❌ Detect Leavers and Update Left Count
@client.on_chat_member_updated()
async def detect_leavers(_, member_update):
    if member_update.old_chat_member and member_update.old_chat_member.status == "member":
        if member_update.new_chat_member.status == "left":
            user_id = member_update.from_user.id
            channel_id = member_update.chat.id

            vote = votes_collection.find_one({
                "channel_id": channel_id,
                f"votes.{user_id}": {"$exists": True}
            })

            if vote and str(user_id) not in vote.get("left_users", []):
                vote["left_users"].append(str(user_id))
                votes_collection.update_one({"vote_id": vote["vote_id"]}, {"$set": {"left_users": vote["left_users"]}})

client.run()
