import asyncio
from typing import Optional, Dict, Any
from urllib.parse import quote

from pyrogram import Client # Removed enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, User, LinkPreviewOptions, ReplyParameters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait

from Thunder.vars import Var
from Thunder.utils.database import db
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.logger import logger
from Thunder.utils.messages import (
    MSG_BUTTON_GET_HELP,
    MSG_NEW_USER,
    MSG_DC_UNKNOWN,
    MSG_DC_USER_INFO,
    MSG_LINKS,
    MSG_BUTTON_STREAM_NOW,
    MSG_BUTTON_DOWNLOAD
)
from Thunder.utils.file_properties import get_fname, get_fsize, get_hash
from Thunder.utils.shortener import shorten

async def _send_msg(
    tgt: Any,
    mthd: str,
    chat_id: Optional[int] = None,
    text: Optional[str] = None,
    **kwargs: Any
) -> Optional[Message]:
    fn_call = getattr(tgt, mthd)
    send_args = {}

    log_chat_id_str = "N/A"
    actual_chat_id = None

    if mthd == "send_message":
        actual_chat_id = chat_id if chat_id is not None else (getattr(tgt, 'id', None) if isinstance(tgt, Client) else None)
        if actual_chat_id is None and hasattr(tgt, 'chat') and hasattr(tgt.chat, 'id'):
             actual_chat_id = tgt.chat.id
        send_args['chat_id'] = actual_chat_id
        send_args['text'] = text
        if actual_chat_id: log_chat_id_str = str(actual_chat_id)
    elif mthd == "reply_text" and hasattr(tgt, 'chat') and hasattr(tgt.chat, 'id'):
        actual_chat_id = tgt.chat.id
        send_args['text'] = text
        log_chat_id_str = str(actual_chat_id)
    elif hasattr(tgt, 'id') and not isinstance(tgt, Client):
        log_chat_id_str = str(tgt.id)

    send_args.update(kwargs)
    tgt_cls_name = tgt.__class__.__name__

    try:
        return await fn_call(**send_args)
    except FloodWait as e_fw:
        logger.warning(f"FloodWait: {tgt_cls_name}.{mthd} to {log_chat_id_str}. Sleep {e_fw.value}s. Retrying once.")
        await asyncio.sleep(e_fw.value)
        try:
            return await fn_call(**send_args)
        except FloodWait as e_fw_retry:
            logger.error(f"FloodWait on retry: {tgt_cls_name}.{mthd} to {log_chat_id_str}: {e_fw_retry}")
        except Exception as e_retry:
            logger.error(f"RetryFail after FloodWait: {tgt_cls_name}.{mthd} to {log_chat_id_str}: {e_retry}")
    except Exception as e:
        logger.error(f"Error: {tgt_cls_name}.{mthd} to {log_chat_id_str}: {e}")
    return None

async def notify_ch(cli: Client, txt: str):
    if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
        await _send_msg(cli, "send_message", chat_id=Var.BIN_CHANNEL, text=txt)

async def notify_own(cli: Client, txt: str):
    o_ids = Var.OWNER_ID
    tasks = []
    for oid in (o_ids if isinstance(o_ids, (list, tuple, set)) else [o_ids]):
        tasks.append(_send_msg(cli, "send_message", chat_id=oid, text=txt))
    if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
        tasks.append(_send_msg(cli, "send_message", chat_id=Var.BIN_CHANNEL, text=txt))
    await asyncio.gather(*tasks, return_exceptions=True)

async def reply_user_err(msg: Message, err_txt: str):
    await _send_msg(
        msg._client,
        "send_message",
        chat_id=msg.chat.id,
        text=err_txt,
        reply_parameters=ReplyParameters(message_id=msg.id),
        link_preview_options=LinkPreviewOptions(is_disabled=True),
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command")]])
    )

async def log_newusr(cli: Client, uid: int, fname: str):
    try:
        user_exists = await db.is_user_exist(uid)
        if not user_exists:
            await db.add_user(uid)
            # Only send log message if user was actually added (i.e., didn't exist before)
            if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
                await _send_msg(
                    cli,
                    "send_message",
                    chat_id=Var.BIN_CHANNEL,
                    text=MSG_NEW_USER.format(first_name=fname, user_id=uid)
                )
    except Exception as e:
        logger.error(f"Database error in log_newusr for user {uid}: {e}")
        # If DB operations fail, we simply log the error and do not proceed to send a "new user" message.
        # The function will implicitly return None.
            )

async def gen_links(fwd_msg: Message, shortener: bool = True) -> Dict[str, str]:
    base_url = Var.URL.rstrip("/")
    fid = fwd_msg.id

    m_name_raw = get_fname(fwd_msg)
    if isinstance(m_name_raw, bytes):
        m_name = m_name_raw.decode('utf-8', errors='replace')
    else:
        m_name = str(m_name_raw)

    m_size_raw = get_fsize(fwd_msg)
    m_size_hr = humanbytes(m_size_raw)

    enc_fname = quote(m_name)
    f_hash = get_hash(fwd_msg)

    slink = f"{base_url}/watch/{f_hash}{fid}/{enc_fname}"
    olink = f"{base_url}/{f_hash}{fid}/{enc_fname}"

    if shortener and getattr(Var, "SHORTEN_MEDIA_LINKS", False):
        try:
            s_results = await asyncio.gather(
                shorten(slink),
                shorten(olink),
                return_exceptions=True
            )
            if not isinstance(s_results[0], Exception):
                slink = s_results[0]
            else:
                logger.warning(f"Failed to shorten stream_link {slink}: {s_results[0]}")

            if not isinstance(s_results[1], Exception):
                olink = s_results[1]
            else:
                logger.warning(f"Failed to shorten online_link {olink}: {s_results[1]}")
        except Exception as e:
            logger.error(f"Error during link shortening process: {e}")

    return {
        "stream_link": slink,
        "online_link": olink,
        "media_name": m_name,
        "media_size": m_size_hr
    }

async def gen_dc_txt(usr: User) -> str:
    dc_id_val = usr.dc_id if usr.dc_id is not None else MSG_DC_UNKNOWN
    return MSG_DC_USER_INFO.format(
        user_name=usr.first_name or 'User',
        user_id=usr.id,
        dc_id=dc_id_val
    )

async def get_user(cli: Client, qry: Any) -> Optional[User]:
    try:
        if isinstance(qry, str):
            if qry.startswith('@'):
                return await cli.get_users(qry)
            elif qry.isdigit():
                return await cli.get_users(int(qry))
        elif isinstance(qry, int):
            return await cli.get_users(qry)
    except FloodWait as e_fw:
        logger.warning(f"FloodWait in get_user for query '{qry}': {e_fw}. Sleeping {e_fw.value}s")
        await asyncio.sleep(e_fw.value)
    except Exception as e:
        logger.error(f"Error in get_user for query '{qry}': {e}")
    return None

async def is_admin(cli: Client, chat_id_val: int) -> bool:
    try:
        member = await cli.get_chat_member(chat_id_val, cli.me.id)
        return member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except FloodWait as e_fw:
        logger.warning(f"FloodWait in is_admin for chat {chat_id_val}: {e_fw}. Sleeping {e_fw.value}s")
        await asyncio.sleep(e_fw.value)
    except Exception as e:
        logger.error(f"Error in is_admin for chat {chat_id_val}: {e}")
    return False
