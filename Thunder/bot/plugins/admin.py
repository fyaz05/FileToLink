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
from typing import AsyncGenerator, List

from pyrogram.client import Client
from pyrogram import filters, StopPropagation
from pyrogram.enums import ParseMode
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery,
    LinkPreviewOptions
)
from pyrogram.errors import (
    FloodWait,
    UserDeactivated,
    ChatWriteForbidden,
    UserIsBlocked,
    PeerIdInvalid,
    MessageNotModified,
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

broadcast_ids = {}
BATCH_SIZE = 30
MAX_CONCURRENT = 10


def generate_unique_id(length: int = 6) -> str:
    while True:
        random_id = ''.join(random.choices(string.ascii_letters + string.digits, k=length))
        if random_id not in broadcast_ids:
            return random_id

async def get_users_in_batches(batch_size: int = BATCH_SIZE) -> AsyncGenerator[List[dict], None]:
    users_cursor = await db.get_all_users()
    current_batch = []
    async for user in users_cursor:
        current_batch.append(user)
        if len(current_batch) >= batch_size:
            yield current_batch
            current_batch = []
    if current_batch:
        yield current_batch


@StreamBot.on_message(filters.command("users") & filters.private & filters.user(list(Var.OWNER_ID) if isinstance(Var.OWNER_ID, list) else Var.OWNER_ID))
async def get_total_users(client: Client, message: Message):
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
    except Exception as e:
        logger.error(f"Error in get_total_users: {e}", exc_info=True)
        await message.reply_text(MSG_DB_ERROR)

@StreamBot.on_message(filters.command("broadcast") & filters.private & filters.user(list(Var.OWNER_ID) if isinstance(Var.OWNER_ID, list) else Var.OWNER_ID))
async def broadcast_message(client: Client, message: Message):
    if not message.reply_to_message:
        await message.reply_text(MSG_INVALID_BROADCAST_CMD, quote=True)
        return

    broadcast_id = generate_unique_id()
    broadcast_ids[broadcast_id] = {
        "total": 0, "processed": 0, "success": 0, "failed": 0, "deleted": 0,
        "start_time": time.time(), "is_cancelled": False, "last_update": time.time()
    }

    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(MSG_BUTTON_CANCEL_BROADCAST, callback_data=f"cancel_broadcast_{broadcast_id}")]])

    output_msg = await message.reply_text(MSG_BROADCAST_START, reply_markup=reply_markup)
    if not output_msg:
        logger.error("Failed to send initial broadcast status message.")
        broadcast_ids.pop(broadcast_id, None)
        return

    try:
        s_time = time.time()
        total_users_count = await db.total_users_count()
        broadcast_ids[broadcast_id]["total"] = total_users_count
        stats = broadcast_ids[broadcast_id]
        my_id = client.me.id if client.me else None

        async def update_progress():
            while stats["processed"] < stats["total"] and not stats["is_cancelled"]:
                c_time = time.time()
                if c_time - stats["last_update"] >= 5:
                    try:
                        prog_text = MSG_BROADCAST_PROGRESS.format(
                            total_users=stats["total"], processed=stats["processed"],
                            elapsed_time=get_readable_time(int(c_time - s_time)),
                            successes=stats["success"], failures=stats["failed"]
                        )
                        await output_msg.edit_text(prog_text)
                        stats["last_update"] = c_time
                    except BadRequest as e_br:
                        if "Message is not modified" not in str(e_br):
                            logger.warning(f"BadRequest editing broadcast progress: {e_br}")
                            break
                    except Exception as e_upd:
                        logger.error(f"Error updating broadcast progress: {e_upd}")
                        break
                await asyncio.sleep(1)

        progress_task = asyncio.create_task(update_progress())

        async def send_to_user(user_id_to_send: int):
            if stats["is_cancelled"] or user_id_to_send == my_id: return


            for attempt_num in range(2):
                if stats["is_cancelled"]: return
                try:
                    if message.reply_to_message.text or message.reply_to_message.caption:
                        await client.send_message(user_id_to_send,
                            message.reply_to_message.text or message.reply_to_message.caption,
                            parse_mode=ParseMode.MARKDOWN,
                            link_preview_options=LinkPreviewOptions(is_disabled=True))
                    elif message.reply_to_message.media:
                        await message.reply_to_message.copy(chat_id=user_id_to_send)
                    stats["success"] += 1; break
                except FloodWait as e_fw:
                    logger.warning(f"FloodWait sending broadcast to user {user_id_to_send} (attempt {attempt_num+1}): {e_fw}. Sleeping {e_fw.value}s")
                    await asyncio.sleep(float(e_fw.value) if isinstance(e_fw.value, (int, float)) else 10.0)
                    if attempt_num == 1: stats["failed"] += 1
                except (UserDeactivated, UserIsBlocked, PeerIdInvalid, ChatWriteForbidden) as e_user_state:
                    logger.debug(f"User {user_id_to_send} is {type(e_user_state).__name__}. Removing from DB.")
                    try: await db.delete_user(user_id_to_send); stats["deleted"] += 1
                    except Exception as e_db_del: logger.error(f"Error deleting user {user_id_to_send} from DB: {e_db_del}")
                    stats["failed"] += 1; break
                except (FileReferenceExpired, FileReferenceInvalid) as e_fr_exp:
                    logger.warning(f"FileReference error for broadcast to {user_id_to_send} (attempt {attempt_num+1}): {e_fr_exp}")
                    if attempt_num == 1: stats["failed"] += 1; break
                    await asyncio.sleep(2)
                except (OSError, asyncio.TimeoutError, ConnectionError) as e_network:
                    logger.warning(f"Network error sending broadcast to {user_id_to_send} (attempt {attempt_num+1}): {e_network}")
                    if attempt_num == 1: stats["failed"] += 1
                except Exception as e_other:
                    logger.error(f"Unhandled error sending broadcast to {user_id_to_send} (attempt {attempt_num+1}): {e_other}", exc_info=True)
                    if attempt_num == 1: stats["failed"] += 1
                    break
            stats["processed"] += 1

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        async def process_user_task(user_id_to_process: int):
            async with semaphore: await send_to_user(user_id_to_process)

        async for user_batch_list in get_users_in_batches():
            if stats["is_cancelled"]: break
            await asyncio.gather(*[process_user_task(user['id']) for user in user_batch_list], return_exceptions=False)
            if not stats["is_cancelled"]: await asyncio.sleep(0.5)

        progress_task.cancel()
        try: await progress_task
        except asyncio.CancelledError: logger.debug("Broadcast progress task cancelled.")

        if output_msg:
            try: await output_msg.delete()
            except Exception as e_del_out: logger.warning(f"Could not delete broadcast status message: {e_del_out}")

        elapsed_time_str = get_readable_time(int(time.time() - s_time))
        completion_text_str = MSG_BROADCAST_COMPLETE.format(
            elapsed_time=elapsed_time_str, total_users=stats["total"],
            successes=stats["success"], failures=stats["failed"], deleted_accounts=stats["deleted"])

        await message.reply_text(completion_text_str, parse_mode=ParseMode.MARKDOWN,
                                 link_preview_options=LinkPreviewOptions(is_disabled=True),
                                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(MSG_ADMIN_RESTART_BROADCAST, callback_data="restart_broadcast")]]))
    except Exception as e_main:
        logger.error(f"Major error in broadcast_message: {e_main}", exc_info=True)
        error_report_id = uuid.uuid4().hex[:8]
        err_msg_to_user = MSG_BROADCAST_FAILED.format(error=str(e_main), error_id=error_report_id)
        if output_msg: await output_msg.edit_text(err_msg_to_user)
        else: await message.reply_text(err_msg_to_user)
    finally:
        if broadcast_id in broadcast_ids:
            broadcast_ids.pop(broadcast_id, None)

