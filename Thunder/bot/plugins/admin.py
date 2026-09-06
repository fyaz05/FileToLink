# Thunder/bot/plugins/admin.py

import asyncio
import html
import os
import shutil
import time
from io import BytesIO
from typing import Any

import psutil
from pyrogram import filters
from pyrogram.client import Client
from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from Thunder import StartTime, __version__
from Thunder.bot import StreamBot, multi_clients, work_loads
from Thunder.utils.bot_utils import reply
from Thunder.utils.broadcast import broadcast_message
from Thunder.utils.database import db
from Thunder.utils.flag_cache import flags
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.logger import LOG_FILE, logger, redact_secrets
from Thunder.utils.messages import (
    MSG_ADMIN_AUTH_LIST_HEADER,
    MSG_ADMIN_NO_BAN_REASON,
    MSG_ADMIN_USER_BANNED,
    MSG_ADMIN_USER_UNBANNED,
    MSG_AUTH_USER_INFO,
    MSG_AUTHORIZE_FAILED,
    MSG_AUTHORIZE_SUCCESS,
    MSG_AUTHORIZE_USAGE,
    MSG_BAN_REASON_SUFFIX,
    MSG_BAN_USAGE,
    MSG_BROADCAST_USAGE,
    MSG_BUTTON_CLOSE,
    MSG_CANNOT_BAN_OWNER,
    MSG_CHANNEL_BANNED,
    MSG_CHANNEL_BANNED_REASON_SUFFIX,
    MSG_CHANNEL_NOT_BANNED,
    MSG_CHANNEL_UNBANNED,
    MSG_DB_ERROR,
    MSG_DB_STATS,
    MSG_DEAUTHORIZE_FAILED,
    MSG_DEAUTHORIZE_SUCCESS,
    MSG_DEAUTHORIZE_USAGE,
    MSG_ERROR_GENERIC,
    MSG_INVALID_BROADCAST_CMD,
    MSG_INVALID_USER_ID,
    MSG_LOG_FILE_CAPTION,
    MSG_LOG_FILE_EMPTY,
    MSG_LOG_FILE_MISSING,
    MSG_NO_AUTH_USERS,
    MSG_RESTARTING,
    MSG_SHELL_DISABLED,
    MSG_SHELL_ERROR,
    MSG_SHELL_EXECUTING,
    MSG_SHELL_NO_OUTPUT,
    MSG_SHELL_OUTPUT,
    MSG_SHELL_OUTPUT_STDERR,
    MSG_SHELL_OUTPUT_STDOUT,
    MSG_SHELL_USAGE,
    MSG_STATUS_ERROR,
    MSG_SYSTEM_STATS,
    MSG_SYSTEM_STATUS,
    MSG_UNBAN_USAGE,
    MSG_USER_BANNED_NOTIFICATION,
    MSG_USER_NOT_IN_BAN_LIST,
    MSG_USER_UNBANNED_NOTIFICATION,
    MSG_WORKLOAD_ITEM,
)
from Thunder.utils.rate_limiter import rate_limiter
from Thunder.utils.safe_call import (
    delete_safe,
    edit_safe,
    send_safe,
    tg_call,
)
from Thunder.utils.time_format import get_readable_time
from Thunder.utils.tokens import authorize, deauthorize, list_allowed
from Thunder.vars import Var

owner_filter = filters.private & filters.user(Var.OWNER_ID)

# H10: /log tail cap (mirrors ThunderGo handlers_owner.go handleLog)
_LOG_TAIL_BYTES = 45 * 1024 * 1024


def _invalidate_gates() -> None:
    """H7: admin mutators flush the flag cache so changes apply within one
    message instead of one TTL."""
    flags.clear()


@StreamBot.on_message(filters.command("users") & owner_filter)
async def get_total_users(client: Client, message: Message):
    try:
        total = await db.total_users_count()
        await reply(
            message,
            text=MSG_DB_STATS.format(total_users=total),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]]
            ),
        )
    except Exception as e:
        logger.error(f"Error in get_total_users: {e}", exc_info=True)
        await reply(message, text=MSG_DB_ERROR)


