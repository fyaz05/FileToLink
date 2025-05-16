"""
Thunder/bot/plugins/common.py - Common plugin handlers and helpers for Thunder bot.
"""

import time
import asyncio
from typing import Tuple, Optional
from urllib.parse import quote_plus

from pyrogram import Client, filters
from Thunder.utils.decorators import check_banned
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
    LinkPreviewOptions,
)

from Thunder.bot import StreamBot
from Thunder.vars import Var
from Thunder.utils.database import Database
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.file_properties import get_hash, get_media_file_size, get_name
from Thunder.utils.force_channel import force_channel_check
from Thunder.utils.logger import logger
from Thunder.utils.tokens import get, check, validate_activation_token

# DB connection
db = Database(Var.DATABASE_URL, Var.NAME)

# Error messages
ERROR_MSG = "🚨 **An unexpected error occurred.**"
INVALID_ARG_MSG = "❌ **Invalid argument.** Please provide a valid Telegram User ID or username."
FAILED_USER_INFO_MSG = "❌ **Failed to retrieve user information.**"
INVALID_FILE_MSG = "❌ **Invalid file identifier provided.**"

# ====== HELPER FUNCTIONS ======

# Send notification to monitoring channel
async def notify_channel(bot: Client, text: str):
    if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
        try:
            await bot.send_message(chat_id=Var.BIN_CHANNEL, text=text)
        except Exception:
            pass

# Handle user error messages consistently
async def handle_user_error(message: Message, error_msg: str):
    try:
        await message.reply_text(error_msg, quote=True)
    except Exception:
        pass

# Log new users and notify monitoring channel
async def log_new_user(bot: Client, user_id: int, first_name: str):
    try:
        if not await db.is_user_exist(user_id):
            await db.add_user(user_id)
            if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
                await bot.send_message(
                    Var.BIN_CHANNEL,
                    f"👋 **New User Alert!**\n\n"
                    f"✨ **Name:** [{first_name}](tg://user?id={user_id})\n"
                    f"🆔 **User ID:** `{user_id}`"
                )
    except Exception:
        pass

# Generate stream and download links for media files
async def generate_media_links(log_msg: Message) -> Tuple[str, str]:
    try:
        base_url = Var.URL.rstrip("/")
        file_id = log_msg.id

        # Process file name
        file_name = get_name(log_msg)
        if isinstance(file_name, bytes):
            file_name = file_name.decode('utf-8', errors='replace')
        else:
            file_name = str(file_name)
        file_name_encoded = quote_plus(file_name)

        # Generate links
        hash_value = get_hash(log_msg)
        stream_link = f"{base_url}/watch/{file_id}/{file_name_encoded}?hash={hash_value}"
        download_link = f"{base_url}/{file_id}/{file_name_encoded}?hash={hash_value}"
        
        return stream_link, download_link
    except Exception as e:
        logger.error(f"Error generating media links: {e}")
        await notify_channel(log_msg._client._client, f"Error generating media links: {e}")
        # Return fallback empty links if generation fails
        return "", ""

# Generate formatted DC text for user information
async def generate_dc_text(user: User) -> str:
    dc_id = user.dc_id if user.dc_id is not None else "Unknown"
    return (
        f"🌐 **Data Center Information**\n\n"
        f"👤 **User:** [{user.first_name or 'User'}](tg://user?id={user.id})\n"
        f"🆔 **User ID:** `{user.id}`\n"
        f"🌐 **Data Center:** `{dc_id}`"
    )

# Get user object safely from username or ID
async def get_user_safely(bot: Client, query) -> Optional[User]:
    try:
        if isinstance(query, str):
            if query.startswith('@'):
                return await bot.get_users(query)
            elif query.isdigit():
                return await bot.get_users(int(query))
        elif isinstance(query, int):
            return await bot.get_users(query)
        return None
    except Exception:
        return None

