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
import uuid
from datetime import datetime
from pyrogram.client import Client
from pyrogram import filters, StopPropagation
from pyrogram.enums import ParseMode
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    LinkPreviewOptions
)
from pyrogram.errors import (
    FloodWait,
    UserDeactivated,
    ChatWriteForbidden,
    UserIsBlocked,
    PeerIdInvalid,
    FileReferenceExpired,
    FileReferenceInvalid,
    BadRequest
)

from Thunder.bot import StreamBot, multi_clients, work_loads
from Thunder.vars import Var
from Thunder import StartTime, __version__
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.time_format import get_readable_time
from Thunder.utils.logger import logger, LOG_FILE
from Thunder.utils.tokens import authorize, deauthorize, list_allowed
from Thunder.utils.database import db
from Thunder.utils.messages import *
from Thunder.utils.retry_api import retry_send_message

broadcast_ids = {}
RATE_LIMIT = 25
BATCH_SIZE = 30
MAX_CONCURRENT = 10

class RateLimiter:
    def __init__(self, rate=RATE_LIMIT):
        self.rate = rate
        self.tokens = rate
        self.last_update = time.time()
        self.lock = asyncio.Lock()
    
    async def acquire(self):
        async with self.lock:
            now = time.time()
            elapsed = now - self.last_update
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
            self.last_update = now
            
            if self.tokens >= 1:
                self.tokens -= 1
                return
            
            wait_time = (1 - self.tokens) / self.rate
            await asyncio.sleep(wait_time)
            self.tokens = 0

rate_limiter = RateLimiter()

def generate_unique_id(length=6):
    while True:
        random_id = ''.join(random.choices(string.ascii_letters + string.digits, k=length))
        if random_id not in broadcast_ids:
            return random_id

async def get_users_in_batches(batch_size=BATCH_SIZE):
    users_cursor = await db.get_all_users()
    current_batch = []
    async for user in users_cursor:
        current_batch.append(user)
        if len(current_batch) >= batch_size:
            yield current_batch
            current_batch = []
    if current_batch:
        yield current_batch

async def handle_flood_wait(e, attempt=0):
    wait_time = max(e.value, 1) * (1.5 ** attempt)
    wait_time = min(wait_time, 300)
    await asyncio.sleep(wait_time + random.uniform(0.1, 0.5))

@StreamBot.on_message(filters.command("users") & filters.private & filters.user(Var.OWNER_ID))
async def get_total_users(client, message):
    try:
        total_users = await db.total_users_count()
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]
        ])
        await message.reply_text(
            MSG_DB_STATS.format(total_users=total_users),
            quote=True,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
    except Exception:
        await message.reply_text(MSG_DB_ERROR)

@StreamBot.on_message(filters.command("broadcast") & filters.private & filters.user(Var.OWNER_ID))
async def broadcast_message(client, message):
    if not message.reply_to_message:
        await message.reply_text(MSG_INVALID_BROADCAST_CMD, quote=True)
        return

    broadcast_id = generate_unique_id()
    broadcast_ids[broadcast_id] = {
        "total": 0,
        "processed": 0,
        "success": 0,
        "failed": 0,
        "deleted": 0,
        "start_time": time.time(),
        "is_cancelled": False,
        "last_update": 0
    }

    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(MSG_BUTTON_CANCEL_BROADCAST, callback_data=f"cancel_broadcast_{broadcast_id}")]
    ])
    output = await message.reply_text(MSG_BROADCAST_START, reply_markup=reply_markup)

    try:
        start_time = time.time()
        total_users = await db.total_users_count()
        broadcast_ids[broadcast_id]["total"] = total_users
        
        stats = broadcast_ids[broadcast_id]
        self_id = client.me.id

        async def update_progress():
            while stats["processed"] < stats["total"] and not stats["is_cancelled"]:
                current_time = time.time()
                if current_time - stats["last_update"] >= 5:
                    try:
                        progress_text = MSG_BROADCAST_PROGRESS.format(
                            total_users=stats["total"],
                            processed=stats["processed"],
                            elapsed_time=get_readable_time(int(current_time - start_time)),
                            successes=stats["success"],
                            failures=stats["failed"]
                        )
                        await output.edit_text(progress_text)
                        stats["last_update"] = current_time
                    except BadRequest as e:
                        if "Message is not modified" not in str(e):
                            break
                    except Exception:
                        break
                await asyncio.sleep(2)

        progress_task = asyncio.create_task(update_progress())

        async def send_to_user(user_id: int):
            if stats["is_cancelled"] or user_id == self_id:
                return

            await rate_limiter.acquire()
            
            for attempt in range(3):
                if stats["is_cancelled"]:
                    return
                
                try:
                    if message.reply_to_message.text or message.reply_to_message.caption:
                        await client.send_message(
                            chat_id=user_id,
                            text=message.reply_to_message.text or message.reply_to_message.caption,
                            parse_mode=ParseMode.MARKDOWN,
                            link_preview_options=LinkPreviewOptions(is_disabled=True)
                        )
                    elif message.reply_to_message.media:
                        await message.reply_to_message.copy(chat_id=user_id)
                    
                    stats["success"] += 1
                    break
                    
                except FloodWait as e:
                    await handle_flood_wait(e, attempt)
                    continue
                    
                except (UserDeactivated, ChatWriteForbidden, UserIsBlocked, PeerIdInvalid):
                    try:
                        await db.delete_user(user_id)
                        stats["deleted"] += 1
                    except Exception:
                        pass
                    stats["failed"] += 1
                    break
                    
                except (FileReferenceExpired, FileReferenceInvalid):
                    if attempt == 2:
                        stats["failed"] += 1
                        break
                    await asyncio.sleep(1)
                    
                except Exception:
                    stats["failed"] += 1
                    break
            
            stats["processed"] += 1

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        
        async def process_user(user_id):
            async with semaphore:
                await send_to_user(int(user_id))

        async for user_batch in get_users_in_batches():
            if stats["is_cancelled"]:
                break
            
            batch_tasks = [process_user(user['id']) for user in user_batch]
            await asyncio.gather(*batch_tasks, return_exceptions=True)
            
            if not stats["is_cancelled"]:
                await asyncio.sleep(1)

        progress_task.cancel()
        try:
            await progress_task
        except asyncio.CancelledError:
            pass

        elapsed_time = get_readable_time(time.time() - start_time)
        
        try:
            await output.delete()
        except Exception:
            pass

        completion_text = MSG_BROADCAST_COMPLETE.format(
            elapsed_time=elapsed_time,
            total_users=stats["total"],
            successes=stats["success"],
            failures=stats["failed"],
            deleted_accounts=stats["deleted"]
        )
        
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(MSG_ADMIN_RESTART_BROADCAST, callback_data="restart_broadcast")]
        ])
        await message.reply_text(
            completion_text,
            parse_mode=ParseMode.MARKDOWN,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            reply_markup=reply_markup
        )
        
    except Exception as e:
        await message.reply_text(
            MSG_BROADCAST_FAILED.format(error=str(e), error_id=uuid.uuid4().hex[:8])
        )
    finally:
        broadcast_ids.pop(broadcast_id, None)

