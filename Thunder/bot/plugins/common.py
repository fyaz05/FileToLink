# Thunder/bot/plugins/common.py

import time
import asyncio
from typing import Tuple
from urllib.parse import quote_plus

from pyrogram import Client, filters
from pyrogram.errors import RPCError
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User
)

from Thunder.bot import StreamBot
from Thunder.vars import Var
from Thunder.utils.database import Database
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.file_properties import get_hash, get_media_file_size, get_name
from Thunder.utils.logger import logger

# ==============================
# Database Initialization
# ==============================

# Initialize the database connection using the provided DATABASE_URL and bot name
db = Database(Var.DATABASE_URL, Var.NAME)

# ==============================
# Constants and Messages
# ==============================

INVALID_ARG_MSG = (
    "❌ **Invalid argument.** Please provide a valid Telegram User ID or username "
    "(e.g., `/dc 123456789` or `/dc @username`)."
)
FAILED_USER_INFO_MSG = (
    "❌ **Failed to retrieve user information.** Please ensure the User ID/username "
    "is correct and the user has interacted with the bot or is accessible."
)
REPLY_DOES_NOT_CONTAIN_USER_MSG = "❌ **The replied message does not contain a user.**"

# ==============================
# Helper Functions
# ==============================

async def notify_channel(bot: Client, text: str):
    """
    Send a notification message to the BIN_CHANNEL.

    Args:
        bot (Client): The Pyrogram client instance.
        text (str): The text message to send.
    """
    try:
        if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
            await bot.send_message(chat_id=Var.BIN_CHANNEL, text=text)
    except Exception as e:
        logger.error(f"Failed to send message to BIN_CHANNEL: {e}", exc_info=True)

async def handle_user_error(message: Message, error_msg: str):
    """
    Send a standardized error message to the user.

    Args:
        message (Message): The incoming message triggering the error.
        error_msg (str): The error message to send.
    """
    try:
        await message.reply_text(f"{error_msg}", quote=True)
    except Exception as e:
        logger.error(f"Failed to send error message to user: {e}", exc_info=True)

async def log_new_user(bot: Client, user_id: int, first_name: str):
    """
    Log a new user and send a notification to the BIN_CHANNEL if the user is new.

    Args:
        bot (Client): The Pyrogram client instance.
        user_id (int): The Telegram user ID.
        first_name (str): The first name of the user.
    """
    try:
        if not await db.is_user_exist(user_id):
            await db.add_user(user_id)
            try:
                if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
                    await bot.send_message(
                        Var.BIN_CHANNEL,
                        f"👋 **New User Alert!**\n\n"
                        f"✨ **Name:** [{first_name}](tg://user?id={user_id})\n"
                        f"🆔 **User ID:** `{user_id}`\n\n"
                        "has started the bot!"
                    )
                logger.info(f"New user added: {user_id} - {first_name}")
            except Exception as e:
                logger.error(f"Failed to send new user alert to BIN_CHANNEL: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error logging new user {user_id}: {e}", exc_info=True)

async def generate_media_links(log_msg: Message) -> Tuple[str, str]:
    """
    Generate stream and download links for media.

    Args:
        log_msg (Message): The message containing the media.

    Returns:
        Tuple[str, str]: A tuple containing the stream link and the download link.
    """
    try:
        base_url = Var.URL.rstrip("/")
        file_id = log_msg.id

        # Ensure file_name is a string
        file_name = get_name(log_msg)
        if isinstance(file_name, bytes):
            file_name = file_name.decode('utf-8', errors='replace')
        else:
            file_name = str(file_name)
        file_name_encoded = quote_plus(file_name)

        hash_value = get_hash(log_msg)
        stream_link = f"{base_url}/watch/{file_id}/{file_name_encoded}?hash={hash_value}"
        online_link = f"{base_url}/{file_id}/{file_name_encoded}?hash={hash_value}"
        logger.info(f"Generated media links for file_id {file_id}")
        return stream_link, online_link
    except Exception as e:
        logger.error(f"Error generating media links: {e}", exc_info=True)
        await notify_channel(log_msg._client, f"Error generating media links: {e}")
        raise

async def generate_dc_text(user: User) -> str:
    """
    Generate formatted DC (Data Center) information text for a user.

    Args:
        user (User): The user object.

    Returns:
        str: The formatted DC information text.
    """
    dc_id = user.dc_id if user.dc_id is not None else "Unknown"
    return (
        f"🌐 **Data Center Information**\n\n"
        f"👤 **User:** [{user.first_name or 'User'}](tg://user?id={user.id})\n"
        f"🆔 **User ID:** `{user.id}`\n"
        f"🌐 **Data Center:** `{dc_id}`\n\n"
        "This is the data center where the specified user is hosted."
    )

# ==============================
# Command Handlers
# ==============================

