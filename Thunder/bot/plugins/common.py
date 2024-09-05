import logging
import time
from hydrogram import Client, filters
from hydrogram.types import Message
from Thunder.bot import StreamBot
from Thunder.vars import Var
from Thunder.utils import human_readable, database

logger = logging.getLogger(__name__)

# Initialize the database
db = database.Database(Var.DATABASE_URL, Var.name)

async def log_new_user(bot: Client, user_id: int, first_name: str):
    """Log new user and send notification if user is new."""
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id)
        await bot.send_message(
            Var.BIN_CHANNEL,
            f"#NEW_USER: \n\nNew User [{first_name}](tg://user?id={user_id}) has started the bot!"
        )

def extract_file_info(message: Message):
    """Extract file information like name and size from the message."""
    media = message.video or message.document or message.audio
    if media:
        return media.file_name, human_readable.humanbytes(media.file_size)
    return None, None

def create_stream_link(msg_id: int):
    """Generate a stream link based on configuration."""
    base_link = f"{Var.FQDN}/{msg_id}"
    return f"https://{base_link}" if Var.ON_HEROKU or Var.NO_PORT else f"http://{Var.FQDN}:{Var.PORT}/{base_link}"

@StreamBot.on_message(filters.command("start") & filters.private)
async def start_command(bot: Client, message: Message):
    """Handle /start command."""
    await log_new_user(bot, message.from_user.id, message.from_user.first_name)
    args = message.text.strip().split("_")

    if len(args) == 1 or args[-1].lower() == "start":
        await message.reply_text(
            text=(
                "**Welcome to the File to Link Bot!**\n\n"
                "I can convert files into links for you to share easily.\n"
                "Send me a file or use the commands for more options.\n"
                "Type /help to see what I can do for you!"
            )
        )
    else:
        msg_id = int(args[-1])
        get_msg = await bot.get_messages(chat_id=Var.BIN_CHANNEL, message_ids=msg_id)
        file_name, file_size = extract_file_info(get_msg)
        stream_link = create_stream_link(get_msg.id)

        if file_name and file_size:
            await message.reply_text(
                text=(
                    f"**Link Generated! ⚡**\n\n"
                    f"📧 **File Name:** {file_name}\n"
                    f"📦 **File Size:** {file_size}\n\n"
                    f"💌 [Download Link]({stream_link})\n\n♻️ This link will work till the bot is active. ♻️"
                )
            )

@StreamBot.on_message(filters.command("help") & filters.private)
async def help_command(bot: Client, message: Message):
    """Handle /help command."""
    await log_new_user(bot, message.from_user.id, message.from_user.first_name)
    help_caption = (
        "**How to use File to Link Bot!**\n"
        "🔹 Send any file or video to generate a shareable link.\n"
        "🔹 Use the link for easy downloading or streaming.\n"
        "🔹 For channel posts: Add me to your channel to generate links automatically for each post.\n"
        "🔹 Type /about to learn more about this bot.\n"
        "Enjoy using the bot and feel free to share your feedback!"
    )
    await message.reply_photo(
        photo="https://cdn.jsdelivr.net/gh/fyaz05/Resources@main/FileToLink/Welcome.png",
        caption=help_caption
    )

@StreamBot.on_message(filters.command("about") & filters.private)
async def about_command(bot: Client, message: Message):
    """Handle /about command."""
    await log_new_user(bot, message.from_user.id, message.from_user.first_name)
    about_caption = (
        "<b>About File to Link Bot</b>\n\n"
        "🔸 <b>Bot Name:</b> File to Link Bot\n"
        "🔸 <b>Description:</b> This bot converts your files into direct download and stream links.\n"
        "🔸 <b>Usage:</b> Send a file to receive a direct link.\n"
        "🔸 Join our Telegram for updates and support.\n"
    )
    await message.reply_photo(
        photo="https://cdn.jsdelivr.net/gh/fyaz05/Resources@main/FileToLink/Welcome.png",
        caption=about_caption
    )

@StreamBot.on_message(filters.command("dc") & filters.private)
async def dc_command(bot: Client, message: Message):
    """Handle DC command."""
    dc_text = f"Your Telegram DC is: `{message.from_user.dc_id}`"
    await message.reply_text(dc_text, disable_web_page_preview=True, quote=True)

@StreamBot.on_message(filters.command("ping") & filters.private)
async def ping_command(bot: Client, message: Message):
    """Handle ping command."""
    start_time = time.time()
    response = await message.reply_text("....")
    end_time = time.time()
    time_taken_ms = (end_time - start_time) * 1000
    await response.edit(f"Pong!\n{time_taken_ms:.3f} ms")
