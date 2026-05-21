import asyncio
import html
import os
import shutil
import sys
import time

import psutil
import pytdbot
from pytdbot import types

from Thunder import StartTime, __version__
from Thunder.bot import StreamBot, multi_clients, work_loads
from Thunder.utils.bot_utils import get_user, reply
from Thunder.utils.broadcast import broadcast_message
from Thunder.utils.compat import Filters
from Thunder.utils.database import db
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.logger import LOG_FILE, logger
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
    MSG_SHELL_ERROR,
    MSG_SHELL_EXECUTING,
    MSG_SHELL_NO_OUTPUT,
    MSG_SHELL_OUTPUT,
    MSG_SHELL_OUTPUT_STDERR,
    MSG_SHELL_OUTPUT_STDOUT,
    MSG_SHELL_USAGE,
    MSG_SPEEDTEST_ERROR,
    MSG_SPEEDTEST_INIT,
    MSG_SPEEDTEST_RESULT,
    MSG_STATUS_ERROR,
    MSG_SYSTEM_STATS,
    MSG_SYSTEM_STATUS,
    MSG_UNBAN_USAGE,
    MSG_USER_BANNED_NOTIFICATION,
    MSG_USER_NOT_IN_BAN_LIST,
    MSG_USER_UNBANNED_NOTIFICATION,
    MSG_WORKLOAD_ITEM,
)
from Thunder.utils.speedtest import run_speedtest
from Thunder.utils.time_format import get_readable_time
from Thunder.utils.tokens import authorize, deauthorize, list_allowed
from Thunder.vars import Var

def _write_file_sync(fd: int, content: str) -> None:
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(content)


owner_filter = Filters.and_(Filters.private, Filters.user(Var.OWNER_ID))

_MARKDOWN_ESCAPE_TRANS = str.maketrans({
    "\\": "\\\\",
    "_": "\\_",
    "*": "\\*",
    "[": "\\[",
    "]": "\\]",
    "`": "\\`",
})


def _escape_markdown(text: str) -> str:
    return text.translate(_MARKDOWN_ESCAPE_TRANS)


def _make_close_button():
    return types.InlineKeyboardButton(
        text=MSG_BUTTON_CLOSE,
        type=types.InlineKeyboardButtonTypeCallback(data=b"close_panel")
    )


@StreamBot.on_message(filters=Filters.command("users") & owner_filter)
async def get_total_users(client: pytdbot.Client, message: types.Message):
    try:
        total = await db.total_users_count()
        await reply(message,
                    text=MSG_DB_STATS.format(total_users=total),
                    reply_markup=types.ReplyMarkupInlineKeyboard(rows=[[_make_close_button()]]))
    except Exception as e:
        logger.error(f"Error in get_total_users: {e}", exc_info=True)
        await reply(message, text=MSG_DB_ERROR)


@StreamBot.on_message(filters=Filters.command("broadcast") & owner_filter)
async def broadcast_handler(client: pytdbot.Client, message: types.Message):
    mode = "all"
    text = getattr(message, "text", "") or ""
    parts = text.split()
    if len(parts) > 1:
        arg = parts[1].lower().strip()
        if arg in ("help", "--help", "-h"):
            return await reply(message, text=MSG_BROADCAST_USAGE)
        if arg == "authorized":
            mode = "authorized"
        elif arg == "regular":
            mode = "regular"
        else:
            safe_arg = arg.replace("`", "'")
            await reply(message, text=f"❌ **Invalid argument:** `{safe_arg}`\n\n{MSG_BROADCAST_USAGE}")
            return

    reply_to = getattr(message, "reply_to", None)
    if not reply_to or not hasattr(reply_to, "message_id"):
        return await reply(message, text=MSG_INVALID_BROADCAST_CMD)

    await broadcast_message(client, message, mode=mode)


