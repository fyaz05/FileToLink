import time
import asyncio
import random
import uuid
from urllib.parse import quote
from typing import Optional, Dict, List, Any

from pyrogram import Client, filters, enums
from pyrogram.errors import (
    FloodWait,
    RPCError,
    MediaEmpty,
    FileReferenceExpired,
    FileReferenceInvalid,
    MessageNotModified
)
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Message,
    LinkPreviewOptions
)

from Thunder.bot import StreamBot
from Thunder.utils.database import db
from Thunder.utils.messages import (
    MSG_ERROR_START_BOT, MSG_BUTTON_START_CHAT, MSG_ERROR_NOT_ADMIN,
    MSG_ERROR_REPLY_FILE, MSG_ERROR_NO_FILE, MSG_ERROR_NUMBER_RANGE,
    MSG_ERROR_INVALID_NUMBER, MSG_PROCESSING_REQUEST, MSG_ERROR_PROCESSING_MEDIA,
    MSG_PROCESSING_FILE, MSG_MEDIA_ERROR, MSG_CRITICAL_ERROR,
    MSG_PROCESSING_BATCH, MSG_PROCESSING_STATUS,
    MSG_BATCH_LINKS_READY, MSG_DM_BATCH_PREFIX, MSG_ERROR_DM_FAILED,
    MSG_PROCESSING_RESULT, MSG_PROCESSING_ERROR,
    MSG_NEW_FILE_REQUEST,
    MSG_LINKS, MSG_BUTTON_STREAM_NOW, MSG_BUTTON_DOWNLOAD
)
from Thunder.utils.logger import logger
from Thunder.vars import Var
from Thunder.utils.decorators import check_banned, require_token
from Thunder.utils.force_channel import force_channel_check
from Thunder.utils.bot_utils import (
    notify_own, # Renamed
    reply_user_err, # Renamed
    log_newusr, # Renamed
    gen_links, # Renamed
    is_admin # Renamed
)

MAX_RETRIES = 3