@StreamBot.on_message(filters.command("start") & filters.private)
async def start_command(bot: Client, message: Message):
    """
    Handle the /start command.

    Args:
        bot (Client): The Pyrogram client instance.
        message (Message): The incoming message triggering the command.
    """
    try:
        if message.from_user:
            await log_new_user(bot, message.from_user.id, message.from_user.first_name)
        args = message.text.strip().split("_", 1)

        if len(args) == 1 or args[-1].lower() == "start":
            # Welcome message when no arguments are provided
            welcome_text = (
                "👋 **Welcome to the File to Link Bot!**\n\n"
                "I'm here to help you generate direct download and streaming links for your files.\n"
                "Simply send me any file, and I'll provide you with links to share with others.\n\n"
                "🔹 **Available Commands:**\n"
                "/help - How to use the bot\n"
                "/about - About the bot\n"
                "/ping - Check bot's response time\n\n"
                "Enjoy using the bot, and feel free to share your feedback!"
            )
            await message.reply_text(text=welcome_text)
            logger.info(f"Sent welcome message to user {message.from_user.id}")
        else:
            # Handling the case when a file ID is provided
            try:
                msg_id = int(args[-1])
                get_msg = await bot.get_messages(chat_id=Var.BIN_CHANNEL, message_ids=msg_id)
                if not get_msg:
                    raise ValueError("Message not found")
                file_name = get_name(get_msg)
                if not file_name:
                    file_name = "Unknown File"
                file_size = humanbytes(get_media_file_size(get_msg))
                stream_link, online_link = await generate_media_links(get_msg)

                await message.reply_text(
                    text=(
                        f"🔗 **Your Links are Ready!**\n\n"
                        f"📄 **File Name:** `{file_name}`\n\n"
                        f"📂 **File Size:** `{file_size}`\n\n"
                        f"📥 **Download Link:**\n`{online_link}`\n\n"
                        f"🖥️ **Watch Now:**\n`{stream_link}`\n\n"
                        "⏰ **Note:** Links are available as long as the bot is active."
                    ),
                    disable_web_page_preview=True,
                    reply_markup=InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("🖥️ Watch Now", url=stream_link),
                            InlineKeyboardButton("📥 Download", url=online_link)
                        ]
                    ])
                )
                logger.info(f"Provided links to user {message.from_user.id} for file_id {msg_id}")
            except ValueError:
                await handle_user_error(message, "❌ **Invalid file identifier provided.**")
                logger.warning(f"Invalid file ID provided by user {message.from_user.id}")
            except Exception as e:
                await handle_user_error(message, "❌ **Failed to retrieve file information.**")
                logger.error(f"Failed to retrieve file info for message ID {args[-1]}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error in start_command: {e}", exc_info=True)
        await handle_user_error(message, "🚨 **An unexpected error occurred.**")
        await notify_channel(bot, f"Error in start_command: {e}")

@StreamBot.on_message(filters.command("help") & filters.private)
async def help_command(bot: Client, message: Message):
    """
    Handle the /help command.

    Args:
        bot (Client): The Pyrogram client instance.
        message (Message): The incoming message triggering the command.
    """
    try:
        if message.from_user:
            await log_new_user(bot, message.from_user.id, message.from_user.first_name)
        help_text = (
            "ℹ️ **How to Use the File to Link Bot**\n\n"
            "🔹 **Generate Links:** Send me any file, and I'll provide you with direct download and streaming links.\n"
            "🔹 **In Groups:** Use `/link` command as per group settings.\n"
            "🔹 **In Channels:** Add me to your channel, and I'll automatically generate links for new posts.\n\n"
            "🔸 **Additional Commands:**\n"
            "/about - Learn more about the bot\n"
            "/ping - Check the bot's response time\n\n"
            "If you have any questions or need support, feel free to reach out!"
        )
        await message.reply_text(text=help_text, disable_web_page_preview=True)
        logger.info(f"Sent help message to user {message.from_user.id}")
    except Exception as e:
        logger.error(f"Error in help_command: {e}", exc_info=True)
        await handle_user_error(message, "🚨 **An unexpected error occurred.**")
        await notify_channel(bot, f"Error in help_command: {e}")

@StreamBot.on_message(filters.command("about") & filters.private)
async def about_command(bot: Client, message: Message):
    """
    Handle the /about command.

    Args:
        bot (Client): The Pyrogram client instance.
        message (Message): The incoming message triggering the command.
    """
    try:
        if message.from_user:
            await log_new_user(bot, message.from_user.id, message.from_user.first_name)
        about_text = (
            "🤖 **About the File to Link Bot**\n\n"
            "This bot helps you generate direct download and streaming links for any file.\n\n"
            "🔹 **Features:**\n"
            " - Generate direct links for files\n"
            " - Support for all file types\n"
            " - Easy to use in private chats and groups\n\n"
            "Feel free to reach out if you have any questions or suggestions!"
        )
        await message.reply_text(text=about_text, disable_web_page_preview=True)
        logger.info(f"Sent about message to user {message.from_user.id}")
    except Exception as e:
        logger.error(f"Error in about_command: {e}", exc_info=True)
        await handle_user_error(message, "🚨 **An unexpected error occurred.**")
        await notify_channel(bot, f"Error in about_command: {e}")