@StreamBot.on_message(filters=Filters.command("status") & owner_filter)
async def show_status(client: pytdbot.Client, message: types.Message):
    try:
        uptime_str = get_readable_time(int(time.time() - StartTime))
        workload_items = ""
        sorted_workloads = sorted(work_loads.items(), key=lambda item: item[0])
        for client_id, load_val in sorted_workloads:
            workload_items += MSG_WORKLOAD_ITEM.format(
                bot_name=f"🔹 Client {client_id}", load=load_val)

        total_workload = sum(work_loads.values())
        status_text_str = MSG_SYSTEM_STATUS.format(
            uptime=uptime_str, active_bots=len(multi_clients),
            total_workload=total_workload, workload_items=workload_items,
            version=__version__)
        await reply(message,
                    text=status_text_str,
                    reply_markup=types.ReplyMarkupInlineKeyboard(rows=[[_make_close_button()]]))
    except Exception as e:
        logger.error(f"Error in show_status: {e}", exc_info=True)
        await reply(message, text=MSG_STATUS_ERROR)


@StreamBot.on_message(filters=Filters.command("stats") & owner_filter)
async def show_stats(client: pytdbot.Client, message: types.Message):
    try:
        def _collect_stats():
            sys_uptime = psutil.boot_time()
            net_io_counters = psutil.net_io_counters()
            cpu_percent = psutil.cpu_percent(interval=0.5)
            cpu_cores = psutil.cpu_count(logical=False)
            cpu_freq = psutil.cpu_freq()
            ram_info = psutil.virtual_memory()
            total_disk, used_disk, free_disk = shutil.disk_usage('.')
            return {
                'sys_uptime': sys_uptime,
                'net_io': net_io_counters,
                'cpu_percent': cpu_percent,
                'cpu_cores': cpu_cores,
                'cpu_freq': cpu_freq,
                'ram': ram_info,
                'disk_total': total_disk,
                'disk_used': used_disk,
                'disk_free': free_disk,
                'disk_percent': (used_disk / total_disk * 100) if total_disk > 0 else 0,
            }

        stats = await asyncio.to_thread(_collect_stats)

        sys_uptime_str = get_readable_time(int(time.time() - stats['sys_uptime']))
        bot_uptime_str = get_readable_time(int(time.time() - StartTime))
        cpu_freq_ghz = f"{stats['cpu_freq'].current / 1000:.2f}" if stats['cpu_freq'] else "N/A"

        stats_text_val = MSG_SYSTEM_STATS.format(
            sys_uptime=sys_uptime_str,
            bot_uptime=bot_uptime_str,
            cpu_percent=stats['cpu_percent'],
            cpu_cores=stats['cpu_cores'],
            cpu_freq=cpu_freq_ghz,
            ram_total=humanbytes(stats['ram'].total),
            ram_used=humanbytes(stats['ram'].used),
            ram_free=humanbytes(stats['ram'].free),
            disk_percent=stats['disk_percent'],
            total=humanbytes(stats['disk_total']),
            used=humanbytes(stats['disk_used']),
            free=humanbytes(stats['disk_free']),
            upload=humanbytes(stats['net_io'].bytes_sent),
            download=humanbytes(stats['net_io'].bytes_recv)
        )

        await reply(message,
                    text=stats_text_val,
                    reply_markup=types.ReplyMarkupInlineKeyboard(rows=[[_make_close_button()]]))
    except Exception as e:
        logger.error(f"Error in show_stats: {e}", exc_info=True)
        await reply(message, text=MSG_STATUS_ERROR)


@StreamBot.on_message(filters=Filters.command("restart") & owner_filter)
async def restart_bot(client: pytdbot.Client, message: types.Message):
    msg = await reply(message, text=MSG_RESTARTING)
    if msg and not isinstance(msg, types.Error):
        await db.add_restart_message(msg.id, message.chat_id)
    os.execv(sys.executable, [sys.executable, "-m", "Thunder"])