# Function to get file ID from message containing media
def get_file_id_from_message(file_msg: Message) -> Optional[str]:
    if file_msg.document:
        return file_msg.document.file_id
    elif file_msg.photo:
        # Photos have sizes array, get the largest one
        return file_msg.photo.file_id if hasattr(file_msg.photo, "file_id") else file_msg.photo[-1].file_id
    elif file_msg.video:
        return file_msg.video.file_id
    elif file_msg.audio:
        return file_msg.audio.file_id
    elif file_msg.voice:
        return file_msg.voice.file_id
    elif file_msg.sticker:
        return file_msg.sticker.file_id
    elif file_msg.animation:
        return file_msg.animation.file_id
    elif file_msg.video_note:
        return file_msg.video_note.file_id
    return None

# ====== COMMAND HANDLERS ======

@check_banned
@StreamBot.on_message(filters.command("start") & filters.private)
async def start_command(bot: Client, message: Message):
    # Process /start command and handle file info retrieval
    try:
        if message.from_user:
            await log_new_user(bot, message.from_user.id, message.from_user.first_name)
        # Handle token activation if present
        if len(message.command) == 2:
            token = message.command[1]
            
            try:
                # Check if it's a token activation command
                if token.startswith("token"):
                    
                    validation_result = await validate_activation_token(token)
                    
                    if (validation_result["valid"]):
                        await db.update_user_token(message.from_user.id, token)
                        await message.reply_text(
                            f"🎉 Token activated successfully!\n\n"
                            f"🔑 You can use all features until: {validation_result['expiry_date']}\n"
                            f"📝 Note: {validation_result['description']}",
                            quote=True
                        )
                        return
                    else:
                        await message.reply_text(
                            f"❌ Token activation failed: {validation_result['reason']}",
                            quote=True
                        )
                        return
                else:
                    
                    # Try to get token from database
                    record = await get(token)
                    if record and record["user_id"] == message.from_user.id and await check(message.from_user.id):
                        await message.reply_text("✅ Token activated! You may now use the bot.", quote=True)
                        return
                    else:
                        await message.reply_text("❌ Invalid or expired token. Please get a new one.", quote=True)
                        return
            except Exception as e:
                logger.error(f"Token activation error: {e}")
                await message.reply_text("❌ An error occurred during token activation. Please try again.", quote=True)
                return
        
        # Parse /start payload (if any) by whitespace
        parts = message.text.strip().split(maxsplit=1)
        if len(parts) == 1 or parts[1].lower() == "start":
            welcome_text = (
                "👋 **Welcome to the Thunder File to Link Bot!**\n\n"
                "I can generate direct download and streaming links for your files. "
                "Simply send me any file, and I'll provide you with shareable links.\n\n"
                "📌 **Available Commands:**\n"
                "• `/help` - Learn how to use the bot\n"
                "• `/link` - Generate links in groups\n"
                "• `/about` - Information about the bot\n"
                "• `/ping` - Check bot's response time\n"
                "• `/dc` - View data center information\n\n"
                "✨ Enjoy using the bot, and feel free to share your feedback!"
            )
            
            if Var.FORCE_CHANNEL_ID:
                try:
                    chat = await bot.get_chat(Var.FORCE_CHANNEL_ID)
                    invite_link = chat.invite_link
                    if not invite_link and chat.username:
                        invite_link = f"https://t.me/{chat.username}"
                    
                    if invite_link:
                        welcome_text += f"\n\nP.S. To unlock all features and get updates, please join our community channel: {invite_link}"
                    else:
                        logger.warning(f"Could not retrieve invite link for FORCE_CHANNEL_ID {Var.FORCE_CHANNEL_ID} for /start message. Channel: {chat.title}")
                        welcome_text += "\n\nP.S. Join our community channel to get the best experience! Ask an admin for the link."
                except Exception as e:
                    logger.error(f"Error adding force channel link to /start message for user {message.from_user.id if message.from_user else 'unknown'}, FORCE_CHANNEL_ID {Var.FORCE_CHANNEL_ID}: {e}")
                    welcome_text += "\n\nP.S. Check out our community channel for more!"
            
            await message.reply_text(text=welcome_text, link_preview_options=LinkPreviewOptions(is_disabled=True))
            return
        
        # File ID provided in command payload - generate links
        payload = parts[1]
        try:
            msg_id = int(payload)
            file_msg = await bot.get_messages(chat_id=Var.BIN_CHANNEL, message_ids=msg_id)
            
            if not file_msg:
                await handle_user_error(message, INVALID_FILE_MSG)
                return
                
            # Get file details
            file_name = get_name(file_msg) or "File"
            file_size = humanbytes(get_media_file_size(file_msg))
            stream_link, download_link = await generate_media_links(file_msg)
            
            if not stream_link or not download_link:
                await handle_user_error(message, "❌ **Failed to generate links for this file.**")
                return

            # Send response with links
            await message.reply_text(
                text=(
                    f"🔗 **Your Links Are Ready!**\n\n"
                    f"📄 **File Name:** `{file_name}`\n"
                    f"📦 **File Size:** `{file_size}`\n\n"
                    f"📥 **Download Link:**\n`{download_link}`\n\n"
                    f"🖥️ **Watch Now:**\n`{stream_link}`\n\n"
                    "⏰ **Note:** Links are available as long as the bot is active."
                ),
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🎬 Stream Now", url=stream_link),
                        InlineKeyboardButton("📥 Download", url=download_link)
                    ]
                ])
            )
        except ValueError:
            await handle_user_error(message, INVALID_FILE_MSG)
        except Exception as e:
            await handle_user_error(message, "❌ **Failed to retrieve file information.**")
    except Exception as e:
        logger.error(f"Error in start_command: {e}")
        await handle_user_error(message, ERROR_MSG)