@StreamBot.on_message(filters.command("cancel_broadcast") & filters.private & filters.user(list(Var.OWNER_ID) if isinstance(Var.OWNER_ID, list) else Var.OWNER_ID))
async def cancel_broadcast(client: Client, message: Message):
    if not broadcast_ids: await message.reply_text(MSG_NO_ACTIVE_BROADCASTS); return

    if len(broadcast_ids) == 1:
        b_id = list(broadcast_ids.keys())[0]
        broadcast_ids[b_id]["is_cancelled"] = True
        await message.reply_text(MSG_BROADCAST_CANCEL.format(broadcast_id=b_id)); return

    keyboard_buttons = []
    for b_id, info_dict in broadcast_ids.items():
        progress_str = f"{info_dict['processed']}/{info_dict['total']}" if info_dict['total'] else "N/A"
        elapsed_str = get_readable_time(time.time() - info_dict['start_time'])
        keyboard_buttons.append([InlineKeyboardButton(f"Cancel {b_id} ({progress_str}) - {elapsed_str}", callback_data=f"cancel_broadcast_{b_id}")])
    await message.reply_text(MSG_MULTIPLE_BROADCASTS, reply_markup=InlineKeyboardMarkup(keyboard_buttons))

@StreamBot.on_callback_query(filters.regex(r"^cancel_broadcast_(.+)$"))
async def handle_cancel_broadcast(client: Client, cb_query: CallbackQuery):
    b_id = cb_query.data.replace("cancel_broadcast_", "")
    if b_id in broadcast_ids:
        broadcast_ids[b_id]["is_cancelled"] = True
        await cb_query.edit_message_text(MSG_CANCELLING_BROADCAST.format(broadcast_id=b_id))
    else: await cb_query.edit_message_text(MSG_BROADCAST_NOT_FOUND)