@StreamBot.on_message(filters=Filters.command("log") & owner_filter)
async def send_logs(client: pytdbot.Client, message: types.Message):
    log_exists = await asyncio.to_thread(os.path.exists, LOG_FILE)
    if not log_exists or await asyncio.to_thread(os.path.getsize, LOG_FILE) == 0:
        await reply(
            message,
            text=(MSG_LOG_FILE_MISSING if not log_exists else MSG_LOG_FILE_EMPTY)
        )
        return

    try:
        result = await client.sendDocument(
            chat_id=message.chat_id,
            document=types.InputFileLocal(path=LOG_FILE),
            caption=MSG_LOG_FILE_CAPTION
        )
        if isinstance(result, types.Error):
            await reply(message, text=MSG_ERROR_GENERIC)
    except Exception as e:
        logger.error(f"Error sending log file: {e}", exc_info=True)
        await reply(message, text=MSG_ERROR_GENERIC)


@StreamBot.on_message(filters=Filters.command("authorize") & owner_filter)
async def authorize_command(client: pytdbot.Client, message: types.Message):
    text = getattr(message, "text", "") or ""
    parts = text.split()
    if len(parts) != 2:
        return await reply(message, text=MSG_AUTHORIZE_USAGE)

    try:
        user_id = int(parts[1])
        success = await authorize(user_id, getattr(message, "from_id", 0))
        await reply(message,
                    text=(MSG_AUTHORIZE_SUCCESS.format(user_id=user_id) if success else MSG_AUTHORIZE_FAILED.format(user_id=user_id)))
    except ValueError:
        await reply(message, text=MSG_INVALID_USER_ID)
    except Exception as e:
        logger.error(f"Error in authorize_command: {e}", exc_info=True)
        await reply(message, text=MSG_ERROR_GENERIC)


@StreamBot.on_message(filters=Filters.command("deauthorize") & owner_filter)
async def deauthorize_command(client: pytdbot.Client, message: types.Message):
    text = getattr(message, "text", "") or ""
    parts = text.split()
    if len(parts) != 2:
        return await reply(message, text=MSG_DEAUTHORIZE_USAGE)

    try:
        user_id = int(parts[1])
        success = await deauthorize(user_id)
        await reply(message,
                    text=(MSG_DEAUTHORIZE_SUCCESS.format(user_id=user_id) if success else MSG_DEAUTHORIZE_FAILED.format(user_id=user_id)))
    except ValueError:
        await reply(message, text=MSG_INVALID_USER_ID)
    except Exception as e:
        logger.error(f"Error in deauthorize_command: {e}", exc_info=True)
        await reply(message, text=MSG_ERROR_GENERIC)


@StreamBot.on_message(filters=Filters.command("listauth") & owner_filter)
async def list_authorized_command(client: pytdbot.Client, message: types.Message):
    users = await list_allowed()
    if not users:
        return await reply(message, text=MSG_NO_AUTH_USERS)

    text = MSG_ADMIN_AUTH_LIST_HEADER
    for i, user in enumerate(users, 1):
        display_name = "Unknown"
        try:
            tg_user = await get_user(client, user['user_id'])
            if tg_user is not None:
                raw_display_name = f"@{tg_user.username}" if hasattr(tg_user, "username") and tg_user.username else (tg_user.first_name or "Unknown")
                display_name = _escape_markdown(raw_display_name)
        except Exception:
            logger.error("Failed to fetch tg_user for user_id=%s", user['user_id'], exc_info=True)

        text += MSG_AUTH_USER_INFO.format(
            i=i,
            display_name=display_name,
            user_id=user['user_id'],
            authorized_by=user['authorized_by'],
            auth_time=user['authorized_at']
        )

    await reply(message,
                text=text,
                reply_markup=types.ReplyMarkupInlineKeyboard(rows=[[_make_close_button()]]))


