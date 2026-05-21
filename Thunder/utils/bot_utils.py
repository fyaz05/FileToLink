import asyncio
import time
from typing import Any
from urllib.parse import quote

import pytdbot
from pytdbot import types

from Thunder.utils.compat import (
    ChatMemberStatus,
    _get_file_name,
    _get_file_size,
    get_member_status,
)
from Thunder.utils.database import db
from Thunder.utils.file_properties import get_hash
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.logger import logger
from Thunder.utils.messages import MSG_BUTTON_GET_HELP, MSG_DC_USER_INFO, MSG_NEW_USER
from Thunder.utils.shortener import shorten
from Thunder.vars import Var

_bot_me_cache: dict[int, tuple[int, float]] = {}
_bot_me_cache_ttl: int = 3600


async def _get_bot_me(cli: pytdbot.Client) -> int | None:
    bot_id = id(cli)
    now = time.time()
    if bot_id in _bot_me_cache:
        cached_id, ts = _bot_me_cache[bot_id]
        if now - ts < _bot_me_cache_ttl:
            return cached_id
    me = await cli.getMe()
    if isinstance(me, types.Error):
        return None
    _bot_me_cache[bot_id] = (me.id, now)
    return me.id


def quote_media_name(file_name: str) -> str:
    return quote(str(file_name).replace("/", "_"), safe="")


async def _build_links(
    *,
    download_path: str,
    stream_path: str,
    media_name: str,
    media_size: str,
    shortener: bool = True
) -> dict[str, str]:
    base_url = Var.URL.rstrip("/")
    slink = f"{base_url}{stream_path}"
    olink = f"{base_url}{download_path}"

    if shortener and getattr(Var, "SHORTEN_MEDIA_LINKS", False):
        try:
            s_results = await asyncio.gather(shorten(slink), shorten(olink), return_exceptions=True)
            if not isinstance(s_results[0], Exception):
                slink = s_results[0]
            else:
                logger.warning(f"Failed to shorten stream_link: {s_results[0]}")
            if not isinstance(s_results[1], Exception):
                olink = s_results[1]
            else:
                logger.warning(f"Failed to shorten online_link: {s_results[1]}")
        except Exception as e:
            logger.error(f"Error during link shortening: {e}")

    return {"stream_link": slink, "online_link": olink, "media_name": media_name, "media_size": media_size}


async def gen_canonical_links(
    *,
    file_name: str,
    file_size: int,
    public_hash: str,
    shortener: bool = True
) -> dict[str, str]:
    media_name = str(file_name)
    media_size = humanbytes(file_size)
    encoded_name = quote_media_name(media_name)
    return await _build_links(
        download_path=f"/f/{public_hash}/{encoded_name}",
        stream_path=f"/watch/f/{public_hash}/{encoded_name}",
        media_name=media_name,
        media_size=media_size,
        shortener=shortener
    )


async def notify_ch(cli: pytdbot.Client, txt: str):
    if not (hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0):
        return
    try:
        await cli.sendTextMessage(chat_id=Var.BIN_CHANNEL, text=txt)
    except Exception as e:
        logger.error(f"Error notifying channel: {e}")


async def notify_own(cli: pytdbot.Client, txt: str):
    o_ids = Var.OWNER_ID if isinstance(Var.OWNER_ID, (list, tuple, set)) else [Var.OWNER_ID]

    async def send_with_retry(chat_id: int):
        try:
            await cli.sendTextMessage(chat_id=chat_id, text=txt)
        except Exception as e:
            logger.error(f"Error notifying owner {chat_id}: {e}")

    tasks = [send_with_retry(oid) for oid in o_ids]
    if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
        tasks.append(send_with_retry(Var.BIN_CHANNEL))
    await asyncio.gather(*tasks, return_exceptions=True)


async def reply_user_err(msg: types.Message, err_txt: str):
    try:
        button = types.InlineKeyboardButton(
            text=MSG_BUTTON_GET_HELP,
            type=types.InlineKeyboardButtonTypeCallback(data=b"help_command")
        )
        await msg.reply_text(
            err_txt,
            reply_markup=types.ReplyMarkupInlineKeyboard(rows=[[button]])
        )
    except Exception as e:
        logger.error(f"Error replying to user: {e}")


