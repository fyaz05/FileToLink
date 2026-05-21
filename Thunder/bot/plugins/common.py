import time
from datetime import UTC, datetime, timedelta

import pytdbot
from pytdbot import types

from Thunder.bot import StreamBot
from Thunder.utils.bot_utils import gen_dc_txt, get_user, log_newusr, reply_user_err
from Thunder.utils.compat import Filters, _get_media_file
from Thunder.utils.database import db
from Thunder.utils.decorators import check_banned
from Thunder.utils.file_properties import get_fname, get_fsize
from Thunder.utils.force_channel import force_channel_check, get_force_info
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.logger import logger
from Thunder.utils.messages import (
    MSG_ABOUT,
    MSG_BUTTON_ABOUT,
    MSG_BUTTON_CLOSE,
    MSG_BUTTON_GET_HELP,
    MSG_BUTTON_GITHUB,
    MSG_BUTTON_JOIN_CHANNEL,
    MSG_BUTTON_VIEW_PROFILE,
    MSG_COMMUNITY_CHANNEL,
    MSG_DC_ANON_ERROR,
    MSG_DC_FILE_ERROR,
    MSG_DC_FILE_INFO,
    MSG_DC_INVALID_USAGE,
    MSG_DC_UNKNOWN,
    MSG_ERROR_USER_INFO,
    MSG_FILE_TYPE_ANIMATION,
    MSG_FILE_TYPE_AUDIO,
    MSG_FILE_TYPE_DOCUMENT,
    MSG_FILE_TYPE_PHOTO,
    MSG_FILE_TYPE_STICKER,
    MSG_FILE_TYPE_UNKNOWN,
    MSG_FILE_TYPE_VIDEO,
    MSG_FILE_TYPE_VIDEO_NOTE,
    MSG_FILE_TYPE_VOICE,
    MSG_HELP,
    MSG_PING_RESPONSE,
    MSG_PING_START,
    MSG_TOKEN_ACTIVATED,
    MSG_TOKEN_FAILED,
    MSG_TOKEN_INVALID,
    MSG_WELCOME,
)
from Thunder.vars import Var


@StreamBot.on_message(filters=Filters.command("start") & Filters.private)
async def start_command(bot: pytdbot.Client, msg: types.Message):
    if not await check_banned(bot, msg):
        return
    from_id = getattr(msg, "from_id", 0)
    if from_id:
        await log_newusr(bot, from_id, "")

    text = getattr(msg, "text", "") or ""
    parts = text.split()
    if len(parts) >= 2:
        payload = parts[1]

        if payload != "start":
            token = await db.token_col.find_one({"token": payload})
            if token:
                if token["user_id"] != from_id:
                    try:
                        return await msg.reply_text(MSG_TOKEN_FAILED.format(
                            reason="This activation link is not for your account.",
                            error_id=str(int(time.time()))[-8:]
                        ))
                    except Exception:
                        logger.debug(f"Failed to send token mismatch error to user {from_id}")
                        return

                if token.get("activated"):
                    try:
                        return await msg.reply_text(MSG_TOKEN_FAILED.format(
                            reason="Token has already been activated.",
                            error_id=str(int(time.time()))[-8:]
                        ))
                    except Exception:
                        logger.debug(f"Failed to send token already activated error to user {from_id}")
                        return

                now = datetime.now(UTC)
                exp = now + timedelta(hours=Var.TOKEN_TTL_HOURS)

                await db.token_col.update_one(
                    {"token": payload, "user_id": from_id},
                    {"$set": {"activated": True, "created_at": now, "expires_at": exp}}
                )

                hrs = round((exp - now).total_seconds() / 3600, 1)
                try:
                    return await msg.reply_text(MSG_TOKEN_ACTIVATED.format(duration_hours=hrs))
                except Exception:
                    logger.debug(f"Failed to send token activated confirmation to user {from_id}")
                    return
            else:
                try:
                    return await msg.reply_text(MSG_TOKEN_INVALID)
                except Exception:
                    logger.debug(f"Failed to send token invalid message to user {from_id}")
                    return

    txt = MSG_WELCOME.format(user_name="")
    link, title = await get_force_info(bot)
    if link:
        txt += f"\n\n{MSG_COMMUNITY_CHANNEL.format(channel_title=title)}"

    buttons = [
        [types.InlineKeyboardButton(text=MSG_BUTTON_GET_HELP, type=types.InlineKeyboardButtonTypeCallback(data=b"help_command")),
         types.InlineKeyboardButton(text=MSG_BUTTON_ABOUT, type=types.InlineKeyboardButtonTypeCallback(data=b"about_command"))],
        [types.InlineKeyboardButton(text=MSG_BUTTON_GITHUB, type=types.InlineKeyboardButtonTypeUrl(url="https://github.com/fyaz05/FileToLink/")),
         types.InlineKeyboardButton(text=MSG_BUTTON_CLOSE, type=types.InlineKeyboardButtonTypeCallback(data=b"close_panel"))]
    ]

    if link:
        buttons.append([types.InlineKeyboardButton(text=MSG_BUTTON_JOIN_CHANNEL.format(channel_title=title), type=types.InlineKeyboardButtonTypeUrl(url=link))])

    try:
        await msg.reply_text(txt, reply_markup=types.ReplyMarkupInlineKeyboard(rows=buttons))
    except Exception as e:
        logger.error(f"Error in start_command: {e}", exc_info=True)