@StreamBot.on_message(filters=Filters.command("ban") & owner_filter)
async def ban_command(client: pytdbot.Client, message: types.Message):
    text = getattr(message, "text", "") or ""
    parts = text.split()
    if len(parts) < 2:
        return await reply(message, text=MSG_BAN_USAGE)

    try:
        target_id = int(parts[1])
        reason = " ".join(parts[2:]) or MSG_ADMIN_NO_BAN_REASON
        banned_by_id = getattr(message, "from_id", None)

        if target_id == Var.OWNER_ID:
            return await reply(message, text=MSG_CANNOT_BAN_OWNER)

        if target_id < 0:
            await db.add_banned_channel(
                channel_id=target_id,
                reason=reason,
                banned_by=banned_by_id
            )
            text = MSG_CHANNEL_BANNED.format(channel_id=target_id)
            if reason != MSG_ADMIN_NO_BAN_REASON:
                text += MSG_CHANNEL_BANNED_REASON_SUFFIX.format(reason=reason)
            await reply(message, text=text)
            result = await client.leaveChat(chat_id=target_id)
            if isinstance(result, types.Error):
                logger.warning(f"Could not leave banned channel {target_id}: {result.message}")
        else:
            await db.add_banned_user(
                user_id=target_id,
                reason=reason,
                banned_by=banned_by_id
            )
            text = MSG_ADMIN_USER_BANNED.format(user_id=target_id)
            if reason != MSG_ADMIN_NO_BAN_REASON:
                text += MSG_BAN_REASON_SUFFIX.format(reason=reason)
            await reply(message, text=text)
            result = await client.sendTextMessage(chat_id=target_id, text=MSG_USER_BANNED_NOTIFICATION)
            if isinstance(result, types.Error):
                logger.warning(f"Could not notify banned user {target_id}: {result.message}")

    except ValueError:
        await reply(message, text=MSG_INVALID_USER_ID)
    except Exception as e:
        logger.error(f"Error in ban_command: {e}", exc_info=True)
        await reply(message, text=MSG_ERROR_GENERIC)


@StreamBot.on_message(filters=Filters.command("unban") & owner_filter)
async def unban_command(client: pytdbot.Client, message: types.Message):
    text = getattr(message, "text", "") or ""
    parts = text.split()
    if len(parts) != 2:
        return await reply(message, text=MSG_UNBAN_USAGE)

    try:
        target_id = int(parts[1])

        if target_id < 0:
            if await db.remove_banned_channel(channel_id=target_id):
                await reply(message, text=MSG_CHANNEL_UNBANNED.format(channel_id=target_id))
            else:
                await reply(message, text=MSG_CHANNEL_NOT_BANNED.format(channel_id=target_id))
        else:
            if await db.remove_banned_user(user_id=target_id):
                await reply(message, text=MSG_ADMIN_USER_UNBANNED.format(user_id=target_id))
                result = await client.sendTextMessage(chat_id=target_id, text=MSG_USER_UNBANNED_NOTIFICATION)
                if isinstance(result, types.Error):
                    logger.warning(f"Could not notify unbanned user {target_id}: {result.message}")
            else:
                await reply(message, text=MSG_USER_NOT_IN_BAN_LIST.format(user_id=target_id))
    except ValueError:
        await reply(message, text=MSG_INVALID_USER_ID)
    except Exception as e:
        logger.error(f"Error in unban_command: {e}", exc_info=True)
        await reply(message, text=MSG_ERROR_GENERIC)


@StreamBot.on_message(filters=Filters.command("shell") & owner_filter)
async def run_shell_command(client: pytdbot.Client, message: types.Message):
    text = getattr(message, "text", "") or ""
    parts = text.split()
    if len(parts) < 2:
        return await reply(message, text=MSG_SHELL_USAGE)

    command = " ".join(parts[1:])
    status_msg = await reply(message, text=MSG_SHELL_EXECUTING.format(command=html.escape(command)))

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            await reply(message, text="⏱️ **Shell command timed out** (60s limit)")
            return

        output = ""
        if stdout:
            output += MSG_SHELL_OUTPUT_STDOUT.format(output=html.escape(stdout.decode(errors='ignore')))
        if stderr:
            output += MSG_SHELL_OUTPUT_STDERR.format(error=html.escape(stderr.decode(errors='ignore')))

        output = output.strip() or MSG_SHELL_NO_OUTPUT

        if status_msg and not isinstance(status_msg, types.Error):
            try:
                await status_msg.delete()
            except Exception:
                logger.debug(f"Failed to delete shell status message {status_msg.id}")

        if len(output) > 4096:
            import tempfile
            tmp_fd, tmp_path = tempfile.mkstemp(suffix='.txt', prefix='shell_')
            try:
                await asyncio.to_thread(_write_file_sync, tmp_fd, output)
                result = await client.sendDocument(
                    chat_id=message.chat_id,
                    document=types.InputFileLocal(path=tmp_path),
                    caption=MSG_SHELL_OUTPUT.format(command=html.escape(command))
                )
                if isinstance(result, types.Error):
                    await reply(message, text=f"Failed to send output: {result.message}")
            finally:
                try:
                    await asyncio.to_thread(os.remove, tmp_path)
                except OSError:
                    pass
        else:
            await reply(message, text=output)

    except Exception as e:
        logger.error(f"Error in run_shell_command: {e}", exc_info=True)
        if status_msg and not isinstance(status_msg, types.Error):
            try:
                await status_msg.editTextMessage(chat_id=status_msg.chat_id, message_id=status_msg.id, text=MSG_SHELL_ERROR.format(error=html.escape(str(e))))
            except Exception:
                logger.debug("Failed to edit shell error status message")


