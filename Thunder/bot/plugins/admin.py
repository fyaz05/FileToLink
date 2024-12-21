# Thunder/bot/plugins/admin.py

import os
import sys
import time
import asyncio
import datetime
import shutil
import psutil
import random
import string
import html
from typing import Tuple, List, Dict
from urllib.parse import quote_plus

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User
)
from pyrogram.errors import FloodWait

from Thunder.bot import StreamBot, multi_clients, work_loads
from Thunder.vars import Var
from Thunder import StartTime, __version__
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.time_format import get_readable_time
from Thunder.utils.database import Database
from Thunder.utils.logger import logger, LOG_FILE

# ==============================
# Database Initialization
# ==============================

# Initialize the database connection using the provided DATABASE_URL and bot name
db = Database(Var.DATABASE_URL, Var.NAME)

# Dictionary to keep track of active broadcasts by their unique IDs
broadcast_ids: Dict[str, any] = {}

# ==============================
# Helper Functions
# ==============================

def generate_unique_id() -> str:
    """
    Generate a unique 6-character alphanumeric ID for each broadcast instance.

    Returns:
        str: A unique ID string.
    """
    while True:
        # Generate a random string of 6 alphanumeric characters
        random_id = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
        # Ensure the generated ID is not already in use
        if random_id not in broadcast_ids:
            return random_id

async def handle_broadcast_completion(
    message: Message,
    output: Message,
    failures: int,
    successes: int,
    total_users: int,
    start_time: float
):
    """
    Handle actions after a broadcast is completed, such as sending a summary to the owner.

    Args:
        message (Message): The original message that initiated the broadcast.
        output (Message): The message object used to display broadcast status.
        failures (int): Number of failed message deliveries.
        successes (int): Number of successful message deliveries.
        total_users (int): Total number of users targeted in the broadcast.
        start_time (float): Timestamp when the broadcast started.
    """
    # Calculate the elapsed time since the broadcast started
    elapsed_time = get_readable_time(time.time() - start_time)
    # Delete the initial broadcast initiation message
    await output.delete()

    # Compose the summary message with broadcast results
    message_text = (
        "✅ **Broadcast Completed** ✅\n\n"
        f"⏱️ **Duration:** {elapsed_time}\n\n"
        f"👥 **Total Users:** {total_users}\n\n"
        f"✅ **Success:** {successes}\n\n"
        f"❌ **Failed:** {failures}\n"
    )

    # Send the summary message to the owner
    await message.reply_text(
        message_text,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )

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

async def notify_owner(client: Client, text: str):
    """
    Send a notification message to the bot owner(s).

    Args:
        client (Client): The Pyrogram client instance.
        text (str): The text message to send.
    """
    try:
        owner_ids = Var.OWNER_ID
        if isinstance(owner_ids, (list, tuple)):
            for owner_id in owner_ids:
                await client.send_message(chat_id=owner_id, text=text)
        else:
            await client.send_message(chat_id=owner_ids, text=text)
    except Exception as e:
        logger.error(f"Failed to send message to owner: {e}", exc_info=True)

async def handle_user_error(message: Message, error_msg: str):
    """
    Send a standardized error message to the user.

    Args:
        message (Message): The incoming message triggering the error.
        error_msg (str): The error message to send.
    """
    try:
        await message.reply_text(f"❌ {error_msg}\nPlease try again or contact support.", quote=True)
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

async def generate_media_links(log_msg: Message) -> Tuple[str, str, str, str]:
    """
    Generate stream and download links for media.

    Args:
        log_msg (Message): The message in BIN_CHANNEL containing media.

    Returns:
        Tuple[str, str, str, str]: A tuple containing the stream link, download link,
                                   media name, and media size.
    """
    try:
        base_url = Var.URL.rstrip("/")
        file_id = log_msg.id
        # Ensure file_name is a string
        media_name = get_name(log_msg)
        if isinstance(media_name, bytes):
            media_name = media_name.decode('utf-8', errors='replace')
        else:
            media_name = str(media_name)
        media_size = humanbytes(get_media_file_size(log_msg))
        file_name_encoded = quote_plus(media_name)
        hash_value = get_hash(log_msg)
        stream_link = f"{base_url}/watch/{file_id}/{file_name_encoded}?hash={hash_value}"
        online_link = f"{base_url}/{file_id}/{file_name_encoded}?hash={hash_value}"
        logger.info(f"Generated media links for file_id {file_id}")
        return stream_link, online_link, media_name, media_size
    except Exception as e:
        error_text = f"Error generating media links: {e}"
        logger.error(error_text, exc_info=True)
        await notify_channel(log_msg._client, error_text)
        raise