@StreamBot.on_message(filters.command("status") & filters.private & filters.user(list(Var.OWNER_ID) if isinstance(Var.OWNER_ID, list) else Var.OWNER_ID))
async def show_status(client: Client, message: Message):
    try:
        uptime_str = get_readable_time(int(time.time() - StartTime))
        workload_text_str = MSG_ADMIN_BOT_WORKLOAD_HEADER
        workloads_dict = {f"🔹 Bot {c + 1}": load for c, (bot, load) in enumerate(sorted(work_loads.items(), key=lambda x: x[1], reverse=True))}
        for bot_name_str, load_val in workloads_dict.items():
            workload_text_str += MSG_ADMIN_BOT_WORKLOAD_ITEM.format(bot_name=bot_name_str, load=load_val)

        status_text_str = MSG_SYSTEM_STATUS.format(uptime=uptime_str, active_bots=len(multi_clients), workloads=workload_text_str, version=__version__)
        await message.reply_text(status_text_str, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]]))
    except Exception as e:
        logger.error(f"Error in show_status: {e}", exc_info=True)
        await message.reply_text(MSG_STATUS_ERROR)

@StreamBot.on_message(filters.command("stats") & filters.private & filters.user(list(Var.OWNER_ID) if isinstance(Var.OWNER_ID, list) else Var.OWNER_ID))
async def show_stats(client: Client, message: Message):
    try:
        current_time_str = get_readable_time(int(time.time() - StartTime))
        total_disk, used_disk, free_disk = shutil.disk_usage('.')
        active_bots = len(multi_clients)  # Get active bot count

        stats_text_val = MSG_SYSTEM_STATS.format(
            uptime=current_time_str,
            total=humanbytes(total_disk),
            used=humanbytes(used_disk),
            free=humanbytes(free_disk),
            upload=humanbytes(psutil.net_io_counters().bytes_sent),
            download=humanbytes(psutil.net_io_counters().bytes_recv)
        )
        stats_text_val += MSG_PERFORMANCE_STATS.format(
            cpu_percent=psutil.cpu_percent(interval=0.5),
            ram_percent=psutil.virtual_memory().percent,
            disk_percent=psutil.disk_usage('.').percent
        )

        await message.reply_text(stats_text_val, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]]))
    except Exception as e:
        logger.error(f"Error in show_stats: {e}", exc_info=True)
        await message.reply_text(MSG_STATUS_ERROR)