@StreamBot.on_message(filters.command("broadcast") & owner_filter)
async def broadcast_handler(client: Client, message: Message):
    mode = "all"
    if len(message.command) > 1:
        arg = message.command[1].lower().strip()
        if arg in ("help", "--help", "-h"):
            return await reply(message, text=MSG_BROADCAST_USAGE, parse_mode=ParseMode.MARKDOWN)
        if arg == "authorized":
            mode = "authorized"
        elif arg == "regular":
            mode = "regular"
        else:
            safe_arg = arg.replace("`", "'")
            await reply(
                message,
                text=f"❌ **Invalid argument:** `{safe_arg}`\n\n{MSG_BROADCAST_USAGE}",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

    if not message.reply_to_message:
        return await reply(message, text=MSG_INVALID_BROADCAST_CMD)

    await broadcast_message(client, message, mode=mode)


@StreamBot.on_message(filters.command("status") & owner_filter)
async def show_status(client: Client, message: Message):
    try:
        uptime_str = get_readable_time(int(time.time() - StartTime))
        workload_items = ""
        sorted_workloads = sorted(work_loads.items(), key=lambda item: item[0])
        for client_id, load_val in sorted_workloads:
            workload_items += MSG_WORKLOAD_ITEM.format(
                bot_name=f"🔹 Client {client_id}", load=load_val
            )

        total_workload = sum(work_loads.values())
        status_text_str = MSG_SYSTEM_STATUS.format(
            uptime=uptime_str,
            active_bots=len(multi_clients),
            total_workload=total_workload,
            workload_items=workload_items,
            version=__version__,
        )
        await reply(
            message,
            text=status_text_str,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]]
            ),
        )
    except Exception as e:
        logger.error(f"Error in show_status: {e}", exc_info=True)
        await reply(message, text=MSG_STATUS_ERROR)


@StreamBot.on_message(filters.command("stats") & owner_filter)
async def show_stats(client: Client, message: Message):
    try:
        sys_uptime = await asyncio.to_thread(psutil.boot_time)
        sys_uptime_str = get_readable_time(int(time.time() - sys_uptime))
        bot_uptime_str = get_readable_time(int(time.time() - StartTime))
        net_io_counters = await asyncio.to_thread(psutil.net_io_counters)
        cpu_percent = await asyncio.to_thread(psutil.cpu_percent, interval=0.5)
        cpu_cores = await asyncio.to_thread(psutil.cpu_count, logical=False)
        cpu_freq = await asyncio.to_thread(psutil.cpu_freq)
        cpu_freq_ghz = f"{cpu_freq.current / 1000:.2f}" if cpu_freq else "N/A"
        ram_info = await asyncio.to_thread(psutil.virtual_memory)
        ram_total = humanbytes(ram_info.total)
        ram_used = humanbytes(ram_info.used)
        ram_free = humanbytes(ram_info.free)

        total_disk, used_disk, free_disk = await asyncio.to_thread(shutil.disk_usage, ".")

        # H8: the last synchronous psutil call is off the event loop too
        disk_percent = (await asyncio.to_thread(psutil.disk_usage, ".")).percent

        limiter_line = (
            ", ".join(f"{k}={v}" for k, v in rate_limiter.occupancy().items()) or "disabled"
        )

        stats_text_val = MSG_SYSTEM_STATS.format(
            sys_uptime=sys_uptime_str,
            bot_uptime=bot_uptime_str,
            cpu_percent=cpu_percent,
            cpu_cores=cpu_cores,
            cpu_freq=cpu_freq_ghz,
            ram_total=ram_total,
            ram_used=ram_used,
            ram_free=ram_free,
            disk_percent=disk_percent,
            total=humanbytes(total_disk),
            used=humanbytes(used_disk),
            free=humanbytes(free_disk),
            upload=humanbytes(net_io_counters.bytes_sent),
            download=humanbytes(net_io_counters.bytes_recv),
            limiter=limiter_line,
        )

        await reply(
            message,
            text=stats_text_val,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]]
            ),
        )
    except Exception as e:
        logger.error(f"Error in show_stats: {e}", exc_info=True)
        await reply(message, text=MSG_STATUS_ERROR)


@StreamBot.on_message(filters.command("restart") & owner_filter)
async def restart_bot(client: Client, message: Message):
    msg = await reply(message, text=MSG_RESTARTING)
    await db.add_restart_message(msg.id, message.chat.id)
    # mirror __main__ teardown ordering (M13): the touch buffer batches view
    # counts for up to a few seconds -- execv skips every finally block, so
    # drain it here or the restart loses those increments
    from Thunder.utils.canonical_files import drain_background_touch_tasks

    await drain_background_touch_tasks()
    os.execv("/bin/bash", ["bash", "thunder.sh"])