@StreamBot.on_message(filters=Filters.command("speedtest") & owner_filter)
async def speedtest_command(client: pytdbot.Client, message: types.Message):
    status_msg = await reply(message, text=MSG_SPEEDTEST_INIT)
    try:
        result_dict, image_url = await run_speedtest()
        if result_dict is None:
            if status_msg and not isinstance(status_msg, types.Error):
                try:
                    await status_msg.editTextMessage(chat_id=status_msg.chat_id, message_id=status_msg.id, text=MSG_SPEEDTEST_ERROR)
                except Exception:
                    logger.debug("Failed to edit speedtest error status message")
            return

        result_text = _format_speedtest_result(result_dict)
        if image_url:
            try:
                result = await client.sendPhoto(
                    chat_id=message.chat_id,
                    photo=types.InputFileRemote(id=image_url),
                    caption=result_text
                )
                if isinstance(result, types.Error):
                    if status_msg and not isinstance(status_msg, types.Error):
                        await status_msg.editTextMessage(chat_id=status_msg.chat_id, message_id=status_msg.id, text=result_text)
            except Exception:
                if status_msg and not isinstance(status_msg, types.Error):
                    await status_msg.editTextMessage(chat_id=status_msg.chat_id, message_id=status_msg.id, text=result_text)
        else:
            if status_msg and not isinstance(status_msg, types.Error):
                await status_msg.editTextMessage(chat_id=status_msg.chat_id, message_id=status_msg.id, text=result_text)
    except Exception as e:
        logger.error(f"Error in speedtest_command: {e}", exc_info=True)
        if status_msg and not isinstance(status_msg, types.Error):
            try:
                await status_msg.editTextMessage(chat_id=status_msg.chat_id, message_id=status_msg.id, text=MSG_SPEEDTEST_ERROR)
            except Exception:
                logger.debug("Failed to edit speedtest error status message")


def _format_speedtest_result(result_dict: dict) -> str:
    s, c = result_dict['server'], result_dict['client']
    return MSG_SPEEDTEST_RESULT.format(
        download_mbps=_fmt(result_dict['download_mbps']),
        upload_mbps=_fmt(result_dict['upload_mbps']),
        download_bps=humanbytes(result_dict['download_bps']),
        upload_bps=humanbytes(result_dict['upload_bps']),
        ping=_fmt(result_dict['ping']),
        timestamp=result_dict['timestamp'],
        bytes_sent=humanbytes(result_dict['bytes_sent']),
        bytes_received=humanbytes(result_dict['bytes_received']),
        server_name=s['name'],
        server_country=f"{s['country']} ({s['cc']})",
        server_sponsor=s['sponsor'],
        server_latency=_fmt(s['latency']),
        server_lat=_fmt(s['lat'], 4),
        server_lon=_fmt(s['lon'], 4),
        client_ip=c['ip'],
        client_lat=_fmt(c['lat'], 4),
        client_lon=_fmt(c['lon'], 4),
        client_isp=c['isp'],
        client_isprating=c['isprating'],
        client_country=c['country']
    )


def _fmt(value, decimals: int = 2) -> str:
    return f"{float(value):.{decimals}f}"