async def send_links_to_user(client: Client, command_message: Message, media_name: str,
                             media_size: str, stream_link: str, online_link: str):
    """
    Send the generated links to the user.

    Args:
        client (Client): The Pyrogram client instance.
        command_message (Message): The message where the command was issued.
        media_name (str): The name of the media file.
        media_size (str): The size of the media file.
        stream_link (str): The streaming link.
        online_link (str): The direct download link.
    """
    msg_text = (
        "🔗 **Your Links are Ready!**\n\n"
        f"📄 **File Name:** `{media_name}`\n"
        f"📂 **File Size:** `{media_size}`\n\n"
        f"📥 **Download Link:**\n`{online_link}`\n\n"
        f"🖥️ **Watch Now:**\n`{stream_link}`\n\n"
        "⏰ **Note:** Links are available as long as the bot is active."
    )
    try:
        await command_message.reply_text(
            msg_text,
            quote=True,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🖥️ Watch Now", url=stream_link),
                 InlineKeyboardButton("📥 Download", url=online_link)]
            ])
        )
        logger.info(f"Sent links to user {command_message.from_user.id}")
    except Exception as e:
        error_text = f"Error sending links to user: {e}"
        logger.error(error_text, exc_info=True)
        await notify_owner(client, error_text)
        raise

async def log_request(log_msg: Message, user, stream_link: str, online_link: str):
    """
    Log the request details in the BIN_CHANNEL.

    Args:
        log_msg (Message): The message in BIN_CHANNEL containing media.
        user (User or Chat): The user or chat who requested the links.
        stream_link (str): The streaming link.
        online_link (str): The direct download link.
    """
    try:
        await log_msg.reply_text(
            f"👤 **Requested by:** [{user.first_name}](tg://user?id={user.id})\n"
            f"🆔 **User ID:** `{user.id}`\n\n"
            f"📥 **Download Link:** `{online_link}`\n"
            f"🖥️ **Watch Now Link:** `{stream_link}`",
            disable_web_page_preview=True,
            quote=True
        )
        logger.info(f"Logged request in BIN_CHANNEL for user {user.id}")
    except Exception as e:
        error_text = f"Error logging request: {e}"
        logger.error(error_text, exc_info=True)
        # Not critical, so no need to notify owner

async def check_admin_privileges(client: Client, chat_id: int) -> bool:
    """
    Check if the bot is an admin in the chat; skip for private chats.

    Args:
        client (Client): The Pyrogram client instance.
        chat_id (int): The ID of the chat to check.

    Returns:
        bool: True if the bot is an admin or the chat is private, False otherwise.
    """
    try:
        chat = await client.get_chat(chat_id)
        if chat.type == 'private':
            return True  # Admin check not needed in private chats

        # Get the bot's status in the chat
        member = await client.get_chat_member(chat_id, client.me.id)
        # Check if the bot is either an administrator or the creator
        is_admin_or_creator = member.status in ["administrator", "creator"]

        # Log and return the privilege check result
        logger.info(f"Bot admin status in chat {chat_id}: {is_admin_or_creator}")
        return is_admin_or_creator

    except Exception as e:
        # Log the error if checking admin privileges fails
        error_text = f"Error checking admin privileges: {e}"
        logger.error(error_text, exc_info=True)
        await notify_channel(client, error_text)
        return False

# ==============================
# Admin Command Handlers
# ==============================