@StreamBot.on_message(filters.command("log") & owner_filter)
async def send_logs(client: Client, message: Message):
    if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0:
        await reply(
            message,
            text=(MSG_LOG_FILE_MISSING if not os.path.exists(LOG_FILE) else MSG_LOG_FILE_EMPTY),
        )
        return

    try:
        # H10: never upload raw logs -- stream the (capped) tail through the
        # shared redaction regexes so bot tokens / Mongo URIs cannot leak.
        # File IO + regex over megabytes must not run on the event loop (H8).
        def _read_redacted_tail() -> str:
            with open(LOG_FILE, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - _LOG_TAIL_BYTES))
                return redact_secrets(f.read().decode("utf-8", errors="replace"))

        payload = await asyncio.to_thread(_read_redacted_tail)

        doc = BytesIO(payload.encode("utf-8"))
        doc.name = "bot_redacted.txt"
        await message.reply_document(doc, caption=MSG_LOG_FILE_CAPTION)
    except Exception as e:
        logger.error(f"Error sending log file: {e}", exc_info=True)
        await reply(message, text=MSG_ERROR_GENERIC)


@StreamBot.on_message(filters.command("authorize") & owner_filter)
async def authorize_command(client: Client, message: Message):
    if len(message.command) != 2:
        return await reply(message, text=MSG_AUTHORIZE_USAGE, parse_mode=ParseMode.MARKDOWN)

    try:
        user_id = int(message.command[1])
        success = await authorize(user_id, message.from_user.id)
        if success:
            _invalidate_gates()
        await reply(
            message,
            text=(
                MSG_AUTHORIZE_SUCCESS.format(user_id=user_id)
                if success
                else MSG_AUTHORIZE_FAILED.format(user_id=user_id)
            ),
        )
    except ValueError:
        await reply(message, text=MSG_INVALID_USER_ID)
    except Exception as e:
        logger.error(f"Error in authorize_command: {e}", exc_info=True)
        await reply(message, text=MSG_ERROR_GENERIC)


@StreamBot.on_message(filters.command("deauthorize") & owner_filter)
async def deauthorize_command(client: Client, message: Message):
    if len(message.command) != 2:
        return await reply(message, text=MSG_DEAUTHORIZE_USAGE, parse_mode=ParseMode.MARKDOWN)

    try:
        user_id = int(message.command[1])
        success = await deauthorize(user_id)
        if success:
            _invalidate_gates()
        await reply(
            message,
            text=(
                MSG_DEAUTHORIZE_SUCCESS.format(user_id=user_id)
                if success
                else MSG_DEAUTHORIZE_FAILED.format(user_id=user_id)
            ),
        )
    except ValueError:
        await reply(message, text=MSG_INVALID_USER_ID)
    except Exception as e:
        logger.error(f"Error in deauthorize_command: {e}", exc_info=True)
        await reply(message, text=MSG_ERROR_GENERIC)


@StreamBot.on_message(filters.command("listauth") & owner_filter)
async def list_authorized_command(client: Client, message: Message):
    users = await list_allowed()
    if not users:
        return await reply(message, text=MSG_NO_AUTH_USERS)

    # M7: HTML + html.escape for user-controlled display names.
    # One batched get_users RPC instead of one per row (N+1 FloodWait risk).
    id_to_user: dict[int, Any] = {}
    try:
        tg_users = await tg_call(client.get_users, [u["user_id"] for u in users], retries=1)
        if isinstance(tg_users, User):
            tg_users = [tg_users]
        id_to_user = {u.id: u for u in (tg_users or []) if u}
    except Exception:
        logger.error("Failed to batch-fetch tg_users for /listauth", exc_info=True)

    text = MSG_ADMIN_AUTH_LIST_HEADER
    for i, user in enumerate(users, 1):
        display_name = "Unknown"
        tg_user = id_to_user.get(user["user_id"])
        if tg_user is not None:
            raw_display_name = (
                f"@{tg_user.username}" if tg_user.username else tg_user.first_name or "Unknown"
            )
            display_name = html.escape(raw_display_name)

        text += MSG_AUTH_USER_INFO.format(
            i=i,
            display_name=display_name,
            user_id=user["user_id"],
            authorized_by=user["authorized_by"],
            auth_time=user["authorized_at"],
        )

    await reply(
        message,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]]
        ),
    )