@check_banned
@StreamBot.on_message(filters.command("help") & filters.private)
async def help_command(bot: Client, message: Message):
    # Handle /help command
    try:
        if message.from_user:
            await log_new_user(bot, message.from_user.id, message.from_user.first_name)
            
        help_text = (
            "📚 **How to Use Thunder File to Link Bot**\n\n"
            "**🔹 For Direct Links:**\n"
            "• Send any file to the bot\n"
            "• Receive download and streaming links instantly\n\n"
            "**🔹 In Groups:**\n"
            "• Reply to any file with `/link` command\n"
            "• Reply to the top file in the batch with the `/link no` command.\n"
            "• Add bot as admin to generate links automatically\n\n"
            "**🔹 In Channels:**\n"
            "• Add bot as admin to generate links for all posts\n\n"
            "**🔸 Additional Commands:**\n"
            "• `/about` - About this bot\n"
            "• `/ping` - Check response time\n"
            "• `/dc` - Check data center information\n\n"
            "⚡ **Pro Tip:** Forward messages from channels and groups to get direct links instantly!"
        )
        await message.reply_text(text=help_text, link_preview_options=LinkPreviewOptions(is_disabled=True))
    except Exception as e:
        logger.error(f"Error in help_command: {e}")
        await handle_user_error(message, ERROR_MSG)

@check_banned
@StreamBot.on_message(filters.command("about") & filters.private)
async def about_command(bot: Client, message: Message):
    # Handle /about command
    try:
        if message.from_user:
            await log_new_user(bot, message.from_user.id, message.from_user.first_name)
            
        about_text = (
            "🤖 **About Thunder File to Link Bot**\n\n"
            "A powerful bot that generates direct download and streaming links for your Telegram files.\n\n"
            "**✨ Features:**\n"
            "• 🔗 Instant link generation\n"
            "• 🎬 Stream media files online\n"
            "• 📦 Support for all file types\n"
            "• ⚡ Fast download speeds\n"
            "• 🔒 Secure file sharing\n"
            "• 📱 Mobile-friendly interface\n\n"
            "• Processing: Lightning fast\n\n"
            "Developed with ❤️ by the help of [AI](https://github.com/fyaz05/FileToLink/)"
        )
        await message.reply_text(text=about_text, link_preview_options=LinkPreviewOptions(is_disabled=True))
    except Exception as e:
        logger.error(f"Error in about_command: {e}")
        await handle_user_error(message, ERROR_MSG)