async def fwd_media(m_msg: Message) -> Optional[Message]: # media_message -> m_msg
    for attempt in range(MAX_RETRIES):
        try:
            return await m_msg.copy(chat_id=Var.BIN_CHANNEL)
        except FloodWait as e:
            logger.warning(f"FloodWait: fwd_media copy (att {attempt + 1}), sleep {e.value}s")
            await asyncio.sleep(e.value)
            if attempt == MAX_RETRIES -1: raise
        except Exception as e:
            logger.warning(f"Error fwd_media copy (att {attempt + 1}): {e}. Trying forward.")
            try:
                return await m_msg.forward(chat_id=Var.BIN_CHANNEL)
            except FloodWait as fe:
                logger.warning(f"FloodWait: fwd_media fwd (att {attempt + 1}), sleep {fe.value}s")
                await asyncio.sleep(fe.value)
                if attempt == MAX_RETRIES -1: raise
            except Exception as final_e:
                logger.error(f"Error fwd_media fwd (att {attempt + 1}): {final_e}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(1 + attempt)
                else:
                    raise Exception(f"Failed fwd_media after {MAX_RETRIES} attempts: {final_e}")

async def log_req(fwd_msg_obj: Message, usr: Any, slink: str, olink: str): # log_msg -> fwd_msg_obj, user -> usr, stream_link -> slink, online_link -> olink
    try:
        src_info = getattr(usr, 'title', None) or f"{getattr(usr, 'first_name', '')} {getattr(usr, 'last_name', '')}".strip() or "Unknown"
        id_ = usr.id
        text_to_log = MSG_NEW_FILE_REQUEST.format(source_info=src_info, id_=id_, online_link=olink, stream_link=slink)
        await fwd_msg_obj.reply_text(
            text_to_log,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            quote=True
        )
    except FloodWait as e:
        logger.warning(f"FloodWait: log_req, sleep {e.value}s")
        await asyncio.sleep(e.value)
    except Exception as e:
        logger.warning(f"Failed log_req: {e}")

async def proc_media(cli: Client, cmd_msg: Message, m_msg: Message, shortener: bool = True) -> Optional[Dict[str, Any]]: # client->cli, command_message->cmd_msg, media_message->m_msg
    for attempt in range(MAX_RETRIES):
        try:
            fwd_msg_obj = await fwd_media(m_msg) # log_msg -> fwd_msg_obj
            if not fwd_msg_obj:
                await reply_user_err(cmd_msg, MSG_ERROR_PROCESSING_MEDIA)
                return None

            ld = await gen_links(fwd_msg_obj, shortener=shortener) # links_data -> ld
            ld['log_msg'] = fwd_msg_obj
            return ld
        except FloodWait as e:
            logger.warning(f"FloodWait: proc_media (att {attempt + 1}), sleep {e.value}s")
            await asyncio.sleep(e.value)
            if attempt == MAX_RETRIES -1:
                await reply_user_err(cmd_msg, f"Service busy (FloodWait). Try after {e.value}s.")
                return None
        except (FileReferenceExpired, FileReferenceInvalid) as e:
            logger.warning(f"FileRef error: proc_media (att {attempt + 1}): {e}. Retrying.")
            if attempt < MAX_RETRIES - 1:
                try: await m_msg.download(in_memory=True)
                except Exception as dl_err: logger.warning(f"Failed refresh FileRef via download: {dl_err}")
                await asyncio.sleep(1 + attempt)
            else:
                try: await cmd_msg.reply_text(MSG_MEDIA_ERROR, quote=True)
                except Exception as e_reply: logger.error(f"Error sending MSG_MEDIA_ERROR (FileRef): {e_reply}")
                return None
        except MediaEmpty:
            try: await cmd_msg.reply_text(MSG_MEDIA_ERROR, quote=True)
            except Exception as e_reply: logger.error(f"Error sending MSG_MEDIA_ERROR (MediaEmpty): {e_reply}")
            return None
        except Exception as e:
            logger.error(f"Error proc_media (att {attempt + 1}): {e}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(1 + attempt)
            else:
                await reply_user_err(cmd_msg, MSG_ERROR_PROCESSING_MEDIA)
                await notify_own(cli, MSG_CRITICAL_ERROR.format(error=str(e), error_id=uuid.uuid4().hex[:8]))
                return None
    return None

async def _reply_with_links(target_msg: Message, ld: Dict[str, Any]):
    links_txt = MSG_LINKS.format(
        file_name=ld['media_name'],
        file_size=ld['media_size'],
        download_link=ld['online_link'],
        stream_link=ld['stream_link']
    )
    links_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(MSG_BUTTON_STREAM_NOW, url=ld['stream_link'])],
        [InlineKeyboardButton(MSG_BUTTON_DOWNLOAD, url=ld['online_link'])]
    ])
    try:
        await target_msg.reply_text(
            text=links_txt, quote=True, parse_mode=enums.ParseMode.MARKDOWN,
            link_preview_options=LinkPreviewOptions(is_disabled=True), reply_markup=links_markup
        )
    except FloodWait as e:
        logger.warning(f"FloodWait: _reply_with_links to {target_msg.chat.id}, sleep {e.value}s")
        await asyncio.sleep(e.value)
        try:
            await target_msg.reply_text(
                text=links_txt, quote=True, parse_mode=enums.ParseMode.MARKDOWN,
                link_preview_options=LinkPreviewOptions(is_disabled=True), reply_markup=links_markup
            )
        except Exception as e_retry:
             logger.error(f"Error on retry _reply_with_links to {target_msg.chat.id}: {e_retry}")
             await reply_user_err(target_msg, MSG_ERROR_PROCESSING_MEDIA) # Fallback if sending link fails
    except Exception as e:
        logger.error(f"Error in _reply_with_links to {target_msg.chat.id}: {e}")
        await reply_user_err(target_msg, MSG_ERROR_PROCESSING_MEDIA)