@StreamBot.on_message(filters=Filters.command("help") & Filters.private)
async def help_command(bot: pytdbot.Client, msg: types.Message):
    if not await check_banned(bot, msg):
        return
    from_id = getattr(msg, "from_id", 0)
    if from_id:
        await log_newusr(bot, from_id, "")

    txt = MSG_HELP.format(max_files=Var.MAX_BATCH_FILES)
    buttons = [[types.InlineKeyboardButton(text=MSG_BUTTON_ABOUT, type=types.InlineKeyboardButtonTypeCallback(data=b"about_command"))]]

    link, title = await get_force_info(bot)
    if link:
        buttons.append([types.InlineKeyboardButton(text=MSG_BUTTON_JOIN_CHANNEL.format(channel_title=title), type=types.InlineKeyboardButtonTypeUrl(url=link))])

    buttons.append([types.InlineKeyboardButton(text=MSG_BUTTON_CLOSE, type=types.InlineKeyboardButtonTypeCallback(data=b"close_panel"))])
    try:
        await msg.reply_text(txt, reply_markup=types.ReplyMarkupInlineKeyboard(rows=buttons))
    except Exception as e:
        logger.error(f"Error in help_command: {e}", exc_info=True)


@StreamBot.on_message(filters=Filters.command("about") & Filters.private)
async def about_command(bot: pytdbot.Client, msg: types.Message):
    if not await check_banned(bot, msg):
        return
    from_id = getattr(msg, "from_id", 0)
    if from_id:
        await log_newusr(bot, from_id, "")

    buttons = [
        [types.InlineKeyboardButton(text=MSG_BUTTON_GET_HELP, type=types.InlineKeyboardButtonTypeCallback(data=b"help_command"))],
        [types.InlineKeyboardButton(text=MSG_BUTTON_GITHUB, type=types.InlineKeyboardButtonTypeUrl(url="https://github.com/fyaz05/FileToLink/")),
         types.InlineKeyboardButton(text=MSG_BUTTON_CLOSE, type=types.InlineKeyboardButtonTypeCallback(data=b"close_panel"))]
    ]

    try:
        await msg.reply_text(MSG_ABOUT, reply_markup=types.ReplyMarkupInlineKeyboard(rows=buttons))
    except Exception as e:
        logger.error(f"Error in about_command: {e}", exc_info=True)


async def send_user_dc(msg: types.Message, user: types.User):
    txt = await gen_dc_txt(user)
    url = f"https://t.me/{user.username}" if hasattr(user, "username") and user.username else f"tg://user?id={user.id}"
    buttons = [
        [types.InlineKeyboardButton(text=MSG_BUTTON_VIEW_PROFILE, type=types.InlineKeyboardButtonTypeUrl(url=url))],
        [types.InlineKeyboardButton(text=MSG_BUTTON_CLOSE, type=types.InlineKeyboardButtonTypeCallback(data=b"close_panel"))]
    ]
    try:
        await msg.reply_text(txt, reply_markup=types.ReplyMarkupInlineKeyboard(rows=buttons))
    except Exception as e:
        logger.error(f"Error sending user DC: {e}")