@StreamBot.on_message(filters.command("cancel_broadcast") & filters.private & filters.user(Var.OWNER_ID))
async def cancel_broadcast(client, message):
    if not broadcast_ids:
        await message.reply_text(MSG_NO_ACTIVE_BROADCASTS)
        return

    if len(broadcast_ids) == 1:
        broadcast_id = list(broadcast_ids.keys())[0]
        broadcast_ids[broadcast_id]["is_cancelled"] = True
        await message.reply_text(MSG_BROADCAST_CANCEL.format(broadcast_id=broadcast_id))
        return

    keyboard = []
    for broadcast_id, info in broadcast_ids.items():
        progress = f"{info['processed']}/{info['total']}" if info['total'] else "Unknown"
        elapsed = get_readable_time(time.time() - info['start_time'])
        keyboard.append([
            InlineKeyboardButton(
                f"Cancel {broadcast_id} ({progress}) - {elapsed}",
                callback_data=f"cancel_broadcast_{broadcast_id}"
            )
        ])
    
    await message.reply_text(
        MSG_MULTIPLE_BROADCASTS,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

@StreamBot.on_callback_query(filters.regex(r"^cancel_broadcast_(.+)$"))
async def handle_cancel_broadcast(client, callback_query):
    broadcast_id = callback_query.data.split("_")[-1]
    if broadcast_id in broadcast_ids:
        broadcast_ids[broadcast_id]["is_cancelled"] = True
        await callback_query.edit_message_text(
            MSG_CANCELLING_BROADCAST.format(broadcast_id=broadcast_id)
        )
    else:
        await callback_query.edit_message_text(MSG_BROADCAST_NOT_FOUND)

@StreamBot.on_message(filters.command("status") & filters.private & filters.user(Var.OWNER_ID))
async def show_status(client, message):
    try:
        uptime = get_readable_time(int(time.time() - StartTime))
        workloads_text = MSG_ADMIN_BOT_WORKLOAD_HEADER
        workloads = {
            f"🔹 Bot {c + 1}": load
            for c, (bot, load) in enumerate(
                sorted(work_loads.items(), key=lambda x: x[1], reverse=True)
            )
        }
        
        for bot_name, load in workloads.items():
            workloads_text += MSG_ADMIN_BOT_WORKLOAD_ITEM.format(bot_name=bot_name, load=load)
        
        stats_text = MSG_SYSTEM_STATUS.format(
            uptime=uptime,
            active_bots=len(multi_clients),
            workloads=workloads_text,
            version=__version__
        )
        
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]
        ])
        await message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    except Exception:
        await message.reply_text(MSG_STATUS_ERROR)