async def proc_batch(cli: Client, cmd_msg: Message, r_msg: Message, num_f: int, stat_msg: Message, shortener: bool = True): # Renamed params
    cid = cmd_msg.chat.id # chat_id -> cid
    start_mid = r_msg.id # start_message_id -> start_mid
    mids = list(range(start_mid, start_mid + num_f)) # message_ids -> mids, num_files -> num_f

    proc_cnt, fail_cnt = 0, 0 # processed_count, failed_count
    dl_parts = [] # download_links_text_parts -> dl_parts
    last_stat_txt = ""

    async def upd_stat(txt: str): # update_status -> upd_stat, text -> txt
        nonlocal last_stat_txt
        if txt != last_stat_txt:
            try:
                await stat_msg.edit(txt)
                last_stat_txt = txt
            except MessageNotModified: pass
            except FloodWait as e_fld:
                logger.warning(f"FloodWait: upd_stat, sleep {e_fld.value}s")
                await asyncio.sleep(e_fld.value)
            except Exception as e_stat:
                logger.error(f"Error upd_stat: {e_stat}")

    for i in range(0, len(mids), 10):
        batch_ids = mids[i:i+10]
        await upd_stat(MSG_PROCESSING_BATCH.format(batch_number=(i//10)+1, total_batches=(len(mids)+9)//10, file_count=len(batch_ids)))

        batch_msgs = [] # messages_in_batch -> batch_msgs
        for att_fetch in range(MAX_RETRIES): # attempt_fetch -> att_fetch
            try:
                batch_msgs = await cli.get_messages(chat_id=cid, message_ids=batch_ids)
                break
            except FloodWait as e_fld:
                logger.warning(f"FloodWait: proc_batch get_msgs (att {att_fetch+1}), sleep {e_fld.value}s")
                await asyncio.sleep(e_fld.value)
                if att_fetch == MAX_RETRIES -1: batch_msgs = []
            except Exception as e_err:
                logger.error(f"Error proc_batch get_msgs (att {att_fetch+1}): {e_err}")
                if att_fetch < MAX_RETRIES -1: await asyncio.sleep(1+att_fetch)
                else: batch_msgs = []

        for b_msg in batch_msgs: # msg_in_batch -> b_msg
            if b_msg and b_msg.media:
                ld = await proc_media(cli, cmd_msg, b_msg, shortener=shortener) # links_data -> ld
                if ld and ld.get('online_link'):
                    dl_parts.append(ld['online_link'])
                    proc_cnt += 1
                else: fail_cnt += 1
            elif b_msg: fail_cnt +=1

            if (proc_cnt + fail_cnt) % 5 == 0 or (proc_cnt + fail_cnt) == num_f:
                 await upd_stat(MSG_PROCESSING_STATUS.format(processed=proc_cnt, total=num_f, failed=fail_cnt))

    def chunk_list(lst, n):
        for i_chunk in range(0, len(lst), n): yield lst[i_chunk:i_chunk + n]

    for chunk in chunk_list(dl_parts, 20):
        fmt_dlinks_txt = "\n".join(chunk) # links_text_formatted -> fmt_dlinks_txt
        grp_msg_txt = MSG_BATCH_LINKS_READY.format(count=len(chunk)) + f"\n\n`{fmt_dlinks_txt}`" # group_message_content -> grp_msg_txt
        dm_prefix = MSG_DM_BATCH_PREFIX.format(chat_title=cmd_msg.chat.title if cmd_msg.chat else "this chat")
        dm_txt = f"{dm_prefix}\n{grp_msg_txt}" # dm_message_text -> dm_txt
        try:
            await cmd_msg.reply_text(grp_msg_txt, quote=True, link_preview_options=LinkPreviewOptions(is_disabled=True), parse_mode=enums.ParseMode.MARKDOWN)
        except FloodWait as e_fld:
            logger.warning(f"FloodWait: proc_batch send group links, sleep {e_fld.value}s")
            await asyncio.sleep(e_fld.value)
            try: await cmd_msg.reply_text(grp_msg_txt, quote=True, link_preview_options=LinkPreviewOptions(is_disabled=True), parse_mode=enums.ParseMode.MARKDOWN)
            except Exception as e_inner: logger.error(f"Error proc_batch send group links retry: {e_inner}")
        except Exception as e_err: logger.error(f"Error proc_batch send group links: {e_err}")

        if cmd_msg.chat and cmd_msg.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP] and cmd_msg.from_user:
            try: # Using client.send_message, so _send_msg could be an option here if it were public / in this file
                await cli.send_message(chat_id=cmd_msg.from_user.id, text=dm_txt, link_preview_options=LinkPreviewOptions(is_disabled=True), parse_mode=enums.ParseMode.MARKDOWN)
            except FloodWait as e_fld:
                logger.warning(f"FloodWait: proc_batch send DM, sleep {e_fld.value}s")
                await asyncio.sleep(e_fld.value)
                try: await cli.send_message(chat_id=cmd_msg.from_user.id, text=dm_txt, link_preview_options=LinkPreviewOptions(is_disabled=True), parse_mode=enums.ParseMode.MARKDOWN)
                except Exception as e_inner_dm: logger.error(f"Error proc_batch send DM retry: {e_inner_dm}")
            except Exception: await cmd_msg.reply_text(MSG_ERROR_DM_FAILED, quote=True)
        await asyncio.sleep(0.2)
    await upd_stat(MSG_PROCESSING_RESULT.format(processed=proc_cnt, total=num_f, failed=fail_cnt))


@StreamBot.on_message(filters.command("link") & ~filters.private)
@check_banned
@require_token
@force_channel_check
async def link_handler(cli: Client, msg: Message, shortener: bool = True): # client->cli, message->msg
    uid = msg.from_user.id if msg.from_user else None # user_id -> uid
    if not await db.is_user_exist(uid) and uid:
        try:
            inv_link = f"https://t.me/{cli.me.username}?start=start" # invite_link -> inv_link
            await msg.reply_text(MSG_ERROR_START_BOT.format(invite_link=inv_link), link_preview_options=LinkPreviewOptions(is_disabled=True), parse_mode=enums.ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(MSG_BUTTON_START_CHAT, url=inv_link)]]), quote=True)
        except FloodWait as e: logger.warning(f"FloodWait: link_handler start_bot, sleep {e.value}s"); await asyncio.sleep(e.value)
        except Exception: pass
        return

    if msg.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        admin_check = await is_admin(cli, msg.chat.id) # check_admin_privileges -> is_admin
        if not admin_check:
            try: await msg.reply_text(MSG_ERROR_NOT_ADMIN, quote=True)
            except FloodWait as e: logger.warning(f"FloodWait: link_handler not_admin, sleep {e.value}s"); await asyncio.sleep(e.value)
            except Exception as e_admin: logger.error(f"Error: link_handler not_admin: {e_admin}")
            return

    if not msg.reply_to_message:
        try: await msg.reply_text(MSG_ERROR_REPLY_FILE, quote=True)
        except FloodWait as e: logger.warning(f"FloodWait: link_handler reply_file, sleep {e.value}s"); await asyncio.sleep(e.value)
        except Exception as e_reply: logger.error(f"Error: link_handler reply_file: {e_reply}")
        return

    r_msg = msg.reply_to_message # reply_msg -> r_msg
    if not r_msg.media:
        try: await msg.reply_text(MSG_ERROR_NO_FILE, quote=True)
        except FloodWait as e: logger.warning(f"FloodWait: link_handler no_file, sleep {e.value}s"); await asyncio.sleep(e.value)
        except Exception as e_media: logger.error(f"Error: link_handler no_file: {e_media}")
        return

    cmd_parts = msg.text.strip().split() # command_parts -> cmd_parts
    num_f = 1 # num_files -> num_f
    if len(cmd_parts) > 1:
        try:
            num_f = int(cmd_parts[1])
            if not (1 <= num_f <= 100):
                try: await msg.reply_text(MSG_ERROR_NUMBER_RANGE, quote=True)
                except FloodWait as e: logger.warning(f"FloodWait: link_handler num_range, sleep {e.value}s"); await asyncio.sleep(e.value)
                return
        except ValueError:
            try: await msg.reply_text(MSG_ERROR_INVALID_NUMBER, quote=True)
            except FloodWait as e: logger.warning(f"FloodWait: link_handler inv_num, sleep {e.value}s"); await asyncio.sleep(e.value)
            return

    p_msg = None # processing_msg -> p_msg
    try:
        p_msg = await msg.reply_text(MSG_PROCESSING_REQUEST, quote=True)
    except FloodWait as e:
        logger.warning(f"FloodWait: link_handler proc_req, sleep {e.value}s")
        await asyncio.sleep(e.value)
        try: p_msg = await msg.reply_text(MSG_PROCESSING_REQUEST, quote=True)
        except Exception as e_proc: logger.error(f"Error: link_handler proc_req retry: {e_proc}"); return
    except Exception as e_init_proc: logger.error(f"Error: link_handler proc_req initial: {e_init_proc}"); return

    if num_f == 1:
        try:
            ld = await proc_media(cli, msg, r_msg, shortener=shortener) # links_data -> ld
            if ld and ld.get('log_msg'):
                await _reply_with_links(msg, ld) # Use new helper
                await log_req(ld['log_msg'], msg.from_user, ld['stream_link'], ld['online_link'])
                if p_msg: await p_msg.delete()
            elif ld is None :
                 if p_msg: await p_msg.edit(MSG_ERROR_PROCESSING_MEDIA)
        except FloodWait as e_outer:
            logger.error(f"Overall FloodWait: link_handler single, sleep {e_outer.value}s")
            if p_msg: await p_msg.edit(f"Service busy (FloodWait). Try after {e_outer.value}s.")
            await asyncio.sleep(e_outer.value)
        except Exception as e_err:
            logger.error(f"Error link_handler single: {e_err}")
            if p_msg:
                try: await p_msg.edit(MSG_ERROR_PROCESSING_MEDIA)
                except Exception: pass
    else:
        await proc_batch(cli, msg, r_msg, num_f, p_msg, shortener)


