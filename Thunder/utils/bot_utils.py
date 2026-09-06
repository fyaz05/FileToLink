# Thunder/utils/bot_utils.py

import asyncio
import html
from typing import Any
from urllib.parse import quote

from pyrogram import Client
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from Thunder.utils.database import db
from Thunder.utils.file_properties import get_fname, get_fsize, get_hash
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.logger import logger
from Thunder.utils.messages import (
    MSG_BUTTON_GET_HELP,
    MSG_DC_UNKNOWN,
    MSG_DC_USER_INFO,
    MSG_FILE_EXPIRY_NOTE,
    MSG_FILE_TTL_DAYS_LABEL,
    MSG_LINKS,
    MSG_NEW_USER,
)
from Thunder.utils.safe_call import reply_safe, send_safe, tg_call
from Thunder.utils.shortener import shorten
from Thunder.vars import Var


def quote_media_name(file_name: str) -> str:
    return quote(str(file_name).replace("/", "_"), safe="")


def format_link_message(links: dict[str, str]) -> str:
    """Render the MSG_LINKS template, appending the TTL expiry note (L2).

    ``media_name`` is user-controlled and MSG_LINKS is HTML -- escape it
    (M7) so a crafted file name cannot inject markup into the link message.
    """
    text = MSG_LINKS.format(
        file_name=html.escape(str(links["media_name"])),
        file_size=links["media_size"],
        download_link=links["online_link"],
        stream_link=links["stream_link"],
    )
    if getattr(Var, "FILE_TTL_DAYS", 0) > 0:
        text += "\n\n" + MSG_FILE_EXPIRY_NOTE.format(
            days=MSG_FILE_TTL_DAYS_LABEL.format(days=Var.FILE_TTL_DAYS)
        )
    return text


async def _build_links(
    *,
    download_path: str,
    stream_path: str,
    media_name: str,
    media_size: str,
    shortener: bool = True,
) -> dict[str, str]:
    base_url = Var.URL.rstrip("/")
    slink = f"{base_url}{stream_path}"
    olink = f"{base_url}{download_path}"

    if shortener and getattr(Var, "SHORTEN_MEDIA_LINKS", False):
        try:
            s_results = await asyncio.gather(shorten(slink), shorten(olink), return_exceptions=True)
            if isinstance(s_results[0], BaseException):
                logger.warning(f"Failed to shorten stream_link: {s_results[0]}")
            else:
                slink = s_results[0]
            if isinstance(s_results[1], BaseException):
                logger.warning(f"Failed to shorten online_link: {s_results[1]}")
            else:
                olink = s_results[1]
        except Exception as e:
            logger.error(f"Error during link shortening: {e}")

    return {
        "stream_link": slink,
        "online_link": olink,
        "media_name": media_name,
        "media_size": media_size,
    }


async def gen_canonical_links(
    *, file_name: str, file_size: int, public_hash: str, shortener: bool = True
) -> dict[str, str]:
    media_name = str(file_name)
    media_size = humanbytes(file_size)
    encoded_name = quote_media_name(media_name)
    return await _build_links(
        download_path=f"/f/{public_hash}/{encoded_name}",
        stream_path=f"/watch/f/{public_hash}/{encoded_name}",
        media_name=media_name,
        media_size=media_size,
        shortener=shortener,
    )


async def notify_own(cli: Client, txt: str):
    o_ids = Var.OWNER_ID if isinstance(Var.OWNER_ID, (list, tuple, set)) else [Var.OWNER_ID]

    async def send_with_flood_wait(chat_id: int):
        try:
            await send_safe(cli, chat_id, text=txt)
        except Exception as e:
            logger.warning(f"Could not notify chat {chat_id}: {e}")

    tasks = [send_with_flood_wait(oid) for oid in o_ids]
    if isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
        tasks.append(send_with_flood_wait(Var.BIN_CHANNEL))
    await asyncio.gather(*tasks, return_exceptions=True)


async def reply_user_err(msg: Message, err_txt: str):
    try:
        await reply_safe(
            msg,
            err_txt,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command")]]
            ),
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"Error sending user error reply: {e}", exc_info=True)


async def log_newusr(cli: Client, uid: int, fname: str):
    try:
        is_new = await db.add_user(uid)
        if not is_new:
            return
        if isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
            try:
                await send_safe(
                    cli,
                    Var.BIN_CHANNEL,
                    # MSG_NEW_USER is HTML; first_name is user-controlled (M7)
                    text=MSG_NEW_USER.format(first_name=html.escape(str(fname or "")), user_id=uid),
                )
            except Exception as e:
                logger.warning(f"Could not log new user {uid}: {e}")
    except Exception as e:
        logger.error(f"Database error in log_newusr for user {uid}: {e}")


async def gen_links(fwd_msg: Message, shortener: bool = True) -> dict[str, str]:
    fid = fwd_msg.id
    m_name_raw = get_fname(fwd_msg)
    m_name = (
        m_name_raw.decode("utf-8", errors="replace")
        if isinstance(m_name_raw, bytes)
        else str(m_name_raw)
    )
    m_size_hr = humanbytes(get_fsize(fwd_msg))
    enc_fname = quote_media_name(m_name)
    f_hash = get_hash(fwd_msg)
    return await _build_links(
        download_path=f"/{f_hash}{fid}/{enc_fname}",
        stream_path=f"/watch/{f_hash}{fid}/{enc_fname}",
        media_name=m_name,
        media_size=m_size_hr,
        shortener=shortener,
    )


async def gen_dc_txt(usr: User) -> str:
    dc_id_val = usr.dc_id if usr.dc_id is not None else MSG_DC_UNKNOWN
    return MSG_DC_USER_INFO.format(
        user_name=usr.first_name or "User", user_id=usr.id, dc_id=dc_id_val
    )


async def get_user(cli: Client, qry: Any) -> User | None:
    if isinstance(qry, str) and qry.startswith("@"):
        try:
            result = await tg_call(cli.get_users, qry)
        except Exception as e:
            logger.debug(f"get_users failed for {qry}: {e}")
            return None
        if isinstance(result, list):  # defensive: pyrogram returns a list for list inputs
            return result[0] if result else None
        return result
    if isinstance(qry, str) and qry.isdigit():
        qry = int(qry)
    if isinstance(qry, int):
        try:
            result = await tg_call(cli.get_users, qry)
        except Exception as e:
            logger.debug(f"get_users failed for {qry}: {e}")
            return None
        if isinstance(result, list):  # defensive: pyrogram returns a list for list inputs
            return result[0] if result else None
        return result
    return None


async def is_admin(cli: Client, chat_id_val: int) -> bool:
    try:
        # cli.me is always populated after client.start(); fall back to an id
        # that cannot match any chat member if it somehow is not.
        me_id = cli.me.id if cli.me else 0
        member = await tg_call(cli.get_chat_member, chat_id_val, me_id, retries=1)
    except Exception:
        return False
    if member is None:
        return False
    return member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]


async def reply(msg: Message, **kwargs):
    kwargs.setdefault("disable_web_page_preview", True)
    return await reply_safe(msg, kwargs.pop("text", ""), **kwargs)


__all__ = [
    "quote_media_name",
    "format_link_message",
    "gen_canonical_links",
    "notify_own",
    "reply_user_err",
    "log_newusr",
    "gen_links",
    "gen_dc_txt",
    "get_user",
    "is_admin",
    "reply",
    "MSG_BUTTON_GET_HELP",
]