@StreamBot.on_message(filters.command("stats") & filters.private & filters.user(Var.OWNER_ID))
async def show_stats(client, message):
    try:
        current_time = get_readable_time(int(time.time() - StartTime))
        total, used, free = shutil.disk_usage('.')
        
        stats_text = MSG_SYSTEM_STATS.format(
            uptime=current_time,
            total=humanbytes(total),
            used=humanbytes(used),
            free=humanbytes(free),
            upload=humanbytes(psutil.net_io_counters().bytes_sent),
            download=humanbytes(psutil.net_io_counters().bytes_recv)
        )
        
        stats_text += MSG_PERFORMANCE_STATS.format(
            cpu_percent=psutil.cpu_percent(interval=0.5),
            ram_percent=psutil.virtual_memory().percent,
            disk_percent=psutil.disk_usage('.').percent
        )
        
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]
        ])
        await message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    except Exception:
        await message.reply_text(MSG_STATUS_ERROR)

@StreamBot.on_message(filters.command("restart") & filters.private & filters.user(Var.OWNER_ID))
async def restart_bot(client, message):
    try:
        sent_message = await message.reply_text(MSG_RESTARTING)
        await db.add_restart_message(sent_message.id, message.chat.id)
        os.execv(sys.executable, [sys.executable, "-m", "Thunder"])
    except Exception:
        await message.reply_text(MSG_RESTART_FAILED)

@StreamBot.on_message(filters.command("log") & filters.private & filters.user(Var.OWNER_ID))
async def send_logs(client, message):
    try:
        if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0:
            await message.reply_text(MSG_LOG_FILE_MISSING if not os.path.exists(LOG_FILE) else MSG_LOG_FILE_EMPTY)
            return
        
        await client.send_document(
            chat_id=message.chat.id,
            document=LOG_FILE,
            caption=MSG_LOG_FILE_CAPTION,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        await message.reply_text(MSG_LOG_ERROR.format(error=str(e)))

@StreamBot.on_message(filters.command("authorize") & filters.private & filters.user(Var.OWNER_ID))
async def authorize_command(client, message):
    if len(message.command) < 2:
        await message.reply_text(MSG_AUTHORIZE_USAGE)
        return
    
    try:
        user_id_to_authorize = int(message.command[1])
        authorized = await authorize(user_id_to_authorize, authorized_by=message.from_user.id)
        await message.reply_text(
            MSG_AUTHORIZE_SUCCESS.format(user_id=user_id_to_authorize) if authorized 
            else MSG_AUTHORIZE_FAILED.format(user_id=user_id_to_authorize)
        )
    except ValueError:
        await message.reply_text(MSG_INVALID_USER_ID)
    except Exception:
        await message.reply_text(MSG_ERROR_GENERIC)

@StreamBot.on_message(filters.command("deauthorize") & filters.private & filters.user(Var.OWNER_ID))
async def deauthorize_command(client, message):
    if len(message.command) < 2:
        await message.reply_text(MSG_DEAUTHORIZE_USAGE)
        return
    
    try:
        user_id_to_deauthorize = int(message.command[1])
        deauthorized = await deauthorize(user_id_to_deauthorize)
        await message.reply_text(
            MSG_DEAUTHORIZE_SUCCESS.format(user_id=user_id_to_deauthorize) if deauthorized 
            else MSG_DEAUTHORIZE_FAILED.format(user_id=user_id_to_deauthorize)
        )
    except ValueError:
        await message.reply_text(MSG_INVALID_USER_ID)
    except Exception:
        await message.reply_text(MSG_ERROR_GENERIC)

@StreamBot.on_message(filters.command("listauth") & filters.private & filters.user(Var.OWNER_ID))
async def list_authorized_command(client, message):
    try:
        authorized_users = await list_allowed()
        if not authorized_users:
            await message.reply_text(MSG_NO_AUTH_USERS)
            return

        message_text = MSG_ADMIN_AUTH_LIST_HEADER
        for i, user in enumerate(authorized_users, 1):
            message_text += MSG_AUTH_USER_INFO.format(
                i=i,
                user_id=user['user_id'],
                authorized_by=user['authorized_by'],
                auth_time=user['authorized_at']
            )
        
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]
        ])
        await message.reply_text(
            message_text,
            parse_mode=ParseMode.MARKDOWN,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            reply_markup=reply_markup
        )
    except Exception:
        await message.reply_text(MSG_ERROR_GENERIC)