@StreamBot.on_message(filters.private & filters.incoming & (filters.document | filters.video | filters.photo | filters.audio | filters.voice | filters.animation | filters.video_note), group=4)
@check_banned
@require_token
@force_channel_check
async def private_receive_handler(cli: Client, msg: Message, shortener: bool = True): # client->cli, message->msg
    if not msg.from_user: return
    await log_newusr(cli, msg.from_user.id, msg.from_user.first_name or "") # bot->cli

    p_msg = None # processing_msg -> p_msg
    try:
        p_msg = await msg.reply_text(MSG_PROCESSING_FILE, quote=True)
    except FloodWait as e:
        logger.warning(f"FloodWait: private_handler proc_file, sleep {e.value}s"); await asyncio.sleep(e.value)
        try: p_msg = await msg.reply_text(MSG_PROCESSING_FILE, quote=True)
        except Exception as e_proc: logger.error(f"Error: private_handler proc_file retry: {e_proc}"); return
    except Exception as e_init_proc: logger.error(f"Error: private_handler proc_file initial: {e_init_proc}"); return

    try:
        ld = await proc_media(cli, msg, msg, shortener=shortener) # links_data -> ld
        if ld and ld.get('log_msg'):
            await _reply_with_links(msg, ld) # Use new helper
            await log_req(ld['log_msg'], msg.from_user, ld['stream_link'], ld['online_link'])
            if p_msg: await p_msg.delete()
        elif ld is None:
            if p_msg: await p_msg.edit(MSG_ERROR_PROCESSING_MEDIA)
    except FloodWait as e_outer:
        logger.error(f"Overall FloodWait: private_handler, sleep {e_outer.value}s")
        if p_msg: await p_msg.edit(f"Service busy (FloodWait). Try after {e_outer.value}s.")
        await asyncio.sleep(e_outer.value)
    except Exception as e_err:
        logger.error(f"Error private_handler: {e_err}")
        if p_msg:
            try: await p_msg.edit(MSG_ERROR_PROCESSING_MEDIA)
            except Exception: pass