@check_banned
@force_channel_check
@StreamBot.on_message(filters.command("dc"))
async def dc_command(bot: Client, message: Message):
    # Handle /dc command with optimized logic for both users and files
    try:
        if message.from_user:
            await log_new_user(bot, message.from_user.id, message.from_user.first_name)

        args = message.text.strip().split(maxsplit=1)
        
        # Function to process and respond with DC info for users
        async def process_dc_info(user):
            dc_text = await generate_dc_text(user)
            dc_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 View Profile", url=f"tg://user?id={user.id}")]
            ])
            await message.reply_text(
                dc_text, 
                link_preview_options=LinkPreviewOptions(is_disabled=True), 
                reply_markup=dc_keyboard, 
                quote=True
            )
        
        # Function to process and respond with DC info for files
        async def process_file_dc_info(file_msg):
            try:
                # Get file name and size
                file_name = get_name(file_msg) or "File"
                file_size = humanbytes(get_media_file_size(file_msg))
                
                # Determine file type
                file_type = "Document" if file_msg.document else \
                           "Photo" if file_msg.photo else \
                           "Video" if file_msg.video else \
                           "Audio" if file_msg.audio else \
                           "Voice Message" if file_msg.voice else \
                           "Sticker" if file_msg.sticker else \
                           "Animation" if file_msg.animation else \
                           "Video Note" if file_msg.video_note else "Unknown"
                
                # Get DC ID directly from raw media document
                dc_id = "Unknown"
                if hasattr(file_msg, 'raw') and hasattr(file_msg.raw, 'media'):
                    if hasattr(file_msg.raw.media, 'document') and hasattr(file_msg.raw.media.document, 'dc_id'):
                        dc_id = file_msg.raw.media.document.dc_id
                
                # Prepare response text
                dc_text = (
                    f"🌐 **File Information**\n\n"
                    f"📄 **File Name:** `{file_name}`\n"
                    f"📦 **File Size:** `{file_size}`\n"
                    f"📁 **File Type:** `{file_type}`\n"
                    f"🌐 **Data Center:** `{dc_id}`"
                )
                
                await message.reply_text(dc_text, link_preview_options=LinkPreviewOptions(is_disabled=True), quote=True)
                
            except Exception as e:
                logger.error(f"Error processing file info: {e}")
                await handle_user_error(message, "❌ **Failed to retrieve file information.**")
        
        # Case 1: Command with arguments
        if len(args) > 1:
            query = args[1].strip()
            user = await get_user_safely(bot, query)
            
            if user:
                await process_dc_info(user)
            else:
                await handle_user_error(message, FAILED_USER_INFO_MSG)
            return
            
        # Case 2: Reply to a message
        if message.reply_to_message:
            # Check if it has any media
            if any(hasattr(message.reply_to_message, attr) and getattr(message.reply_to_message, attr) 
                for attr in ["document", "photo", "video", "audio", "voice", "sticker", "animation", "video_note"]):
                await process_file_dc_info(message.reply_to_message)
                return
            # If it's a normal message with a sender
            elif message.reply_to_message.from_user:
                await process_dc_info(message.reply_to_message.from_user)
                return
            else:
                await handle_user_error(message, "❌ **Unable to process this type of message.**")
                return
            
        # Case 3: Command issuer's DC info
        if message.from_user:
            await process_dc_info(message.from_user)
        else:
            await handle_user_error(message, "❌ **Unable to retrieve your information.**")
            
    except Exception as e:
        logger.error(f"Error in dc_command: {e}")
        await handle_user_error(message, ERROR_MSG)

@check_banned
@force_channel_check
@StreamBot.on_message(filters.command("ping") & filters.private)
async def ping_command(bot: Client, message: Message):
    # Handle /ping command - check bot response time
    try:
        start_time = time.time()
        response = await message.reply_text("🏓 Pinging...")
        end_time = time.time()
        time_taken_ms = (end_time - start_time) * 1000
        await response.edit_text(
            f"⚡ **PONG!**\n\n"
            f"⏱️ **Response Time:** `{time_taken_ms:.2f} ms`\n"
            f"🔌 **Server Status:** `Online`\n"
            f"🚀 **Bot Status:** `Active`"
        )
    except Exception as e:
        logger.error(f"Error in ping_command: {e}")
        await handle_user_error(message, ERROR_MSG)