@StreamBot.on_message(filters.command("restart") & filters.private & filters.user(list(Var.OWNER_ID) if isinstance(Var.OWNER_ID, list) else Var.OWNER_ID))
async def restart_bot(client: Client, message: Message):
    try:
        sent_msg = await message.reply_text(MSG_RESTARTING)
        await db.add_restart_message(sent_msg.id, message.chat.id)
        os.execv(sys.executable, [sys.executable, "-m", "Thunder"])
    except Exception as e:
        logger.error(f"Error in restart_bot: {e}", exc_info=True)
        await message.reply_text(MSG_RESTART_FAILED)

@StreamBot.on_message(filters.command("log") & filters.private & filters.user(list(Var.OWNER_ID) if isinstance(Var.OWNER_ID, list) else Var.OWNER_ID))
async def send_logs(client: Client, message: Message):
    try:
        if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0:
            await message.reply_text(MSG_LOG_FILE_MISSING if not os.path.exists(LOG_FILE) else MSG_LOG_FILE_EMPTY); return

        await client.send_document(chat_id=message.chat.id, document=LOG_FILE, caption=MSG_LOG_FILE_CAPTION, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Error sending logs: {e}", exc_info=True)
        await message.reply_text(MSG_LOG_ERROR.format(error=str(e)))

@StreamBot.on_message(filters.command("authorize") & filters.private & filters.user(list(Var.OWNER_ID) if isinstance(Var.OWNER_ID, list) else Var.OWNER_ID))
async def authorize_command(client: Client, message: Message):
    if len(message.command) < 2: await message.reply_text(MSG_AUTHORIZE_USAGE); return
    try:
        user_id_to_auth = int(message.command[1])
        is_authorized = await authorize(user_id_to_auth, authorized_by=message.from_user.id)
        await message.reply_text(MSG_AUTHORIZE_SUCCESS.format(user_id=user_id_to_auth) if is_authorized else MSG_AUTHORIZE_FAILED.format(user_id=user_id_to_auth))
    except ValueError: await message.reply_text(MSG_INVALID_USER_ID)
    except Exception as e:
        logger.error(f"Error in authorize_command: {e}", exc_info=True)
        await message.reply_text(MSG_ERROR_GENERIC)

@StreamBot.on_message(filters.command("deauthorize") & filters.private & filters.user(list(Var.OWNER_ID) if isinstance(Var.OWNER_ID, list) else Var.OWNER_ID))
async def deauthorize_command(client: Client, message: Message):
    if len(message.command) < 2: await message.reply_text(MSG_DEAUTHORIZE_USAGE); return
    try:
        user_id_to_deauth = int(message.command[1])
        is_deauthorized = await deauthorize(user_id_to_deauth)
        await message.reply_text(MSG_DEAUTHORIZE_SUCCESS.format(user_id=user_id_to_deauth) if is_deauthorized else MSG_DEAUTHORIZE_FAILED.format(user_id=user_id_to_deauth))
    except ValueError: await message.reply_text(MSG_INVALID_USER_ID)
    except Exception as e:
        logger.error(f"Error in deauthorize_command: {e}", exc_info=True) # Added exc_info=True
        await message.reply_text(MSG_ERROR_GENERIC)

@StreamBot.on_message(filters.command("listauth") & filters.private & filters.user(list(Var.OWNER_ID) if isinstance(Var.OWNER_ID, list) else Var.OWNER_ID))
async def list_authorized_command(client: Client, message: Message):
    try:
        authorized_user_list = await list_allowed()
        if not authorized_user_list: await message.reply_text(MSG_NO_AUTH_USERS); return

        message_text_build = MSG_ADMIN_AUTH_LIST_HEADER
        for i, user_data_item in enumerate(authorized_user_list, 1):
            message_text_build += MSG_AUTH_USER_INFO.format(i=i, user_id=user_data_item['user_id'], authorized_by=user_data_item['authorized_by'], auth_time=user_data_item['authorized_at'])

        await message.reply_text(message_text_build, parse_mode=ParseMode.MARKDOWN,
                                 link_preview_options=LinkPreviewOptions(is_disabled=True),
                                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]]))
    except Exception as e:
        logger.error(f"Error in list_authorized_command: {e}", exc_info=True) # Added exc_info=True
        await message.reply_text(MSG_ERROR_GENERIC)