@StreamBot.on_message(filters.channel & filters.incoming & (filters.document | filters.video | filters.photo | filters.audio | filters.voice | filters.animation | filters.video_note) & ~filters.chat(Var.BIN_CHANNEL), group=-1)
async def channel_receive_handler(cli: Client, bcst: Message, shortener: bool = True): # client->cli, broadcast->bcst
    if hasattr(Var, 'BANNED_CHANNELS') and bcst.chat.id in Var.BANNED_CHANNELS:
        try: await cli.leave_chat(bcst.chat.id)
        except Exception as e: logger.warning(f"Failed leave banned ch {bcst.chat.id}: {e}")
        return

    can_edit = False
    try:
        member = await cli.get_chat_member(bcst.chat.id, cli.me.id)
        can_edit = member.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except Exception: pass

    try:
        ld = await proc_media(cli, bcst, bcst, shortener=shortener) # links_data -> ld
        if ld and ld.get('log_msg'):
            await log_req(ld['log_msg'], bcst.chat, ld['stream_link'], ld['online_link'])
            if can_edit:
                btns_markup = InlineKeyboardMarkup([ # reply_markup_buttons -> btns_markup
                    [InlineKeyboardButton(MSG_BUTTON_STREAM_NOW, url=ld['stream_link'])],
                    [InlineKeyboardButton(MSG_BUTTON_DOWNLOAD, url=ld['online_link'])]
                ])
                try:
                    await cli.edit_message_reply_markup(
                        chat_id=bcst.chat.id, message_id=bcst.id, reply_markup=btns_markup
                    )
                except FloodWait as e_edit:
                    logger.warning(f"FloodWait: channel_handler edit, sleep {e_edit.value}s")
                    await asyncio.sleep(e_edit.value)
                except Exception as e_err:
                    logger.warning(f"Could not edit ch msg {bcst.id} in {bcst.chat.id}: {e_err}")
    except FloodWait as e_outer:
        logger.warning(f"Overall FloodWait: channel_handler for {bcst.chat.id} msg {bcst.id}, sleep {e_outer.value}s")
        await asyncio.sleep(e_outer.value)
        await notify_own(cli, f"Channel proc for {bcst.chat.id} msg {bcst.id} hit FloodWait: {e_outer.value}s")
    except Exception as e_glob:
        logger.error(f"Global error: channel_handler for {bcst.chat.id} msg {bcst.id}: {e_glob}")
        await notify_own(cli, MSG_CRITICAL_ERROR.format(error=str(e_glob), error_id=uuid.uuid4().hex[:8]) + f" (Channel: {bcst.chat.id}, Msg: {bcst.id})")
