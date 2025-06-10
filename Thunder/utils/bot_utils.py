import asyncio
from typing import Optional, Dict, Any
from urllib.parse import quote

from pyrogram import Client, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, User, LinkPreviewOptions, ReplyParameters # CallbackQuery removed
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
)
from Thunder.utils.file_properties import get_fname, get_fsize, get_hash # Updated to minified names
from Thunder.utils.shortener import shorten

async def _send_msg(
    tgt: Any,
    mthd: str,
    chat_id: Optional[int] = None,
    text: Optional[str] = None,
    **kwargs: Any
) -> Optional[Message]:
    fn_call = getattr(tgt, mthd)
    args = {}
    if mthd == "send_message":
        args['chat_id'] = chat_id if chat_id is not None else tgt.id
        args['text'] = text
    elif mthd == "reply_text":
        args['text'] = text
    args.update(kwargs)

    log_tgt = "N/A"
    if mthd == "send_message":
        log_tgt = str(args.get('chat_id', getattr(tgt, 'id', "N/A")))
    elif mthd == "reply_text" and hasattr(tgt, 'chat') and hasattr(tgt.chat, 'id'):
         log_tgt = str(tgt.chat.id)
    elif hasattr(tgt, 'id'):
        log_tgt = str(tgt.id)

    tgt_cls = tgt.__class__.__name__
    try:
        return await fn_call(**args)
    except FloodWait as e:
        logger.warning(f"FloodWait: {tgt_cls}.{mthd} to {log_tgt}. Sleep {e.value}s")
        await asyncio.sleep(e.value)
        try:
            return await fn_call(**args)
        except FloodWait as e_fw_retry:
            logger.error(f"FloodWait on retry: {tgt_cls}.{mthd} to {log_tgt}: {e_fw_retry}")
        except Exception as e_retry:
            logger.error(f"RetryFail after FloodWait: {tgt_cls}.{mthd} to {log_tgt}: {e_retry}")
    except Exception as e:
        logger.error(f"Error: {tgt_cls}.{mthd} to {log_tgt}: {e}")
    return None

async def notify_ch(cli: Client, text: str):
    if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
        await _send_msg(cli, "send_message", chat_id=Var.BIN_CHANNEL, text=text)

async def notify_own(cli: Client, text: str):
    o_ids = Var.OWNER_ID
    for oid in (o_ids if isinstance(o_ids, (list, tuple, set)) else [o_ids]):
        await _send_msg(cli, "send_message", chat_id=oid, text=text)
    if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
        await _send_msg(cli, "send_message", chat_id=Var.BIN_CHANNEL, text=text)

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

async def log_newusr(cli: Client, user_id: int, fname: str): # param first_name -> fname
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id)
        if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
            await _send_msg(
                cli,
                "send_message",
                chat_id=Var.BIN_CHANNEL,
                text=MSG_NEW_USER.format(first_name=fname, user_id=user_id) # dict key remains first_name
            )

async def gen_links(fwd_msg: Message, shortener: bool = True) -> Dict[str, str]: # log_msg -> fwd_msg
    base_url = Var.URL.rstrip("/")
    fid = fwd_msg.id # file_id -> fid
    fname = get_fname(fwd_msg) # media_name -> fname, using updated get_fname
    if isinstance(fname, bytes):
        fname = fname.decode('utf-8', errors='replace')
    else:
        fname = str(fname)
    fsize = get_fsize(fwd_msg) # media_size -> fsize, using updated get_fsize
    enc_fname = quote(fname) # file_name_encoded -> enc_fname
    fhash = get_hash(fwd_msg) # hash_value -> fhash

    slink = f"{base_url}/watch/{fhash}{fid}/{enc_fname}" # stream_link -> slink
    olink = f"{base_url}/{fhash}{fid}/{enc_fname}" # online_link -> olink

    if shortener and getattr(Var, "SHORTEN_MEDIA_LINKS", False):
        sh_slink, sh_olink = await asyncio.gather( # shortened_stream_link -> sh_slink, etc.
            shorten(slink),
            shorten(olink),
            return_exceptions=True
        )
        if not isinstance(sh_slink, Exception):
            slink = sh_slink
        if not isinstance(sh_olink, Exception):
            olink = sh_olink

    return {
        "stream_link": slink, "online_link": olink,
        "media_name": fname, "media_size": fsize
    }

async def gen_dc_txt(usr: User) -> str: # user -> usr
    dc = usr.dc_id if usr.dc_id is not None else MSG_DC_UNKNOWN # dc_id -> dc
    return MSG_DC_USER_INFO.format(
        user_name=usr.first_name or 'User', # user.first_name -> usr.first_name
        user_id=usr.id, # user.id -> usr.id
        dc_id=dc # dc_id -> dc
    )

async def get_user(cli: Client, qry: Any) -> Optional[User]: # bot -> cli, query -> qry
    try:
        if isinstance(qry, str):
            if qry.startswith('@'):
                return await cli.get_users(qry)
            elif qry.isdigit():
                return await cli.get_users(int(qry))
        elif isinstance(qry, int):
            return await cli.get_users(qry)
    except FloodWait as e:
        logger.warning(f"FloodWait: get_user for {qry}. Sleep {e.value}s")
        await asyncio.sleep(e.value)
    except Exception as e:
        logger.error(f"Error: get_user for {qry}: {e}")
    return None

async def is_admin(cli: Client, chat_id: int) -> bool: # client -> cli
    try:
        member = await cli.get_chat_member(chat_id, cli.me.id)
        return member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except FloodWait as e:
        logger.warning(f"FloodWait: is_admin for {chat_id}. Sleep {e.value}s")
        await asyncio.sleep(e.value)
    except Exception as e:
        logger.error(f"Error: is_admin for {chat_id}: {e}")
    return False

# exec_cb_cmd function removed from here