@StreamBot.on_message(filters.command("ban") & filters.private & filters.user(Var.OWNER_ID))
async def ban_user_command(client, message):
    if len(message.command) < 2:
        await message.reply_text(MSG_BAN_USAGE)
        return

    try:
        user_id_to_ban = int(message.command[1])
        if user_id_to_ban in (Var.OWNER_ID if isinstance(Var.OWNER_ID, list) else [Var.OWNER_ID]):
            await message.reply_text(MSG_CANNOT_BAN_OWNER)
            return

        reason = " ".join(message.command[2:]) if len(message.command) > 2 else MSG_ADMIN_NO_BAN_REASON
        ban_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        await db.add_banned_user(
            user_id=user_id_to_ban,
            reason=reason,
            ban_time=ban_time,
            banned_by=message.from_user.id
        )
        
        reply_text = MSG_ADMIN_USER_BANNED.format(user_id=user_id_to_ban)
        if reason != MSG_ADMIN_NO_BAN_REASON:
            reply_text += MSG_BAN_REASON_SUFFIX.format(reason=reason)
        await message.reply_text(reply_text)
        
        try:
            await retry_send_message(client, chat_id=user_id_to_ban, text=MSG_USER_BANNED_NOTIFICATION)
        except Exception:
            pass
            
    except ValueError:
        await message.reply_text(MSG_INVALID_USER_ID)
    except Exception as e:
        await message.reply_text(MSG_BAN_ERROR.format(error=str(e)))

@StreamBot.on_message(filters.command("unban") & filters.private & filters.user(Var.OWNER_ID))
async def unban_user_command(client, message):
    if len(message.command) < 2:
        await message.reply_text(MSG_UNBAN_USAGE)
        return

    try:
        user_id_to_unban = int(message.command[1])
        removed = await db.remove_banned_user(user_id=user_id_to_unban)
        
        if removed:
            await message.reply_text(MSG_ADMIN_USER_UNBANNED.format(user_id=user_id_to_unban))
            try:
                await retry_send_message(client, chat_id=user_id_to_unban, text=MSG_USER_UNBANNED_NOTIFICATION)
            except Exception:
                pass
        else:
            await message.reply_text(MSG_USER_NOT_IN_BAN_LIST.format(user_id=user_id_to_unban))
            
    except ValueError:
        await message.reply_text(MSG_INVALID_USER_ID)
    except Exception as e:
        await message.reply_text(MSG_UNBAN_ERROR.format(error=str(e)))

@StreamBot.on_message(filters.command("shell") & filters.private & filters.user(Var.OWNER_ID))
async def run_shell_command(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text(MSG_SHELL_USAGE, parse_mode=ParseMode.HTML)
        return

    command_to_run = " ".join(message.command[1:])
    reply_msg = await message.reply_text(
        MSG_SHELL_EXECUTING.format(command=html.escape(command_to_run)),
        parse_mode=ParseMode.HTML,
        quote=True
    )

    try:
        process = await asyncio.create_subprocess_shell(
            command_to_run,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        output = ""
        if stdout:
            output += MSG_SHELL_OUTPUT_STDOUT.format(output=html.escape(stdout.decode().strip()))
        if stderr:
            output += MSG_SHELL_OUTPUT_STDERR.format(error=html.escape(stderr.decode().strip()))
        if not output:
            output = MSG_SHELL_NO_OUTPUT
            
    except Exception as e:
        output = MSG_SHELL_ERROR.format(error=html.escape(str(e)))

    if len(output) > 4096:
        try:
            filename = f"shell_output_{int(time.time())}.txt"
            with open(filename, "w", encoding="utf-8") as file:
                if stdout:
                    file.write(f"STDOUT:\n{stdout.decode()}\n\n")
                if stderr:
                    file.write(f"STDERR:\n{stderr.decode()}")
            
            await client.send_document(
                chat_id=message.chat.id,
                document=filename,
                caption=MSG_SHELL_OUTPUT.format(command=html.escape(command_to_run)),
                parse_mode=ParseMode.HTML
            )
            os.remove(filename)
        except Exception:
            await message.reply_text("Output too large and file creation failed", parse_mode=ParseMode.HTML)
    else:
        await message.reply_text(output, parse_mode=ParseMode.HTML)

    try:
        await reply_msg.delete()
    except Exception:
        pass

@StreamBot.on_callback_query(filters.regex("close_panel"))
async def close_panel(client, callback_query):
    try:
        await callback_query.answer()
        await callback_query.message.delete()
        
        if callback_query.message.reply_to_message:
            try:
                ctx = callback_query.message.reply_to_message
                await ctx.delete()
                if ctx.reply_to_message:
                    await ctx.reply_to_message.delete()
            except Exception:
                pass
                
    except Exception:
        pass
    finally:
        raise StopPropagation

@StreamBot.on_callback_query(filters.regex("restart_broadcast"))
async def restart_broadcast(client, callback_query):
    try:
        await callback_query.edit_message_text(
            MSG_ERROR_BROADCAST_INSTRUCTION,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass
