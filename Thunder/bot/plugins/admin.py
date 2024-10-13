import os
import time
import random
import logging
import asyncio
import datetime
import shutil
import psutil
import string
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message
from Thunder.bot import StreamBot, multi_clients, work_loads
from Thunder.vars import Var
from Thunder import StartTime, __version__
from Thunder.utils.utils_bot import get_readable_file_size, get_readable_time
from Thunder.utils.database import Database

# Set up logging
logging.basicConfig(level=logging.INFO)

# Initialize the database
db = Database(Var.DATABASE_URL, Var.name)
broadcast_ids = {}

def generate_unique_id():
    """Generate a unique ID for each broadcast instance."""
    while True:
        random_id = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
        if random_id not in broadcast_ids:
            return random_id

async def handle_broadcast_completion(message, output, failures, successes, total_users, start_time):
    """Handle actions after a broadcast is completed."""
    elapsed_time = str(datetime.timedelta(seconds=int(time.time() - start_time)))
    await output.delete()

    message_text = (
        "✅ **Broadcast Completed** ✅\n\n\n"
        f"⏱️ **Duration:** {elapsed_time}\n\n"
        f"👥 **Total Users:** {total_users}\n\n"
        f"✅ **Success:** {successes}\n\n"
        f"❌ **Failed:** {failures}\n"
    )

    await message.reply_text(
        message_text,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )

@StreamBot.on_message(filters.command("users") & filters.private & filters.user(list(Var.OWNER_ID)))
async def get_total_users(client: Client, message: Message):
    """Retrieve the total number of users from the database."""
    try:
        total_users = await db.total_users_count()
        await message.reply_text(
            f"👥 **Total Users in DB:** **{total_users}**",
            quote=True,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
    except Exception as e:
        logging.error(f"Error while fetching total users: {e}")
        await message.reply_text(
            "🚨 An error occurred while fetching the total users.",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )

@StreamBot.on_message(filters.command("broadcast") & filters.private & filters.user(list(Var.OWNER_ID)))
async def broadcast_message(client, message):
    """Broadcast a message to all users."""
    if not message.reply_to_message:
        await message.reply_text("⚠️ Please reply to a message to broadcast.", quote=True)
        return

    output = await message.reply_text(
        "📢 **Broadcast Initiated**. Please wait until completion.",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )

    # Fetch user IDs from the database
    all_users_cursor = await db.get_all_users()
    all_users = await all_users_cursor.to_list(length=None)

    if not all_users:
        await output.edit("📢 **No Users Found**. Broadcast aborted.")
        return

    self_id = client.me.id  # Get the bot's self ID
    broadcast_id = generate_unique_id()
    start_time = time.time()
    successes, failures = 0, 0

    for user in all_users:
        user_id = int(user['id'])

        # Skip other bots or self-message
        if user_id == self_id:
            continue

        for _ in range(3):  # Retry up to 3 times
            try:
                # Clone and send the message without forwarding
                if message.reply_to_message.text or message.reply_to_message.caption:
                    await client.send_message(
                        chat_id=user_id,
                        text=message.reply_to_message.text or message.reply_to_message.caption,
                        parse_mode=ParseMode.MARKDOWN,
                        disable_web_page_preview=True
                    )
                elif message.reply_to_message.media:
                    await message.reply_to_message.copy(chat_id=user_id)

                successes += 1
                break

            except Exception as e:
                logging.warning(f"Problem sending to {user_id}: {e}")
                if "bot" in str(e).lower() or "self" in str(e).lower():
                    # Do not retry if issues related to bot/self
                    break
                if "User" in str(e) and "not found" in str(e):
                    await db.delete_user(user_id)
                failures += 1
                await asyncio.sleep(1)

        broadcast_ids[broadcast_id] = {"successful": successes, "failed": failures}

    broadcast_ids.pop(broadcast_id, None)
    await handle_broadcast_completion(message, output, failures, successes, len(all_users), start_time)

@StreamBot.on_message(filters.command("status") & filters.private & filters.user(list(Var.OWNER_ID)))
async def show_status(client: StreamBot, message: Message):
    """Display the current status of the bot, including workloads per connected bot."""
    try:
        uptime = get_readable_time(time.time() - StartTime)
        
        # Generate a string representation of workload distribution
        workloads_text = "📊 **Workloads per Bot:**\n\n"
        workloads = dict(
            ("🤖 Bot" + str(c + 1), l) 
            for c, (_, l) in enumerate(
                sorted(work_loads.items(), key=lambda x: x[1], reverse=True)
            )
        )
        for bot_name, load in workloads.items():
            workloads_text += f"   {bot_name}: {load}\n"
        
        # Combine all parts into status text
        stats_text = (
            f"⚙️ **Server Status:** Running\n\n"
            f"🕒 **Uptime:** {uptime}\n\n"
            f"🤖 **Connected Bots:** {len(multi_clients)}\n\n"
            f"{workloads_text}\n"
            f"♻️ **Version:** {__version__}\n"
        )
        
        # Send status as a reply
        await message.reply_text(
            stats_text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
    
    except Exception as e:
        logging.error(f"Error displaying status: {e}")
        await message.reply_text(
            "🚨 An error occurred while retrieving the status.",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )

@StreamBot.on_message(filters.command("stats") & filters.private & filters.user(list(Var.OWNER_ID)))
async def show_stats(client: StreamBot, message: Message):
    """Display server statistics where the bot is hosted."""
    try:
        current_time = get_readable_time(time.time() - StartTime)
        total, used, free = shutil.disk_usage('.')

        stats_text = (
            f"📊 **𝘽𝙤𝙩 𝙎𝙩𝙖𝙩𝙞𝙨𝙩𝙞𝙘𝙨** 📊\n\n"
            f"⏳ **Uptime:** {current_time}\n\n"
            f"💾 **Disk Space:**\n\n"
            f"   📀 **Total:** {get_readable_file_size(total)}\n\n"
            f"   📝 **Used:** {get_readable_file_size(used)}\n\n"
            f"   📭 **Free:** {get_readable_file_size(free)}\n\n\n"
            f"📶 **Data Usage:**\n\n"
            f"   🔺 **Upload:** {get_readable_file_size(psutil.net_io_counters().bytes_sent)}\n\n"
            f"   🔻 **Download:** {get_readable_file_size(psutil.net_io_counters().bytes_recv)}\n\n\n"
            f"🖥️ **CPU Usage:** {psutil.cpu_percent(interval=0.5)}%\n\n"
            f"🧠 **RAM Usage:** {psutil.virtual_memory().percent}%\n\n"
            f"📦 **Disk Usage:** {psutil.disk_usage('/').percent}%\n"
        )
        await message.reply_text(
            stats_text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
    except Exception as e:
        logging.error(f"Error retrieving bot statistics: {e}")
        await message.reply_text(
            "🚨 An error occurred while retrieving the statistics.",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
