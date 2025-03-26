from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import ChatAdminRequired
import asyncio
import uuid
import os

API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

app = Client("vote_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

vote_data = {}  # vote_id -> {"channel": str, "message_id": int, "voters": {}, "left": set()}


@app.on_message(filters.command("start"))
async def start(client, message: Message):
    await message.reply_text(
        f"👋 Hello {message.from_user.first_name}!\n\n"
        "Welcome to the *Vote Bot*! 🗳️\n\n"
        "How to use:\n"
        "1️⃣ Use /vote to create a new giveaway.\n"
        "2️⃣ Share the vote link.\n"
        "3️⃣ Only channel subscribers can vote.\n"
        "4️⃣ If a voter leaves, ❌ Left count increases.\n\n"
        "Bot must be admin in the channel.",
        parse_mode="markdown"
    )


@app.on_message(filters.command("vote"))
async def vote_command(client, message: Message):
    await message.reply_text("📢 Please send your *channel username or invite link* (e.g. @mychannel or https://t.me/mychannel):", parse_mode="markdown")

    try:
        reply = await app.listen(message.chat.id, timeout=60)
    except asyncio.TimeoutError:
        return await message.reply_text("⏰ Time's up! Please send the command again.")

    channel_input = reply.text.strip()

    try:
        chat = await app.get_chat(channel_input)
        member = await app.get_chat_member(chat.id, "me")

        if not member.can_post_messages:
            return await message.reply_text("❌ Bot is not an *admin* in the channel. Please make me admin and try again.")

    except Exception as e:
        return await message.reply_text("⚠️ Couldn't access the channel. Make sure the username/link is correct and bot is an admin.")

    # Create vote data
    vote_id = str(uuid.uuid4())[:8]
    vote_data[vote_id] = {
        "channel": chat.id,
        "message_id": None,
        "voters": {},  # user_id -> user info
        "left": set()
    }

    # Prepare vote post
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Vote", callback_data=f"vote_{vote_id}")],
        [InlineKeyboardButton("❌ Left: 0", callback_data="no_action")]
    ])

    text = "**🎉 Giveaway Voting Started!**\n\n👤 Voters: 0\n❌ Left: 0"
    sent = await app.send_message(chat.id, text, reply_markup=buttons)

    vote_data[vote_id]["message_id"] = sent.id

    await message.reply_text(f"✅ Vote started!\n🔗 Share this link: `https://t.me/{app.me.username}?start={vote_id}`", parse_mode="markdown")


@app.on_message(filters.regex(r"^/start (\w+)$"))
async def handle_vote_link(client, message: Message):
    vote_id = message.matches[0].group(1)

    if vote_id not in vote_data:
        return await message.reply_text("⚠️ Invalid or expired vote link.")

    chat_id = vote_data[vote_id]["channel"]

    try:
        member = await app.get_chat_member(chat_id, message.from_user.id)
        if member.status in ("left", "kicked"):
            raise ValueError
    except:
        return await message.reply_text("🚫 You must *join the channel* to vote.", parse_mode="markdown")

    if message.from_user.id in vote_data[vote_id]["voters"]:
        return await message.reply_text("✅ You have already voted!")

    # Add voter
    user_info = {
        "name": message.from_user.first_name,
        "username": message.from_user.username or "N/A",
        "user_id": message.from_user.id
    }
    vote_data[vote_id]["voters"][message.from_user.id] = user_info

    # Update original message
    voters = len(vote_data[vote_id]["voters"])
    left = len(vote_data[vote_id]["left"])

    voter_list = "\n".join([f"👤 [{v['name']}](tg://user?id={v['user_id']}) (`{v['user_id']}`)" for v in vote_data[vote_id]["voters"].values()])
    text = f"**🎉 Giveaway Voting Started!**\n\n👤 Voters: {voters}\n❌ Left: {left}\n\n{voter_list}"

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Vote", callback_data=f"vote_{vote_id}")],
        [InlineKeyboardButton(f"❌ Left: {left}", callback_data="no_action")]
    ])

    try:
        await client.edit_message_text(chat_id, vote_data[vote_id]["message_id"], text, reply_markup=buttons, parse_mode="markdown")
    except:
        pass

    await message.reply_text("🗳️ Your vote has been recorded. Thank you!")


@app.on_chat_member_updated()
async def detect_leavers(client, event):
    for vote_id, data in vote_data.items():
        if event.chat.id != data["channel"]:
            continue

        if event.new_chat_member.status == "left":
            user_id = event.new_chat_member.user.id

            if user_id in data["voters"] and user_id not in data["left"]:
                data["left"].add(user_id)

                # Update message
                voters = len(data["voters"])
                left = len(data["left"])
                voter_list = "\n".join([f"👤 [{v['name']}](tg://user?id={v['user_id']}) (`{v['user_id']}`)" for v in data["voters"].values()])
                text = f"**🎉 Giveaway Voting Started!**\n\n👤 Voters: {voters}\n❌ Left: {left}\n\n{voter_list}"

                buttons = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Vote", callback_data=f"vote_{vote_id}")],
                    [InlineKeyboardButton(f"❌ Left: {left}", callback_data="no_action")]
                ])

                try:
                    await client.edit_message_text(data["channel"], data["message_id"], text, reply_markup=buttons, parse_mode="markdown")
                except:
                    pass


@app.on_callback_query(filters.regex("no_action"))
async def no_action(_, query: CallbackQuery):
    await query.answer("This button is not clickable!", show_alert=True)


if __name__ == "__main__":
    print("Bot started...")
    app.run()