async def send_file_dc(msg: types.Message, file_msg: types.Message):
    try:
        fname = get_fname(file_msg) or "Untitled File"
        fsize = humanbytes(get_fsize(file_msg))

        content = getattr(file_msg, "content", None)
        type_map = {
            "MessageDocument": MSG_FILE_TYPE_DOCUMENT,
            "MessagePhoto": MSG_FILE_TYPE_PHOTO,
            "MessageVideo": MSG_FILE_TYPE_VIDEO,
            "MessageAudio": MSG_FILE_TYPE_AUDIO,
            "MessageVoiceNote": MSG_FILE_TYPE_VOICE,
            "MessageSticker": MSG_FILE_TYPE_STICKER,
            "MessageAnimation": MSG_FILE_TYPE_ANIMATION,
            "MessageVideoNote": MSG_FILE_TYPE_VIDEO_NOTE,
        }

        file_type = type(content).__name__ if content else "unknown"
        type_display = type_map.get(file_type, MSG_FILE_TYPE_UNKNOWN)

        dc_id = MSG_DC_UNKNOWN
        media_file = _get_media_file(file_msg)
        if media_file and hasattr(media_file, "remote"):
            dc_id = getattr(media_file.remote, "dc_id", MSG_DC_UNKNOWN) or MSG_DC_UNKNOWN

        txt = MSG_DC_FILE_INFO.format(
            file_name=fname,
            file_size=fsize,
            file_type=type_display,
            dc_id=dc_id
        )

        buttons = [[types.InlineKeyboardButton(text=MSG_BUTTON_CLOSE, type=types.InlineKeyboardButtonTypeCallback(data=b"close_panel"))]]
        await msg.reply_text(txt, reply_markup=types.ReplyMarkupInlineKeyboard(rows=buttons))

    except Exception as e:
        logger.error(f"File DC error: {e}", exc_info=True)
        await reply_user_err(msg, MSG_DC_FILE_ERROR)


@StreamBot.on_message(filters=Filters.command("dc"))
async def dc_command(bot: pytdbot.Client, msg: types.Message):
    if not await check_banned(bot, msg):
        return
    if not await force_channel_check(bot, msg):
        return

    from_id = getattr(msg, "from_id", None)
    reply_to = getattr(msg, "reply_to", None)

    if not from_id and not reply_to:
        return await reply_user_err(msg, MSG_DC_ANON_ERROR)

    text = getattr(msg, "text", "") or ""
    args = text.strip().split(maxsplit=1)
    if len(args) > 1:
        user = await get_user(bot, args[1].strip())
        if user:
            await send_user_dc(msg, user)
        else:
            await reply_user_err(msg, MSG_ERROR_USER_INFO)
        return

    if reply_to and hasattr(reply_to, "message_id"):
        ref_result = await bot.getMessage(chat_id=msg.chat_id, message_id=reply_to.message_id)
        if not isinstance(ref_result, types.Error) and ref_result:
            content = getattr(ref_result, "content", None)
            if content and _get_media_file(ref_result):
                await send_file_dc(msg, ref_result)
            else:
                sender_id = getattr(ref_result, "from_id", None)
                if sender_id:
                    user = await get_user(bot, sender_id)
                    if user:
                        await send_user_dc(msg, user)
                    else:
                        await reply_user_err(msg, MSG_ERROR_USER_INFO)
                else:
                    await reply_user_err(msg, MSG_DC_INVALID_USAGE)
        return

    if from_id:
        user = await get_user(bot, from_id)
        if user:
            await send_user_dc(msg, user)
        else:
            await reply_user_err(msg, MSG_DC_ANON_ERROR)


@StreamBot.on_message(filters=Filters.command("ping") & Filters.private)
async def ping_command(bot: pytdbot.Client, msg: types.Message):
    if not await check_banned(bot, msg):
        return
    if not await force_channel_check(bot, msg):
        return
    start = time.time()
    sent = await msg.reply_text(MSG_PING_START)
    end = time.time()
    ms = (end - start) * 1000

    buttons = [
        [types.InlineKeyboardButton(text=MSG_BUTTON_GET_HELP, type=types.InlineKeyboardButtonTypeCallback(data=b"help_command")),
         types.InlineKeyboardButton(text=MSG_BUTTON_CLOSE, type=types.InlineKeyboardButtonTypeCallback(data=b"close_panel"))]
    ]

    if sent and not isinstance(sent, types.Error):
        try:
            await sent.editTextMessage(
                chat_id=msg.chat_id,
                message_id=sent.id,
                text=MSG_PING_RESPONSE.format(time_taken_ms=ms),
                reply_markup=types.ReplyMarkupInlineKeyboard(rows=buttons)
            )
        except Exception as e:
            logger.error(f"Error editing ping message: {e}", exc_info=True)