@StreamBot.on_message(filters.command("ban") & filters.private & filters.user(list(Var.OWNER_ID) if isinstance(Var.OWNER_ID, list) else Var.OWNER_ID))
async def ban_user_command(client: Client, message: Message):
    if len(message.command) < 2: await message.reply_text(MSG_BAN_USAGE); return
    try:
        user_id_to_ban = int(message.command[1])
        if user_id_to_ban in (Var.OWNER_ID if isinstance(Var.OWNER_ID, list) else [Var.OWNER_ID]):
            await message.reply_text(MSG_CANNOT_BAN_OWNER); return

        reason_for_ban = " ".join(message.command[2:]) if len(message.command) > 2 else MSG_ADMIN_NO_BAN_REASON # Renamed
        time_of_ban = datetime.now().strftime("%Y-%m-%d %H:%M:%S") # Renamed

        await db.add_banned_user(user_id=user_id_to_ban, reason=reason_for_ban, ban_time=time_of_ban, banned_by=message.from_user.id)

        reply_text_build = MSG_ADMIN_USER_BANNED.format(user_id=user_id_to_ban)
        if reason_for_ban != MSG_ADMIN_NO_BAN_REASON: reply_text_build += MSG_BAN_REASON_SUFFIX.format(reason=reason_for_ban)
        await message.reply_text(reply_text_build)

        try:
            await client.send_message(chat_id=user_id_to_ban, text=MSG_USER_BANNED_NOTIFICATION)
        except FloodWait as e_fw:
            logger.warning(f"FloodWait sending ban notification to {user_id_to_ban}: {e_fw}. Sleeping {e_fw.value}s")
            wait_time = float(e_fw.value) if isinstance(e_fw.value, (int, float)) else 10.0
            await asyncio.sleep(wait_time)
            try: await client.send_message(chat_id=user_id_to_ban, text=MSG_USER_BANNED_NOTIFICATION)
            except Exception as e_retry: logger.error(f"Failed to send ban notification to {user_id_to_ban} on retry: {e_retry}")
        except Exception as e_final: logger.error(f"Failed to send ban notification to {user_id_to_ban}: {e_final}")

    except ValueError: await message.reply_text(MSG_INVALID_USER_ID)
    except Exception as e:
        logger.error(f"Error in ban_user_command: {e}", exc_info=True)
        await message.reply_text(MSG_BAN_ERROR.format(error=str(e)))

@StreamBot.on_message(filters.command("unban") & filters.private & filters.user(list(Var.OWNER_ID) if isinstance(Var.OWNER_ID, list) else Var.OWNER_ID))
async def unban_user_command(client: Client, message: Message):
    if len(message.command) < 2: await message.reply_text(MSG_UNBAN_USAGE); return
    try:
        user_id_to_unban = int(message.command[1])
        was_removed = await db.remove_banned_user(user_id=user_id_to_unban)

        if was_removed:
            await message.reply_text(MSG_ADMIN_USER_UNBANNED.format(user_id=user_id_to_unban))
            try:
                await client.send_message(chat_id=user_id_to_unban, text=MSG_USER_UNBANNED_NOTIFICATION)
            except FloodWait as e_fw:
                logger.warning(f"FloodWait sending unban notification to {user_id_to_unban}: {e_fw}. Sleeping {e_fw.value}s")
                wait_time = float(e_fw.value) if isinstance(e_fw.value, (int, float)) else 10.0
                await asyncio.sleep(wait_time)
                try: await client.send_message(chat_id=user_id_to_unban, text=MSG_USER_UNBANNED_NOTIFICATION)
                except Exception as e_retry: logger.error(f"Failed to send unban notification to {user_id_to_unban} on retry: {e_retry}")
            except Exception as e_final: logger.error(f"Failed to send unban notification to {user_id_to_unban}: {e_final}")
        else: await message.reply_text(MSG_USER_NOT_IN_BAN_LIST.format(user_id=user_id_to_unban))

    except ValueError: await message.reply_text(MSG_INVALID_USER_ID)
    except Exception as e:
        logger.error(f"Error in unban_user_command: {e}", exc_info=True)
        await message.reply_text(MSG_UNBAN_ERROR.format(error=str(e)))