@StreamBot.on_message(filters.command("ban") & owner_filter)
async def ban_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await reply(message, text=MSG_BAN_USAGE)

    try:
        target_id = int(message.command[1])
        # M7: reason is user-controlled and the ban messages are HTML now
        reason = html.escape(" ".join(message.command[2:])) or MSG_ADMIN_NO_BAN_REASON
        banned_by_id = message.from_user.id if message.from_user else None

        if target_id == Var.OWNER_ID:
            return await reply(message, text=MSG_CANNOT_BAN_OWNER)

        if target_id < 0:
            await db.add_banned_channel(channel_id=target_id, reason=reason, banned_by=banned_by_id)
            _invalidate_gates()
            text = MSG_CHANNEL_BANNED.format(channel_id=target_id)
            if reason != MSG_ADMIN_NO_BAN_REASON:
                text += MSG_CHANNEL_BANNED_REASON_SUFFIX.format(reason=reason)
            await reply(message, text=text, parse_mode=ParseMode.HTML)
            try:
                await tg_call(client.leave_chat, target_id, retries=1)
            except Exception as e:
                logger.warning(f"Could not leave banned channel {target_id}: {e}", exc_info=True)
        else:
            await db.add_banned_user(user_id=target_id, reason=reason, banned_by=banned_by_id)
            _invalidate_gates()
            text = MSG_ADMIN_USER_BANNED.format(user_id=target_id)
            if reason != MSG_ADMIN_NO_BAN_REASON:
                text += MSG_BAN_REASON_SUFFIX.format(reason=reason)
            await reply(message, text=text, parse_mode=ParseMode.HTML)
            try:
                await send_safe(client, target_id, text=MSG_USER_BANNED_NOTIFICATION)
            except Exception as e:
                logger.warning(f"Could not notify banned user {target_id}: {e}", exc_info=True)

    except ValueError:
        await reply(message, text=MSG_INVALID_USER_ID)
    except Exception as e:
        logger.error(f"Error in ban_command: {e}", exc_info=True)
        await reply(message, text=MSG_ERROR_GENERIC)


@StreamBot.on_message(filters.command("unban") & owner_filter)
async def unban_command(client: Client, message: Message):
    if len(message.command) != 2:
        return await reply(message, text=MSG_UNBAN_USAGE)

    try:
        target_id = int(message.command[1])

        if target_id < 0:
            if await db.remove_banned_channel(channel_id=target_id):
                _invalidate_gates()
                await reply(message, text=MSG_CHANNEL_UNBANNED.format(channel_id=target_id))
            else:
                await reply(message, text=MSG_CHANNEL_NOT_BANNED.format(channel_id=target_id))
        else:
            if await db.remove_banned_user(user_id=target_id):
                _invalidate_gates()
                await reply(message, text=MSG_ADMIN_USER_UNBANNED.format(user_id=target_id))
                try:
                    await send_safe(client, target_id, text=MSG_USER_UNBANNED_NOTIFICATION)
                except Exception as e:
                    logger.warning(
                        f"Could not notify unbanned user {target_id}: {e}", exc_info=True
                    )
            else:
                await reply(message, text=MSG_USER_NOT_IN_BAN_LIST.format(user_id=target_id))
    except ValueError:
        await reply(message, text=MSG_INVALID_USER_ID)
    except Exception as e:
        logger.error(f"Error in unban_command: {e}", exc_info=True)
        await reply(message, text=MSG_ERROR_GENERIC)


@StreamBot.on_message(filters.command("shell") & owner_filter)
async def run_shell_command(client: Client, message: Message):
    # L10: env kill-switch -- the powerful command is opt-in.
    if not getattr(Var, "ENABLE_SHELL", False):
        return await reply(message, text=MSG_SHELL_DISABLED, parse_mode=ParseMode.HTML)

    if len(message.command) < 2:
        return await reply(message, text=MSG_SHELL_USAGE, parse_mode=ParseMode.HTML)

    command = " ".join(message.command[1:])
    # L10: command log -- who ran what, when.
    logger.info(
        f"/shell invoked by {message.from_user.id if message.from_user else 'unknown'}: {command}"
    )

    status_msg = await reply(
        message,
        text=MSG_SHELL_EXECUTING.format(command=html.escape(command)),
        parse_mode=ParseMode.HTML,
    )

    try:
        process = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise TimeoutError("shell command exceeded 60s") from None

        output = ""
        if stdout:
            output += MSG_SHELL_OUTPUT_STDOUT.format(
                output=html.escape(stdout.decode(errors="ignore"))
            )
        if stderr:
            output += MSG_SHELL_OUTPUT_STDERR.format(
                error=html.escape(stderr.decode(errors="ignore"))
            )

        output = output.strip() or MSG_SHELL_NO_OUTPUT

        try:
            await delete_safe(status_msg)
        except Exception:
            pass

        if len(output) > 4096:
            file = BytesIO(output.encode())
            file.name = "shell_output.txt"
            await message.reply_document(
                file, caption=MSG_SHELL_OUTPUT.format(command=html.escape(command))
            )
        else:
            await reply(message, text=output, parse_mode=ParseMode.HTML)

    except Exception as e:
        try:
            await edit_safe(
                status_msg,
                MSG_SHELL_ERROR.format(error=html.escape(str(e))),
                parse_mode=ParseMode.HTML,
            )
        except MessageNotModified:
            pass
        except Exception:
            await reply(
                message,
                text=MSG_SHELL_ERROR.format(error=html.escape(str(e))),
                parse_mode=ParseMode.HTML,
            )