async def log_newusr(cli: pytdbot.Client, uid: int, fname: str):
    try:
        is_new = await db.add_user(uid)
        if not is_new:
            return
        if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
            try:
                await cli.sendTextMessage(
                    chat_id=Var.BIN_CHANNEL,
                    text=MSG_NEW_USER.format(first_name=fname, user_id=uid)
                )
            except Exception as e:
                logger.error(f"Error logging new user: {e}")
    except Exception as e:
        logger.error(f"Database error in log_newusr for user {uid}: {e}")


async def gen_links(fwd_msg: types.Message, shortener: bool = True) -> dict[str, str]:
    fid = fwd_msg.id
    m_name = _get_file_name(fwd_msg) or "Untitled"
    if isinstance(m_name, bytes):
        m_name = m_name.decode('utf-8', errors='replace')
    m_name = str(m_name)
    m_size_hr = humanbytes(_get_file_size(fwd_msg))
    enc_fname = quote_media_name(m_name)
    f_hash = get_hash(fwd_msg)
    return await _build_links(
        download_path=f"/{f_hash}{fid}/{enc_fname}",
        stream_path=f"/watch/{f_hash}{fid}/{enc_fname}",
        media_name=m_name,
        media_size=m_size_hr,
        shortener=shortener
    )


async def gen_dc_txt(usr: types.User) -> str:
    dc_id_val = "Unknown"
    if hasattr(usr, "profile_photo") and usr.profile_photo:
        dc_id_val = getattr(usr.profile_photo, "dc_id", "Unknown")
    return MSG_DC_USER_INFO.format(user_name=usr.first_name or 'User', user_id=usr.id, dc_id=dc_id_val)


async def get_user(cli: pytdbot.Client, qry: Any) -> types.User | None:
    try:
        if isinstance(qry, str):
            if qry.startswith('@'):
                return None
            elif qry.isdigit():
                result = await cli.getUser(user_id=int(qry))
            else:
                return None
        elif isinstance(qry, int):
            result = await cli.getUser(user_id=qry)
        else:
            return None

        if isinstance(result, types.Error):
            logger.warning(f"Error getting user {qry}: {result.message}")
            return None
        return result
    except Exception as e:
        logger.error(f"Error in get_user: {e}")
        return None


async def is_admin(cli: pytdbot.Client, chat_id_val: int) -> bool:
    try:
        bot_id = await _get_bot_me(cli)
        if bot_id is None:
            return False
        member = await cli.getChatMember(
            chat_id=chat_id_val,
            member_id=types.MessageSenderUser(user_id=bot_id)
        )
        if isinstance(member, types.Error):
            return False
        status = get_member_status(member)
        return status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except Exception as e:
        logger.error(f"Error checking admin: {e}")
        return False


async def reply(msg: types.Message, **kwargs):
    try:
        text = kwargs.get("text", "")
        parse_mode = kwargs.get("parse_mode")
        reply_markup = kwargs.get("reply_markup")

        if reply_markup and isinstance(reply_markup, types.ReplyMarkupInlineKeyboard):
            pass
        elif reply_markup and isinstance(reply_markup, dict):
            rows = reply_markup.get("inline_keyboard", [])
            buttons = []
            for row in rows:
                button_row = []
                for btn in row:
                    if isinstance(btn, types.InlineKeyboardButton):
                        button_row.append(btn)
                    elif isinstance(btn, dict):
                        text_btn = btn.get("text", "")
                        if "url" in btn:
                            button_row.append(types.InlineKeyboardButton(
                                text=text_btn,
                                type=types.InlineKeyboardButtonTypeUrl(url=btn["url"])
                            ))
                        elif "callback_data" in btn:
                            button_row.append(types.InlineKeyboardButton(
                                text=text_btn,
                                type=types.InlineKeyboardButtonTypeCallback(data=btn["callback_data"].encode("utf-8"))
                            ))
                if button_row:
                    buttons.append(button_row)
            reply_markup = types.ReplyMarkupInlineKeyboard(rows=buttons) if buttons else None

        result = await msg.reply_text(text, reply_markup=reply_markup)
        if isinstance(result, types.Error):
            logger.error(f"Error replying: {result.message}")
        return result
    except Exception as e:
        logger.error(f"Error in reply: {e}", exc_info=True)
        return None
