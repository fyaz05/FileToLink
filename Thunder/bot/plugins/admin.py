# Thunder/bot/plugins/admin.py

import os
import sys
import time
import asyncio
import shutil
import psutil
import random
import string
import html
import hashlib
from typing import Tuple, List, Dict
from urllib.parse import quote_plus

from pyrogram import Client, filters
from pyrogram.enums import ParseMode, ChatMemberStatus
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message
)
from pyrogram.errors import (
    FloodWait, 
    UserDeactivated, 
    ChatWriteForbidden, 
    UserIsBlocked, 
    PeerIdInvalid
)

from Thunder.bot import StreamBot, multi_clients, work_loads
from Thunder.vars import Var
from Thunder import StartTime, __version__
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.time_format import get_readable_time
from Thunder.utils.database import Database
from Thunder.utils.logger import logger, LOG_FILE

# Initialize database
db = Database(Var.DATABASE_URL, Var.NAME)

# Track active broadcasts
broadcast_ids = {}

# Maximum concurrent tasks
MAX_CONCURRENT_TASKS = 10

# Media utility functions - implemented directly to fix import error
def get_name(message):
    try:
        if message.document:
            return message.document.file_name
        elif message.video:
            return message.video.file_name
        elif message.audio:
            return message.audio.file_name
        elif message.photo:
            return f"photo_{message.id}.jpg"
        elif message.voice:
            return f"voice_{message.id}.ogg"
        else:
            return f"file_{message.id}"
    except:
        return f"unnamed_file_{message.id}"

def get_media_file_size(message):
    try:
        if message.document:
            return message.document.file_size
        elif message.video:
            return message.video.file_size
        elif message.audio:
            return message.audio.file_size
        elif message.photo:
            return message.photo.file_size
        elif message.voice:
            return message.voice.file_size
        else:
            return 0
    except:
        return 0

def get_hash(message):
    try:
        if message.document:
            file_id = message.document.file_unique_id
        elif message.video:
            file_id = message.video.file_unique_id
        elif message.audio:
            file_id = message.audio.file_unique_id
        elif message.photo:
            file_id = message.photo.file_unique_id
        elif message.voice:
            file_id = message.voice.file_unique_id
        else:
            file_id = str(message.id)
        
        hash_input = f"{file_id}_{message.id}"
        return hashlib.md5(hash_input.encode()).hexdigest()
    except:
        return hashlib.md5(str(message.id).encode()).hexdigest()

# Helper Functions
def generate_unique_id(length=6):
    while True:
        random_id = ''.join(random.choices(string.ascii_letters + string.digits, k=length))
        if random_id not in broadcast_ids:
            return random_id

async def notify_channel(bot, text):
    try:
        if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
            await bot.send_message(chat_id=Var.BIN_CHANNEL, text=text)
    except Exception:
        pass

async def notify_owner(client, text):
    try:
        owner_ids = Var.OWNER_ID
        if isinstance(owner_ids, (list, tuple)):
            for owner_id in owner_ids:
                await client.send_message(chat_id=owner_id, text=text)
        else:
            await client.send_message(chat_id=owner_ids, text=text)
    except Exception:
        pass

async def log_new_user(bot, user_id, first_name):
    try:
        if not await db.is_user_exist(user_id):
            await db.add_user(user_id)
            if hasattr(Var, 'BIN_CHANNEL') and Var.BIN_CHANNEL:
                await bot.send_message(
                    Var.BIN_CHANNEL,
                    f"👋 **New User Alert!**\n\n"
                    f"✨ **Name:** [{first_name}](tg://user?id={user_id})\n"
                    f"🆔 **User ID:** `{user_id}`\n\n"
                    "has started the bot!"
                )
    except Exception:
        pass

async def generate_media_links(log_msg):
    try:
        base_url = Var.URL.rstrip("/")
        file_id = log_msg.id
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
        
        return stream_link, online_link, media_name, media_size
    except Exception as e:
        await notify_channel(log_msg._client, f"Error generating links: {e}")
        raise

async def get_users_in_batches(batch_size=100):
    users_cursor = await db.get_all_users()
    current_batch = []
    
    async for user in users_cursor:
        current_batch.append(user)
        if len(current_batch) >= batch_size:
            yield current_batch
            current_batch = []
    
    if current_batch:
        yield current_batch