@StreamBot.on_message(filters.command("shell") & filters.private & filters.user(list(Var.OWNER_ID) if isinstance(Var.OWNER_ID, list) else Var.OWNER_ID))
async def run_shell_command(client: Client, message: Message):
    if len(message.command) < 2: await message.reply_text(MSG_SHELL_USAGE, parse_mode=ParseMode.HTML); return

    command_to_exec = " ".join(message.command[1:])
    reply_status_msg = await message.reply_text(MSG_SHELL_EXECUTING.format(command=html.escape(command_to_exec)), parse_mode=ParseMode.HTML, quote=True)

    try:
        process = await asyncio.create_subprocess_shell(command_to_exec, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout_bytes, stderr_bytes = await process.communicate()

        shell_output_str = ""
        if stdout_bytes: shell_output_str += MSG_SHELL_OUTPUT_STDOUT.format(output=html.escape(stdout_bytes.decode(errors='ignore').strip()))
        if stderr_bytes: shell_output_str += MSG_SHELL_OUTPUT_STDERR.format(error=html.escape(stderr_bytes.decode(errors='ignore').strip()))
        if not shell_output_str: shell_output_str = MSG_SHELL_NO_OUTPUT

    except Exception as e: shell_output_str = MSG_SHELL_ERROR.format(error=html.escape(str(e)))

    if len(shell_output_str) > 4096:
        try:
            output_filename = f"shell_output_{int(time.time())}.txt"
            with open(output_filename, "w", encoding="utf-8") as file:
                if stdout_bytes: file.write(f"STDOUT:\n{stdout_bytes.decode(errors='ignore')}\n\n")
                if stderr_bytes: file.write(f"STDERR:\n{stderr_bytes.decode(errors='ignore')}")

            await client.send_document(chat_id=message.chat.id, document=output_filename, caption=MSG_SHELL_OUTPUT.format(command=html.escape(command_to_exec)), parse_mode=ParseMode.HTML)
            os.remove(output_filename)
        except Exception as e_doc_send:
            logger.error(f"Error sending shell output as document: {e_doc_send}", exc_info=True)
            await message.reply_text("Output too large, and file creation/sending failed.", parse_mode=ParseMode.HTML)
    else: await message.reply_text(shell_output_str, parse_mode=ParseMode.HTML)

    if reply_status_msg:
        try: await reply_status_msg.delete()
        except Exception: pass

@StreamBot.on_callback_query(filters.regex("close_panel"))
async def close_panel(client: Client, cb_query: CallbackQuery):
    try:
        await cb_query.answer()
        if cb_query.message:
            await cb_query.message.delete()
            if cb_query.message.reply_to_message:
                try:
                    context_msg = cb_query.message.reply_to_message
                    await context_msg.delete()
                    if context_msg.reply_to_message: await context_msg.reply_to_message.delete()
                except Exception as e_del_context: logger.warning(f"Error deleting context messages in close_panel: {e_del_context}")
    except Exception as e_callback:
        logger.error(f"Error in close_panel callback: {e_callback}", exc_info=True)
    finally:
        raise StopPropagation

@StreamBot.on_callback_query(filters.regex("restart_broadcast"))
async def restart_broadcast(client: Client, cb_query: CallbackQuery):
    try:
        await cb_query.edit_message_text(MSG_ERROR_BROADCAST_INSTRUCTION, parse_mode=ParseMode.MARKDOWN)
    except MessageNotModified: pass
    except Exception as e:
        logger.error(f"Error in restart_broadcast callback: {e}", exc_info=True)
        try: await cb_query.answer("Error processing request.", show_alert=True)
        except Exception: pass
