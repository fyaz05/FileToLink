import asyncio
from Thunder.bot import StreamBot
from Thunder.utils.database import Database
from Thunder.utils.human_readable import humanbytes
from Thunder.vars import Var
from urllib.parse import quote_plus
from pyrogram import filters, Client
from pyrogram.errors import FloodWait
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from Thunder.utils.file_properties import get_name, get_hash, get_media_file_size

# Initialize databases
db = Database(Var.DATABASE_URL, Var.name)
pass_db = Database(Var.DATABASE_URL, "ag_passwords")

async def register_user(client: Client, message: Message) -> None:
    """Register a new user if not already in the database."""
    if not await db.is_user_exist(message.from_user.id):
        await db.add_user(message.from_user.id)
        await client.send_message(
            Var.BIN_CHANNEL,
            f"👋 <b>Welcome!</b>\n✨ <b>{message.from_user.first_name}</b> has started using the bot."
        )

async def generate_links(log_msg: Message) -> tuple:
    """Generate streaming and download links with the correct format."""
    base_url = Var.URL.rstrip("/")  # Ensure no trailing slash
    file_id = log_msg.id
    file_name = quote_plus(get_name(log_msg))
    hash_value = get_hash(log_msg)
    stream_link = f"{base_url}/watch/{file_id}/{file_name}?hash={hash_value}"
    online_link = f"{base_url}/{file_id}/{file_name}?hash={hash_value}"
    return stream_link, online_link

async def check_admin_privileges(client: Client, chat_id: int) -> bool:
    """Check if the bot is an admin in the chat; skip for private chats."""
    try:
        chat = await client.get_chat(chat_id)
        if chat.type == 'private':
            return True  # Admin check not needed in private chats
        member = await client.get_chat_member(chat_id, client.me.id)
        return member.status in ("administrator", "creator")
    except Exception as e:
        print(f"Error checking admin privileges: {e}")
        return False

async def handle_flood_wait(e: FloodWait) -> None:
    """Handle FloodWait exceptions."""
    print(f"Waiting for {e.x} seconds due to FloodWait.")
    await asyncio.sleep(e.x)

@StreamBot.on_message(filters.command("link") & filters.reply)
async def link_handler(client: Client, message: Message):
    """Handle the link command when replying to a media file."""
    await register_user(client, message)  # Register the user

    reply_msg = message.reply_to_message
    if not reply_msg or not reply_msg.media:
        await message.reply_text("⚠️ Please reply to a media file to generate a link.", quote=True)
        return

    if message.chat.type in ['group', 'supergroup']:
        is_admin = await check_admin_privileges(client, message.chat.id)
        if not is_admin:
            await message.reply_text("🔒 The bot needs admin rights in this group to function properly.", quote=True)
            return

    await process_media_message(client, message, reply_msg)  # Process the media file

@StreamBot.on_message(filters.private & (filters.document | filters.video | filters.audio | filters.photo), group=4)
async def private_receive_handler(client: Client, message: Message):
    """Handle direct media uploads in private chat."""
    await register_user(client, message)  # Register the user
    await process_media_message(client, message, message)  # Process the media file

async def process_media_message(client: Client, command_message: Message, media_message: Message):
    """Process the media message and generate streaming and download links."""
    try:
        log_msg = await media_message.forward(chat_id=Var.BIN_CHANNEL)  # Forward media to log channel
        stream_link, online_link = await generate_links(log_msg)
        media_name = get_name(log_msg)
        media_size = humanbytes(get_media_file_size(media_message))

        # Create a message with the details
        msg_text = (
            "🔗 <b>Your Links are Ready!</b>\n\n"
            f"📄 <b>File Name:</b> <i>{media_name}</i>\n\n"
            f"📂 <b>File Size:</b> <i>{media_size}</i>\n\n"
            f"📥 <b>Download Link:</b>\n<code>{online_link}</code>\n\n"
            f"🖥️ <b>Watch Now:</b>\n<code>{stream_link}</code>\n\n"
            "⏰ <b>Note:</b> Links are available as long as the bot is active."
        )

        await command_message.reply_text(
            msg_text,
            quote=True,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🖥️ Watch Now", url=stream_link), 
                 InlineKeyboardButton("📥 Download", url=online_link)]
            ])
        )

        # Log information about the request
        await log_msg.reply_text(
            f"👤 <b>Requested by:</b> [{command_message.from_user.first_name}](tg://user?id={command_message.from_user.id})\n\n"
            f"🆔 <b>User ID:</b> `{command_message.from_user.id}`\n\n"
            f"📥 <b>Download Link:</b> <code>{online_link}</code>\n\n"
            f"🖥️ <b>Watch Now Link:</b> <code>{stream_link}</code>",
            disable_web_page_preview=True,
            quote=True
        )

    except FloodWait as e:
        await handle_flood_wait(e)
    except Exception as e:
        print(f"Error processing media message: {e}")
        await command_message.reply_text("❌ An error occurred. Please try again later.")

@StreamBot.on_message(filters.channel & (filters.document | filters.video | filters.photo) & ~filters.forwarded, group=-1)
async def channel_receive_handler(bot: Client, broadcast: Message):
    """Handle media shared in a channel."""
    try:
        if int(broadcast.chat.id) in Var.BANNED_CHANNELS:
            await bot.leave_chat(broadcast.chat.id)
            return

        log_msg = await broadcast.forward(chat_id=Var.BIN_CHANNEL)
        stream_link, online_link = await generate_links(log_msg)

        await log_msg.reply_text(
            f"🔘 <b>Channel:</b> `{broadcast.chat.title}`\n\n"
            f"🆔 <b>Channel ID:</b> `{broadcast.chat.id}`\n\n"
            f"📥 <b>Download Link:</b> <code>{online_link}</code>\n\n"
            f"🖥️ <b>Watch Now Link:</b> <code>{stream_link}</code>",
            quote=True
        )

        await bot.edit_message_reply_markup(
            chat_id=broadcast.chat.id,
            message_id=broadcast.id,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🖥️ Watch Now", url=stream_link),
                 InlineKeyboardButton("📥 Download", url=online_link)]
            ])
        )
    except FloodWait as e:
        await handle_flood_wait(e)
    except Exception as e:
        await bot.send_message(
            chat_id=Var.BIN_CHANNEL,
            text=f"⚠️ **Error Traceback:** `{e}`",
            disable_web_page_preview=True
        )
        print(f"Error editing broadcast message: {e}")