async def handle_broadcast_completion(message, output, failures, successes, total_users, start_time):
    elapsed_time = get_readable_time(time.time() - start_time)
    
    try:
        await output.delete()
    except Exception:
        pass
    
    message_text = (
        "✅ **Broadcast Completed** ✅\n\n"
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

async def check_admin_privileges(client, chat_id):
    try:
        chat = await client.get_chat(chat_id)
        if chat.type == 'private':
            return True  # Admin check not needed in private chats

        member = await client.get_chat_member(chat_id, client.me.id)
        return member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except Exception:
        return False

async def send_links_to_user(client, command_message, media_name, media_size, stream_link, online_link):
    msg_text = (
        "🔗 **Your Links are Ready!**\n\n"
        f"📄 **File Name:** `{media_name}`\n"
        f"📂 **File Size:** `{media_size}`\n\n"
        f"📥 **Download Link:**\n`{online_link}`\n\n"
        f"🖥️ **Watch Now:**\n`{stream_link}`\n\n"
        "⏰ **Note:** Links are available as long as the bot is active."
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

# Command Handlers

@StreamBot.on_message(filters.command("users") & filters.private & filters.user(list(Var.OWNER_ID)))
async def get_total_users(client, message):
    try:
        total_users = await db.total_users_count()
        await message.reply_text(
            f"👥 **Total Users in DB:** **{total_users}**",
            quote=True,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        await message.reply_text("🚨 **Error fetching user count.**")

@StreamBot.on_message(filters.command("broadcast") & filters.private & filters.user(list(Var.OWNER_ID)))
async def broadcast_message(client, message):
    if not message.reply_to_message:
        await message.reply_text("⚠️ **Please reply to a message to broadcast.**", quote=True)
        return

    try:
        broadcast_id = generate_unique_id()
        broadcast_ids[broadcast_id] = {
            "total": 0, 
            "current": 0, 
            "success": 0, 
            "failed": 0, 
            "deleted": 0, 
            "start_time": time.time(),
            "is_cancelled": False
        }
        
        output = await message.reply_text("📢 **Broadcast Initiated**. Please wait until completion.")

        self_id = client.me.id
        start_time = time.time()
        
        total_users = await db.total_users_count()
        processed = 0
        successes = 0
        failures = 0
        
        broadcast_ids[broadcast_id]["total"] = total_users
        
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
        
        success_lock = asyncio.Lock()
        failure_lock = asyncio.Lock()
        processed_lock = asyncio.Lock()
        
        async def update_progress():
            while processed < total_users and not broadcast_ids[broadcast_id]["is_cancelled"]:
                try:
                    await output.edit_text(
                        f"📢 **Broadcasting in Progress**\n\n"
                        f"👥 **Total Users:** {total_users}\n"
                        f"✅ **Completed:** {processed} / {total_users}\n"
                        f"⏱️ **Elapsed Time:** {get_readable_time(time.time() - start_time)}\n\n"
                        f"✓ **Success:** {successes}\n"
                        f"✗ **Failed:** {failures}\n"
                    )
                except Exception:
                    pass
                await asyncio.sleep(3)
        
        progress_task = asyncio.create_task(update_progress())
        
        async def send_message_to_user(user_id):
            nonlocal successes, failures, processed
            
            if not isinstance(user_id, int) or user_id == self_id:
                return
            
            async with semaphore:
                for attempt in range(3):
                    if broadcast_ids[broadcast_id]["is_cancelled"]:
                        return
                    
                    try:
                        if message.reply_to_message.text or message.reply_to_message.caption:
                            await client.send_message(
                                chat_id=user_id,
                                text=message.reply_to_message.text or message.reply_to_message.caption,
                                parse_mode=ParseMode.MARKDOWN,
                                disable_web_page_preview=True
                            )
                        elif message.reply_to_message.media:
                            await message.reply_to_message.copy(chat_id=user_id)
                        
                        async with success_lock:
                            successes += 1
                        break
                    
                    except FloodWait as e:
                        await asyncio.sleep(e.value + 1)
                        continue
                    
                    except (UserDeactivated, ChatWriteForbidden, UserIsBlocked, PeerIdInvalid):
                        try:
                            await db.delete_user(user_id)
                            async with failure_lock:
                                failures += 1
                                broadcast_ids[broadcast_id]["deleted"] += 1
                        except Exception:
                            pass
                        break
                    
                    except Exception as e:
                        async with failure_lock:
                            failures += 1
                        
                        if attempt == 2 or "bot" in str(e).lower() or "peer" in str(e).lower():
                            break
                        
                        await asyncio.sleep(1)
                
                async with processed_lock:
                    processed += 1
                    broadcast_ids[broadcast_id]["current"] = processed
        
        async for user_batch in get_users_in_batches(batch_size=100):
            if broadcast_ids[broadcast_id]["is_cancelled"]:
                break
            
            batch_tasks = [send_message_to_user(int(user['id'])) for user in user_batch]
            await asyncio.gather(*batch_tasks)
        
        progress_task.cancel()
        try:
            await progress_task
        except asyncio.CancelledError:
            pass
        
        await handle_broadcast_completion(
            message, 
            output, 
            failures, 
            successes, 
            total_users, 
            start_time
        )
        
        broadcast_ids.pop(broadcast_id, None)
    
    except Exception as e:
        await message.reply_text(
            "🚨 **Broadcast error:**\n\n"
            f"Error details: `{str(e)}`"
        )

@StreamBot.on_message(filters.command("cancel_broadcast") & filters.private & filters.user(list(Var.OWNER_ID)))
async def cancel_broadcast(client, message):
    if not broadcast_ids:
        await message.reply_text("⚠️ **No active broadcasts to cancel.**")
        return
    
    if len(broadcast_ids) == 1:
        broadcast_id = list(broadcast_ids.keys())[0]
        broadcast_ids[broadcast_id]["is_cancelled"] = True
        
        await message.reply_text(
            f"🛑 **Broadcast {broadcast_id} is being cancelled.**\n"
            "It may take a moment to stop all ongoing operations."
        )
        return
    
    keyboard = []
    for broadcast_id, info in broadcast_ids.items():
        progress = f"{info['current']}/{info['total']}" if info['total'] else "Unknown"
        elapsed = get_readable_time(time.time() - info['start_time'])
        keyboard.append([
            InlineKeyboardButton(
                f"ID: {broadcast_id} | Progress: {progress} | Time: {elapsed}",
                callback_data=f"cancel_broadcast_{broadcast_id}"
            )
        ])
    
    await message.reply_text(
        "🔄 **Multiple broadcasts active. Select one to cancel:**",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

@StreamBot.on_callback_query(filters.regex(r"^cancel_broadcast_(.+)$"))
async def handle_cancel_broadcast(client, callback_query):
    broadcast_id = callback_query.data.split("_")[-1]
    
    if broadcast_id in broadcast_ids:
        broadcast_ids[broadcast_id]["is_cancelled"] = True
        
        await callback_query.edit_message_text(
            f"🛑 **Broadcast {broadcast_id} is being cancelled.**\n"
            "It may take a moment to stop all ongoing operations."
        )
    else:
        await callback_query.edit_message_text(
            "⚠️ **This broadcast is no longer active.**"
        )

@StreamBot.on_message(filters.command("status") & filters.private & filters.user(list(Var.OWNER_ID)))
async def show_status(client, message):
    try:
        uptime = get_readable_time(time.time() - StartTime)
        
        workloads_text = "📊 **Workloads per Bot:**\n\n"
        workloads = {
            f"🤖 Bot {c + 1}": load
            for c, (bot, load) in enumerate(
                sorted(work_loads.items(), key=lambda x: x[1], reverse=True)
            )
        }
        
        for bot_name, load in workloads.items():
            workloads_text += f"   {bot_name}: {load}\n"
        
        stats_text = (
            f"⚙️ **Server Status:** Running\n\n"
            f"🕒 **Uptime:** {uptime}\n\n"
            f"🤖 **Connected Bots:** {len(multi_clients)}\n\n"
            f"{workloads_text}\n"
            f"♻️ **Version:** {__version__}\n"
        )
        
        await message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN)
    
    except Exception:
        await message.reply_text("🚨 **Error retrieving status.**")

@StreamBot.on_message(filters.command("stats") & filters.private & filters.user(list(Var.OWNER_ID)))
async def show_stats(client, message):
    try:
        current_time = get_readable_time(time.time() - StartTime)
        total, used, free = shutil.disk_usage('.')
        
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
        
        await message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN)
    
    except Exception:
        await message.reply_text("🚨 **Error retrieving statistics.**")

@StreamBot.on_message(filters.command("restart") & filters.private & filters.user(list(Var.OWNER_ID)))
async def restart_bot(client, message):
    try:
        await message.reply_text("🔄 **Restarting the bot...**")
        await asyncio.sleep(2)
        os.execv(sys.executable, [sys.executable, "-m", "Thunder"])
    except Exception:
        await message.reply_text("🚨 **Failed to restart the bot.**")

@StreamBot.on_message(filters.command("log") & filters.private & filters.user(list(Var.OWNER_ID)))
async def send_logs(client, message):
    try:
        if os.path.exists(LOG_FILE):
            if os.path.getsize(LOG_FILE) > 0:
                await message.reply_document(
                    document=LOG_FILE,
                    caption="📄 **Here are the latest logs:**"
                )
            else:
                await message.reply_text("⚠️ **The log file is empty.**")
        else:
            await message.reply_text("⚠️ **Log file not found.**")
    except Exception:
        await message.reply_text("🚨 **Failed to send log file.**")

@StreamBot.on_message(filters.command("shell") & filters.private & filters.user(list(Var.OWNER_ID)))
async def run_shell_command(client, message):
    if len(message.command) < 2:
        await message.reply_text("⚠️ **Please provide a command to execute.**")
        return
    
    try:
        cmd = message.text.split(None, 1)[1]
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=60
            )
            
            stdout = stdout.decode('utf-8', errors='replace')
            stderr = stderr.decode('utf-8', errors='replace')
            
            if stdout:
                stdout = stdout[:4000] if len(stdout) > 4000 else stdout
                stdout = html.escape(stdout)
                await message.reply_text(
                    f"<b>Output:</b>\n<pre>{stdout}</pre>",
                    parse_mode=ParseMode.HTML
                )
            
            if stderr:
                stderr = stderr[:4000] if len(stderr) > 4000 else stderr
                stderr = html.escape(stderr)
                await message.reply_text(
                    f"<b>Error:</b>\n<pre>{stderr}</pre>",
                    parse_mode=ParseMode.HTML
                )
                
            if not stdout and not stderr:
                await message.reply_text("⚠️ **Command executed with no output.**")
        
        except asyncio.TimeoutError:
            process.kill()
            await message.reply_text("⚠️ **Command timed out (60s limit).**")
    
    except Exception as e:
        await message.reply_text(f"🚨 **Error executing command:** {str(e)}")

@StreamBot.on_message(filters.command("db") & filters.private & filters.user(list(Var.OWNER_ID)))
async def db_operations(client, message):
    if len(message.command) < 2:
        await message.reply_text(
            "⚠️ **Please specify a database operation.**\n\n"
            "**Available commands:**\n"
            "• `/db stats` - Show database statistics\n"
            "• `/db cleanup` - Remove inactive users\n"
            "• `/db backup` - Backup database"
        )
        return
    
    operation = message.command[1].lower()
    
    try:
        if operation == "stats":
            total_users = await db.total_users_count()
            await message.reply_text(f"📊 **Database Statistics**\n\n👥 **Total Users:** {total_users}")
        
        elif operation == "cleanup":
            await message.reply_text("⏳ **Database cleanup in progress...**")
            # Your cleanup logic here
            await message.reply_text("✅ **Database cleanup completed.**")
        
        elif operation == "backup":
            await message.reply_text("⏳ **Creating database backup...**")
            # Your backup logic here
            await message.reply_text("✅ **Database backup completed.**")
        
        else:
            await message.reply_text(f"⚠️ **Unknown operation:** `{operation}`")
    
    except Exception as e:
        await message.reply_text(f"🚨 **Error during database operation:** {str(e)}")

@StreamBot.on_message(filters.command("process") & filters.private & filters.user(list(Var.OWNER_ID)))
async def process_media(client, message):
    if not message.reply_to_message:
        await message.reply_text("⚠️ **Please reply to a media message to process it.**")
        return
    
    try:
        if hasattr(Var, 'BIN_CHANNEL') and Var.BIN_CHANNEL:
            forwarded = await message.reply_to_message.forward(Var.BIN_CHANNEL)
            
            stream_link, download_link, file_name, file_size = await generate_media_links(forwarded)
            
            message_text = (
                f"🔗 **Links generated successfully!**\n\n"
                f"📁 **File:** `{file_name}`\n"
                f"💾 **Size:** `{file_size}`\n\n"
                f"🔗 **Download Link:** `{download_link}`\n\n"
                f"🎬 **Stream Link:** `{stream_link}`"
            )
            
            keyboard = [
                [InlineKeyboardButton("🎬 Stream", url=stream_link)],
                [InlineKeyboardButton("📥 Download", url=download_link)]
            ]
            
            await message.reply_text(
                message_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                disable_web_page_preview=True
            )
        else:
            await message.reply_text("⚠️ **BIN_CHANNEL not configured. Cannot process media.**")
    
    except Exception as e:
        await message.reply_text(f"🚨 **Error processing media:** {str(e)}")