@StreamBot.on_message(filters.command("users") & filters.private & filters.user(list(Var.OWNER_ID)))
async def get_total_users(client: Client, message: Message):
    """
    Retrieve and display the total number of users in the database.

    This command is restricted to the bot owner(s).

    Args:
        client (Client): The Pyrogram client instance.
        message (Message): The incoming message triggering the command.
    """
    try:
        # Fetch the total number of users from the database
        total_users = await db.total_users_count()
        # Reply with the total user count
        await message.reply_text(
            f"👥 **Total Users in DB:** **{total_users}**",
            quote=True,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
    except Exception as e:
        # Log the error and notify the owner of the failure
        logger.error(f"Error while fetching total users: {e}", exc_info=True)
        await message.reply_text(
            "🚨 **An error occurred while fetching the total users.**",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )

@StreamBot.on_message(filters.command("broadcast") & filters.private & filters.user(list(Var.OWNER_ID)))
async def broadcast_message(client: Client, message: Message):
    """
    Broadcast a message to all users in the database.

    This command is restricted to the bot owner(s) and must be used by replying to a message.

    Args:
        client (Client): The Pyrogram client instance.
        message (Message): The incoming message triggering the command.
    """
    # Ensure the command is used by replying to a message
    if not message.reply_to_message:
        await message.reply_text("⚠️ **Please reply to a message to broadcast.**", quote=True)
        return

    try:
        # Notify the owner that the broadcast has been initiated
        output = await message.reply_text(
            "📢 **Broadcast Initiated**. Please wait until completion.",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )

        # Fetch all user IDs from the database
        all_users_cursor = await db.get_all_users()
        all_users: List[Dict[str, int]] = []
        async for user in all_users_cursor:
            all_users.append(user)

        # Check if there are any users to broadcast to
        if not all_users:
            await output.edit("📢 **No Users Found**. Broadcast aborted.")
            return

        # Get the bot's own user ID to avoid sending messages to itself
        self_id = client.me.id
        start_time = time.time()
        successes, failures = 0, 0

        # Semaphore to limit the number of concurrent tasks
        semaphore = asyncio.Semaphore(10)  # Adjust concurrency level as needed

        # Locks to safely update shared counters across asynchronous tasks
        successes_lock = asyncio.Lock()
        failures_lock = asyncio.Lock()

        async def send_message_to_user(user_id: int):
            """
            Send the broadcast message to a single user with retry logic.

            Args:
                user_id (int): The Telegram user ID to send the message to.
            """
            nonlocal successes, failures
            # Skip sending the message to the bot itself or invalid user IDs
            if not isinstance(user_id, int) or user_id == self_id:
                return

            async with semaphore:
                for attempt in range(3):  # Retry up to 3 times
                    try:
                        # Determine the type of content to send based on the replied message
                        if message.reply_to_message.text or message.reply_to_message.caption:
                            # Send text or caption content
                            await client.send_message(
                                chat_id=user_id,
                                text=message.reply_to_message.text or message.reply_to_message.caption,
                                parse_mode=ParseMode.MARKDOWN,
                                disable_web_page_preview=True
                            )
                        elif message.reply_to_message.media:
                            # Copy media content directly
                            await message.reply_to_message.copy(chat_id=user_id)

                        # Safely increment the success counter
                        async with successes_lock:
                            successes += 1
                        break  # Exit the retry loop on success

                    except FloodWait as e:
                        logger.warning(f"FloodWait error: sleeping for {e.value} seconds.")
                        await asyncio.sleep(e.value + 1)
                        continue  # Retry after waiting
                    except Exception as e:
                        logger.warning(f"Problem sending to {user_id}: {e}")
                        # Do not retry for certain types of errors related to the bot itself
                        if "bot" in str(e).lower() or "self" in str(e).lower():
                            break
                        # If the user is not found, remove them from the database
                        if "user" in str(e).lower() and "not found" in str(e).lower():
                            await db.delete_user(user_id)
                        # Safely increment the failure counter
                        async with failures_lock:
                            failures += 1
                        # Wait before retrying to prevent rapid retries
                        await asyncio.sleep(0.5)  # Adjust delay as needed

        # Create asynchronous tasks for sending messages to all users
        tasks = [send_message_to_user(int(user['id'])) for user in all_users]
        await asyncio.gather(*tasks)  # Run all tasks concurrently

        # Handle the completion of the broadcast by sending a summary
        await handle_broadcast_completion(
            message,
            output,
            failures,
            successes,
            len(all_users),
            start_time
        )

    except Exception as e:
        error_text = f"Error during broadcast: {e}"
        logger.error(error_text, exc_info=True)
        await message.reply_text(
            "🚨 **An error occurred during the broadcast.**",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
        await notify_channel(client, f"⚠️ Critical error during broadcast:\n{e}")

@StreamBot.on_message(filters.command("status") & filters.private & filters.user(list(Var.OWNER_ID)))
async def show_status(client: Client, message: Message):
    """
    Display the current status of the bot, including server uptime, connected bots, and their workloads.

    This command is restricted to the bot owner(s).

    Args:
        client (Client): The Pyrogram client instance.
        message (Message): The incoming message triggering the command.
    """
    try:
        # Calculate the bot's uptime
        uptime = get_readable_time(time.time() - StartTime)

        # Generate a detailed workload distribution among connected bots
        workloads_text = "📊 **Workloads per Bot:**\n\n"
        workloads = {
            f"🤖 Bot {c + 0}": load
            for c, (bot, load) in enumerate(
                sorted(work_loads.items(), key=lambda x: x[1], reverse=True)
            )
        }
        for bot_name, load in workloads.items():
            workloads_text += f"   {bot_name}: {load}\n"

        # Compile the full status message with all relevant information
        stats_text = (
            f"⚙️ **Server Status:** Running\n\n"
            f"🕒 **Uptime:** {uptime}\n\n"
            f"🤖 **Connected Bots:** {len(multi_clients)}\n\n"
            f"{workloads_text}\n"
            f"♻️ **Version:** {__version__}\n"
        )

        # Send the status message to the owner
        await message.reply_text(
            stats_text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )

    except Exception as e:
        # Log the error and notify the owner of the failure
        logger.error(f"Error displaying status: {e}", exc_info=True)
        await message.reply_text(
            "🚨 **An error occurred while retrieving the status.**",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )

@StreamBot.on_message(filters.command("stats") & filters.private & filters.user(list(Var.OWNER_ID)))
async def show_stats(client: Client, message: Message):
    """
    Display detailed server statistics where the bot is hosted.

    This includes disk usage, data usage, CPU and RAM utilization.

    This command is restricted to the bot owner(s).

    Args:
        client (Client): The Pyrogram client instance.
        message (Message): The incoming message triggering the command.
    """
    try:
        # Calculate the bot's uptime
        current_time = get_readable_time(time.time() - StartTime)
        # Get disk usage statistics
        total, used, free = shutil.disk_usage('.')

        # Compile the statistics into a formatted message
        stats_text = (
            f"📊 **Bot Statistics** 📊\n\n"
            f"⏳ **Uptime:** {current_time}\n\n"
            f"💾 **Disk Space:**\n"
            f"   📀 **Total:** {humanbytes(total)}\n"
            f"   📝 **Used:** {humanbytes(used)}\n"
            f"   📭 **Free:** {humanbytes(free)}\n\n"
            f"📶 **Data Usage:**\n"
            f"   🔺 **Upload:** {humanbytes(psutil.net_io_counters().bytes_sent)}\n"
            f"   🔻 **Download:** {humanbytes(psutil.net_io_counters().bytes_recv)}\n\n"
            f"🖥️ **CPU Usage:** {psutil.cpu_percent(interval=0.5)}%\n"
            f"🧠 **RAM Usage:** {psutil.virtual_memory().percent}%\n"
            f"📦 **Disk Usage:** {psutil.disk_usage('/').percent}%\n"
        )
        # Send the statistics message to the owner
        await message.reply_text(
            stats_text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
    except Exception as e:
        # Log the error and notify the owner of the failure
        logger.error(f"Error retrieving bot statistics: {e}", exc_info=True)
        await message.reply_text(
            "🚨 **Failed to retrieve the statistics.**",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )

@StreamBot.on_message(filters.command("restart") & filters.private & filters.user(list(Var.OWNER_ID)))
async def restart_bot(client: Client, message: Message):
    """
    Restart the bot process.

    This command is restricted to the bot owner(s). It attempts to gracefully restart the bot
    by replacing the current process with a new one.

    Args:
        client (Client): The Pyrogram client instance.
        message (Message): The incoming message triggering the command.
    """
    try:
        # Notify the owner that the bot is restarting
        await message.reply_text(
            "🔄 **Restarting the bot...**",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
        # Log the restart action
        logger.info("Bot is restarting as per owner's request.")

        # Wait briefly to ensure the notification message is sent
        await asyncio.sleep(2)

        # Restart the bot by replacing the current process
        os.execv(sys.executable, [sys.executable, "-m", "Thunder"])

    except Exception as e:
        # Log the error and notify the owner of the failure
        logger.error(f"Error during restart: {e}", exc_info=True)
        await message.reply_text(
            "🚨 **Failed to restart the bot.**",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )

@StreamBot.on_message(filters.command("log") & filters.private & filters.user(list(Var.OWNER_ID)))
async def send_logs(client: Client, message: Message):
    """
    Send the latest log file to the bot owner.

    This command is restricted to the bot owner(s).

    Args:
        client (Client): The Pyrogram client instance.
        message (Message): The incoming message triggering the command.
    """
    try:
        # Use the absolute path from logger.py
        log_file_path = LOG_FILE
        # Check if the log file exists
        if os.path.exists(log_file_path):
            # Check if the log file is empty
            if os.path.getsize(log_file_path) > 0:
                # Send the log file as a document to the owner
                await message.reply_document(
                    document=log_file_path,
                    caption="📄 **Here are the latest logs:**",
                    parse_mode=ParseMode.MARKDOWN
                )
                # Log the successful transmission of the log file
                logger.info("Sent log file to the owner.")
            else:
                # Notify the owner that the log file is empty
                await message.reply_text(
                    "⚠️ **The log file is empty.**",
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True
                )
                # Log that the log file is empty
                logger.warning("Log file is empty; not sending.")
        else:
            # Notify the owner that the log file was not found
            await message.reply_text(
                "⚠️ **Log file not found.**",
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
            # Log the absence of the log file
            logger.warning("Log file was requested but not found.")
    except Exception as e:
        # Log the error and notify the owner of the failure
        logger.error(f"Error sending log file: {e}", exc_info=True)
        await message.reply_text(
            "🚨 **Failed to retrieve the log file.**",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )

@StreamBot.on_message(filters.command("shell") & filters.private & filters.user(list(Var.OWNER_ID)))
async def run_shell_command(client: Client, message: Message):
    """
    Execute a shell command on the server and return its output.

    **⚠️ Warning:** This command can execute arbitrary shell commands, which poses significant security risks.
    Ensure that only trusted individuals have access to this command.

    This command is restricted to the bot owner(s).

    Args:
        client (Client): The Pyrogram client instance.
        message (Message): The incoming message triggering the command.
    """
    try:
        # Ensure that a shell command is provided
        if len(message.command) < 2:
            await message.reply_text(
                "⚠️ <b>Please provide a shell command to execute.</b>\n\n<b>Usage:</b> <code>/shell &lt;command&gt;</code>",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            return

        # Extract the shell command from the message
        shell_command = message.text.split(None, 1)[1]
        logger.info(f"Executing shell command: {shell_command}")

        # Execute the shell command asynchronously
        process = await asyncio.create_subprocess_shell(
            shell_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # Capture the standard output and error
        stdout, stderr = await process.communicate()
        stdout, stderr = stdout.decode().strip(), stderr.decode().strip()

        # Escape HTML special characters in the outputs
        stdout = html.escape(stdout)
        stderr = html.escape(stderr)

        # Prepare the response message with escaped content
        response = ""
        if stdout:
            # Truncate stdout to prevent exceeding message limits
            stdout = stdout[:4000]
            response += f"<b>STDOUT:</b>\n<pre>{stdout}</pre>"
        if stderr:
            # Truncate stderr to prevent exceeding message limits
            stderr = stderr[:4000]
            if stdout:
                response += "\n\n"
            response += f"<b>STDERR:</b>\n<pre>{stderr}</pre>"
        if not stdout and not stderr:
            # Notify the owner if the command produced no output
            response = "⚠️ <b>No output returned from the command.</b>"

        # Send the response back to the owner
        await message.reply_text(
            response,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

    except Exception as e:
        # Log the error and notify the owner of the failure
        logger.error(f"Error executing shell command: {e}", exc_info=True)
        await message.reply_text(
            "🚨 <b>Failed to execute the shell command.</b>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