@StreamBot.on_message(filters.command("dc"))
async def dc_command(bot: Client, message: Message):
    """
    Handle the /dc command to provide Data Center information.

    Args:
        bot (Client): The Pyrogram client instance.
        message (Message): The incoming message triggering the command.
    """
    try:
        # Log the user
        if message.from_user:
            await log_new_user(bot, message.from_user.id, message.from_user.first_name)

        # Extract arguments
        args = message.text.strip().split(maxsplit=1)

        if len(args) > 1:
            query = args[1].strip()

            if query.startswith('@'):
                # Handle username
                username = query
                try:
                    user = await bot.get_users(username)
                    dc_text = await generate_dc_text(user)

                    dc_keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔍 View Profile", url=f"tg://user?id={user.id}")]
                    ])

                    await message.reply_text(dc_text, disable_web_page_preview=True, reply_markup=dc_keyboard, quote=True)
                    logger.info(f"Provided DC info for username {username}")
                except RPCError as e:
                    await handle_user_error(message, FAILED_USER_INFO_MSG)
                    logger.error(f"Failed to get user info for username {username}: {e}", exc_info=True)
                except Exception as e:
                    await handle_user_error(message, FAILED_USER_INFO_MSG)
                    logger.error(f"Failed to get user info for username {username}: {e}", exc_info=True)
                return

            elif query.isdigit():
                # Handle TGID (Telegram User ID)
                user_id_arg = int(query)
                try:
                    user = await bot.get_users(user_id_arg)
                    dc_text = await generate_dc_text(user)

                    dc_keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔍 View Profile", url=f"tg://user?id={user.id}")]
                    ])

                    await message.reply_text(dc_text, disable_web_page_preview=True, reply_markup=dc_keyboard, quote=True)
                    logger.info(f"Provided DC info for user ID {user_id_arg}")
                except RPCError as e:
                    await handle_user_error(message, FAILED_USER_INFO_MSG)
                    logger.error(f"Failed to get user info for user ID {user_id_arg}: {e}", exc_info=True)
                except Exception as e:
                    await handle_user_error(message, FAILED_USER_INFO_MSG)
                    logger.error(f"Failed to get user info for user ID {user_id_arg}: {e}", exc_info=True)
                return
            else:
                await handle_user_error(message, INVALID_ARG_MSG)
                logger.warning(f"Invalid argument provided in /dc command: {query}")
                return

        # Check if the command is a reply to a message
        if message.reply_to_message and message.reply_to_message.from_user:
            user = message.reply_to_message.from_user
            dc_text = await generate_dc_text(user)
            dc_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 View Profile", url=f"tg://user?id={user.id}")]
            ])
            await message.reply_text(dc_text, disable_web_page_preview=True, reply_markup=dc_keyboard, quote=True)
            logger.info(f"Provided DC info for replied user {user.id}")
            return

        # Default case: No arguments and not a reply, return the DC of the command issuer
        if message.from_user:
            user = message.from_user
            dc_text = await generate_dc_text(user)

            dc_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 View Profile", url=f"tg://user?id={user.id}")]
            ])

            await message.reply_text(dc_text, disable_web_page_preview=True, reply_markup=dc_keyboard, quote=True)
            logger.info(f"Provided DC info for user {user.id}")
        else:
            await handle_user_error(message, "❌ **Unable to retrieve your information.**")
            logger.warning("Failed to retrieve information for the command issuer in /dc command.")
    except Exception as e:
        logger.error(f"Error in dc_command: {e}", exc_info=True)
        await handle_user_error(message, "🚨 **An unexpected error occurred.**")
        await notify_channel(bot, f"Error in dc_command: {e}")

@StreamBot.on_message(filters.command("ping") & filters.private)
async def ping_command(bot: Client, message: Message):
    """
    Handle the /ping command to check the bot's response time.

    Args:
        bot (Client): The Pyrogram client instance.
        message (Message): The incoming message triggering the command.
    """
    try:
        start_time = time.time()
        response = await message.reply_text("🏓 Pong!")
        end_time = time.time()
        time_taken_ms = (end_time - start_time) * 1000
        await response.edit(f"🏓 **Pong!**\n⏱ **Response Time:** `{time_taken_ms:.3f} ms`")
        logger.info(f"Ping command executed by user {message.from_user.id} in {time_taken_ms:.3f} ms")
    except Exception as e:
        logger.error(f"Error in ping_command: {e}", exc_info=True)
        await handle_user_error(message, "🚨 **An unexpected error occurred.**")
        await notify_channel(bot, f"Error in ping_command: {e}")
