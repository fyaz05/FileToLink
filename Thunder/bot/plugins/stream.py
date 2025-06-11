import time
import asyncio
import uuid # Keep uuid for error IDs
from typing import Optional, Dict, Any # List, Set, Tuple, Union likely not needed after refactor

from pyrogram import Client, filters, enums
from pyrogram.errors import (
    FloodWait,
    MediaEmpty,
    FileReferenceExpired,
    FileReferenceInvalid,
    MessageNotModified # RPCError is too generic usually
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
    MSG_MEDIA_ERROR, MSG_CRITICAL_ERROR,
    MSG_PROCESSING_BATCH, MSG_PROCESSING_STATUS,
    MSG_BATCH_LINKS_READY, MSG_DM_BATCH_PREFIX, MSG_ERROR_DM_FAILED,
    MSG_PROCESSING_RESULT, MSG_PROCESSING_ERROR,
    MSG_NEW_FILE_REQUEST, # Used by log_req_in_bin
    MSG_LINKS, MSG_BUTTON_STREAM_NOW, MSG_BUTTON_DOWNLOAD,
    MSG_PROCESSING_FILE # For private_receive_handler initial reply
)
from Thunder.utils.logger import logger
from Thunder.vars import Var
from Thunder.utils.decorators import check_banned, require_token
from Thunder.utils.force_channel import force_channel_check
# Using minified names from bot_utils
from Thunder.utils.bot_utils import (
    notify_own,
    reply_user_err,
    log_newusr,
    gen_links, # generate_media_links became gen_links
    is_admin # check_admin_privileges became is_admin
)

MAX_RETRIES = 2 # Reduced max retries for some operations to fail faster

# Simplified forward_media (fwd_media)
async def fwd_media(m_msg: Message) -> Optional[Message]:
    for attempt in range(MAX_RETRIES):
        try:
            return await m_msg.copy(chat_id=Var.BIN_CHANNEL)
        except FloodWait as e:
            logger.warning(f"FloodWait: fwd_media copy (att {attempt + 1}), sleep {e.value}s")
            await asyncio.sleep(e.value +1) # Ensure sleep is slightly more than value
            if attempt == MAX_RETRIES -1 : raise # Raise on last attempt
        except Exception as e:
            logger.warning(f"Error fwd_media copy (att {attempt + 1}): {e}. Trying forward.")
            try:
                return await m_msg.forward(chat_id=Var.BIN_CHANNEL)
            except FloodWait as fe:
                logger.warning(f"FloodWait: fwd_media fwd (att {attempt + 1}), sleep {fe.value}s")
                await asyncio.sleep(fe.value +1)
                if attempt == MAX_RETRIES -1: raise
            except Exception as final_e:
                logger.error(f"Error fwd_media fwd (att {attempt + 1}): {final_e}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(1 + attempt)
                else: # Raise on last attempt
                    raise Exception(f"Failed fwd_media after {MAX_RETRIES} attempts: {final_e}")
    # Removed unreachable: return None

# Simplified log_request_in_bin (log_req_in_bin)
async def log_req_in_bin(log_msg_obj: Message, usr_or_chat: Any, links_data: Dict[str,str]):
    try:
        # Determine if user or chat
        if hasattr(usr_or_chat, 'first_name') or hasattr(usr_or_chat, 'username'): # User attributes
            src_info = f"{getattr(usr_or_chat, 'first_name', '')} {getattr(usr_or_chat, 'last_name', '')}".strip() or getattr(usr_or_chat, 'username', 'Unknown User')
            id_ = usr_or_chat.id
        elif hasattr(usr_or_chat, 'title'): # Chat attributes
            src_info = usr_or_chat.title
            id_ = usr_or_chat.id
        else: # Fallback
            src_info = "Unknown Source"
            id_ = "N/A"

        text_to_log = MSG_NEW_FILE_REQUEST.format(
            source_info=src_info,
            id_=id_,
            online_link=links_data['online_link'],
            stream_link=links_data['stream_link']
        )
        await log_msg_obj.reply_text(
            text=text_to_log,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            quote=True
        )
    except FloodWait as e: # Specific FloodWait handling
        logger.warning(f"FloodWait: log_req_in_bin for log_msg {log_msg_obj.id}, sleep {e.value}s")
        await asyncio.sleep(e.value + 1)
    except Exception as e: # Catch general errors
        logger.warning(f"Failed log_req_in_bin for log_msg {log_msg_obj.id}: {e}")

# process_media, refactored from process_media_message
async def proc_media(cli: Client, cmd_msg: Message, m_msg: Message, shortener: bool = True) -> Optional[Dict[str, Any]]:
    for attempt in range(MAX_RETRIES):
        try:
            fwd_msg = await fwd_media(m_msg) # Use refactored fwd_media
            # Removed redundant check: if not fwd_msg as fwd_media now raises on failure

            links_data = await gen_links(fwd_msg, shortener=shortener) # gen_links from bot_utils
            # Add the forwarded message object to links_data to be used for logging
            links_data['log_msg_obj'] = fwd_msg
            return links_data
        except FloodWait as e:
            logger.warning(f"FloodWait: proc_media (att {attempt + 1}), sleep {e.value}s")
            await asyncio.sleep(e.value + 1)
            if attempt == MAX_RETRIES -1:
                await reply_user_err(cmd_msg, f"Service busy due to high load (FloodWait). Please try again after {e.value} seconds.")
                return None
        except (FileReferenceExpired, FileReferenceInvalid) as e_fr:
            logger.warning(f"FileReference error: proc_media (att {attempt + 1}): {e_fr}. Retrying.")
            if attempt < MAX_RETRIES - 1:
                try:
                    # Attempt to "refresh" the message object, simple re-fetch might work
                    m_msg = await cli.get_messages(m_msg.chat.id, m_msg.id)
                except Exception as e_refresh: logger.warning(f"Failed to refresh message on FileRef error: {e_refresh}")
                await asyncio.sleep(1 + attempt) # Wait a bit before retrying
            else: # Last attempt failed
                await reply_user_err(cmd_msg, MSG_MEDIA_ERROR + " (File reference issue)")
                return None
        except MediaEmpty:
            await reply_user_err(cmd_msg, MSG_MEDIA_ERROR + " (Media is empty)")
            return None
        except Exception as e:
            logger.error(f"Error in proc_media (att {attempt + 1}): {e}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(1 + attempt) # Wait before retrying
            else: # Last attempt failed
                await reply_user_err(cmd_msg, MSG_ERROR_PROCESSING_MEDIA)
                await notify_own(cli, MSG_CRITICAL_ERROR.format(error=str(e), error_id=uuid.uuid4().hex[:8]))
                return None
    return None # Should be unreachable if MAX_RETRIES >=1

# Helper to send links to the user
async def _reply_with_links(target_msg: Message, links_data: Dict[str, Any]):
    reply_text = MSG_LINKS.format(
        file_name=links_data['media_name'],
        file_size=links_data['media_size'],
        download_link=links_data['online_link'],
        stream_link=links_data['stream_link']
    )
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(MSG_BUTTON_STREAM_NOW, url=links_data['stream_link'])],
        [InlineKeyboardButton(MSG_BUTTON_DOWNLOAD, url=links_data['online_link'])]
    ])
    try:
        await target_msg.reply_text(
            text=reply_text, quote=True, parse_mode=enums.ParseMode.MARKDOWN,
            link_preview_options=LinkPreviewOptions(is_disabled=True), reply_markup=reply_markup
        )
    except FloodWait as e:
        logger.warning(f"FloodWait: _reply_with_links to {target_msg.chat.id}, sleep {e.value}s. Retrying.")
        await asyncio.sleep(e.value + 1)
        try: # Retry once
            await target_msg.reply_text(
                text=reply_text, quote=True, parse_mode=enums.ParseMode.MARKDOWN,
                link_preview_options=LinkPreviewOptions(is_disabled=True), reply_markup=reply_markup
            )
        except Exception as e_retry:
             logger.error(f"Error on retry _reply_with_links to {target_msg.chat.id}: {e_retry}")
             # Not calling reply_user_err here to avoid potential loop if reply_user_err also fails
    except Exception as e:
        logger.error(f"Error in _reply_with_links to {target_msg.chat.id}: {e}")

# process_batch, refactored from process_multiple_messages
async def proc_batch(cli: Client, cmd_msg: Message, r_msg: Message, num_f: int, stat_msg: Message, shortener: bool = True):
    chat_id = cmd_msg.chat.id
    start_mid = r_msg.id
    mids_to_proc = list(range(start_mid, start_mid + num_f))

    proc_c = 0; fail_c = 0 # Minified counters
    dl_links_list = []
    last_stat_upd_txt = ""

    async def _update_status_msg(text_to_set: str):
        nonlocal last_stat_upd_txt
        if text_to_set != last_stat_upd_txt:
            try:
                await stat_msg.edit_text(text_to_set)
                last_stat_upd_txt = text_to_set
            except MessageNotModified: pass # Ignore if text is the same
            except FloodWait as e_fw_stat:
                logger.warning(f"FloodWait: proc_batch _update_status_msg, sleep {e_fw_stat.value}s")
                await asyncio.sleep(e_fw_stat.value + 1)
            except Exception as e_stat_edit:
                logger.error(f"Error editing status message in proc_batch: {e_stat_edit}")

    for i in range(0, len(mids_to_proc), 10): # Process in sub-batches of 10 for get_messages
        current_batch_ids = mids_to_proc[i:i+10]
        await _update_status_msg(MSG_PROCESSING_BATCH.format(batch_number=(i//10)+1, total_batches=(len(mids_to_proc)+9)//10, file_count=len(current_batch_ids)))

        batch_msgs_retrieved = []
        for fetch_att in range(MAX_RETRIES): # Retries for fetching batch messages
            try:
                batch_msgs_retrieved = await cli.get_messages(chat_id=chat_id, message_ids=current_batch_ids)
                break
            except FloodWait as e_fld_fetch:
                logger.warning(f"FloodWait: proc_batch get_messages (att {fetch_att+1}), sleep {e_fld_fetch.value}s")
                await asyncio.sleep(e_fld_fetch.value +1)
                if fetch_att == MAX_RETRIES -1 : batch_msgs_retrieved = [] # Failed to fetch
            except Exception as e_err_fetch:
                logger.error(f"Error proc_batch get_messages (att {fetch_att+1}): {e_err_fetch}")
                if fetch_att < MAX_RETRIES -1: await asyncio.sleep(1+fetch_att)
                else: batch_msgs_retrieved = [] # Failed to fetch

        for b_msg_item in batch_msgs_retrieved: # Minified var name
            if b_msg_item and b_msg_item.media:
                links_data = await proc_media(cli, cmd_msg, b_msg_item, shortener=shortener) # Use refactored proc_media
                if links_data and links_data.get('online_link'):
                    dl_links_list.append(links_data['online_link'])
                    proc_c += 1
                    if links_data.get('log_msg_obj') and cmd_msg.from_user : # Log individual success if needed
                         await log_req_in_bin(links_data['log_msg_obj'], cmd_msg.from_user, links_data)
                else: fail_c += 1
            elif b_msg_item: fail_c +=1 # Message exists but no media or processing failed earlier

            if (proc_c + fail_c) % 5 == 0 or (proc_c + fail_c) == num_f: # Update status every 5 files or at the end
                 await _update_status_msg(MSG_PROCESSING_STATUS.format(processed=proc_c, total=num_f, failed=fail_c))

    def _chunk_list_for_sending(lst, n_size):
        for i_chunk_send in range(0, len(lst), n_size): yield lst[i_chunk_send:i_chunk_send + n_size]

    for link_chunk in _chunk_list_for_sending(dl_links_list, 20): # Send links in chunks of 20
        formatted_links_text = "\n".join(link_chunk)
        group_msg_text = MSG_BATCH_LINKS_READY.format(count=len(link_chunk)) + f"\n\n`{formatted_links_text}`"

        try: # Send to group/chat first
            await cmd_msg.reply_text(group_msg_text, quote=True, link_preview_options=LinkPreviewOptions(is_disabled=True), parse_mode=enums.ParseMode.MARKDOWN)
        except FloodWait as e_fld_grp:
            logger.warning(f"FloodWait: proc_batch sending group links, sleep {e_fld_grp.value}s. Retrying.")
            await asyncio.sleep(e_fld_grp.value + 1)
            try: await cmd_msg.reply_text(group_msg_text, quote=True, link_preview_options=LinkPreviewOptions(is_disabled=True), parse_mode=enums.ParseMode.MARKDOWN)
            except Exception as e_inner_grp: logger.error(f"Error proc_batch sending group links on retry: {e_inner_grp}")
        except Exception as e_err_grp: logger.error(f"Error proc_batch sending group links: {e_err_grp}")

        if cmd_msg.chat.type != enums.ChatType.PRIVATE and cmd_msg.from_user: # Send to DM if not already in DM
            dm_prefix_text = MSG_DM_BATCH_PREFIX.format(chat_title=cmd_msg.chat.title or "the chat")
            dm_full_text = f"{dm_prefix_text}\n{group_msg_text}"
            try:
                await cli.send_message(chat_id=cmd_msg.from_user.id, text=dm_full_text, link_preview_options=LinkPreviewOptions(is_disabled=True), parse_mode=enums.ParseMode.MARKDOWN)
            except FloodWait as e_fld_dm:
                logger.warning(f"FloodWait: proc_batch sending DM, sleep {e_fld_dm.value}s. Retrying.")
                await asyncio.sleep(e_fld_dm.value + 1)
                try: await cli.send_message(chat_id=cmd_msg.from_user.id, text=dm_full_text, link_preview_options=LinkPreviewOptions(is_disabled=True), parse_mode=enums.ParseMode.MARKDOWN)
                except Exception as e_inner_dm: logger.error(f"Error proc_batch sending DM on retry: {e_inner_dm}")
            except Exception: await cmd_msg.reply_text(MSG_ERROR_DM_FAILED, quote=True) # Inform group if DM fails
        await asyncio.sleep(0.3)

    await _update_status_msg(MSG_PROCESSING_RESULT.format(processed=proc_c, total=num_f, failed=fail_c))


@StreamBot.on_message(filters.command("link") & ~filters.private)
@check_banned
@require_token
@force_channel_check # Assuming force_channel_check is D3/D11a compliant
async def link_handler(cli: Client, msg: Message, shortener: bool = True):
    uid = msg.from_user.id if msg.from_user else None
    if uid and not await db.is_user_exist(uid): # Check uid exists before db call
        try:
            inv_link = f"https://t.me/{cli.me.username}?start=start" # Use cli.me.username
            await msg.reply_text(MSG_ERROR_START_BOT.format(invite_link=inv_link),
                                 link_preview_options=LinkPreviewOptions(is_disabled=True),
                                 parse_mode=enums.ParseMode.MARKDOWN,
                                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(MSG_BUTTON_START_CHAT, url=inv_link)]]),
                                 quote=True)
        except FloodWait as e_fw: logger.warning(f"FloodWait: link_handler start_bot msg for {uid}, sleep {e_fw.value}s"); await asyncio.sleep(e_fw.value +1)
        except Exception as e_start_bot : logger.error(f"Error link_handler start_bot msg for {uid}: {e_start_bot}")
        return

    if msg.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        admin_ok = await is_admin(cli, msg.chat.id) # Use is_admin from bot_utils
        if not admin_ok:
            await reply_user_err(msg, MSG_ERROR_NOT_ADMIN) # Use reply_user_err
            return

    if not msg.reply_to_message:
        await reply_user_err(msg, MSG_ERROR_REPLY_FILE)
        return

    r_msg = msg.reply_to_message # Minified var name
    if not r_msg.media:
        await reply_user_err(msg, MSG_ERROR_NO_FILE)
        return

    cmd_parts = msg.text.strip().split()
    num_f = 1
    if len(cmd_parts) > 1:
        try:
            num_f = int(cmd_parts[1])
            if not (1 <= num_f <= Var.MAX_BATCH_FILES): # Use Var for max batch files
                await reply_user_err(msg, MSG_ERROR_NUMBER_RANGE.format(max_files=Var.MAX_BATCH_FILES))
                return
        except ValueError:
            await reply_user_err(msg, MSG_ERROR_INVALID_NUMBER)
            return

    prog_msg = None # Minified var name for progress message
    try:
        prog_msg = await msg.reply_text(MSG_PROCESSING_REQUEST, quote=True)
    except FloodWait as e_fw_prog:
        logger.warning(f"FloodWait: link_handler sending processing_request for {uid}, sleep {e_fw_prog.value}s"); await asyncio.sleep(e_fw_prog.value +1)
        try: prog_msg = await msg.reply_text(MSG_PROCESSING_REQUEST, quote=True) # Retry once
        except Exception as e_prog_retry: logger.error(f"Error link_handler proc_req retry for {uid}: {e_prog_retry}"); return
    except Exception as e_init_prog: logger.error(f"Error link_handler proc_req initial for {uid}: {e_init_prog}"); return
    if not prog_msg : return # If progress message failed to send

    if num_f == 1:
        links_data = await proc_media(cli, msg, r_msg, shortener=shortener)
        if links_data and links_data.get('log_msg_obj'):
            await _reply_with_links(msg, links_data) # Use new helper
            if msg.from_user: # Ensure from_user exists
                await log_req_in_bin(links_data['log_msg_obj'], msg.from_user, links_data)
            if prog_msg: await prog_msg.delete()
        elif prog_msg: # If proc_media failed but prog_msg was sent
            try: await prog_msg.edit_text(MSG_ERROR_PROCESSING_MEDIA)
            except Exception: pass # Ignore edit errors if original processing failed
    else:
        await proc_batch(cli, msg, r_msg, num_f, prog_msg, shortener=shortener)


@StreamBot.on_message(filters.private & filters.incoming & (filters.document | filters.video | filters.photo | filters.audio | filters.voice | filters.animation | filters.video_note), group=4)
@check_banned
@require_token
@force_channel_check
async def private_receive_handler(cli: Client, msg: Message, shortener: bool = True):
    if not msg.from_user: return # Should not happen in private but good check

    await log_newusr(cli, msg.from_user.id, msg.from_user.first_name or "") # Use log_newusr

    prog_msg = None
    try:
        prog_msg = await msg.reply_text(MSG_PROCESSING_FILE, quote=True)
    except FloodWait as e_fw_prog:
        logger.warning(f"FloodWait: private_handler sending proc_file for {msg.from_user.id}, sleep {e_fw_prog.value}s"); await asyncio.sleep(e_fw_prog.value +1)
        try: prog_msg = await msg.reply_text(MSG_PROCESSING_FILE, quote=True)
        except Exception as e_prog_retry: logger.error(f"Error: private_handler proc_file retry for {msg.from_user.id}: {e_prog_retry}"); return
    except Exception as e_init_prog: logger.error(f"Error: private_handler proc_file initial for {msg.from_user.id}: {e_init_prog}"); return
    if not prog_msg: return

    links_data = await proc_media(cli, msg, msg, shortener=shortener) # Use refactored proc_media
    if links_data and links_data.get('log_msg_obj'):
        await _reply_with_links(msg, links_data) # Use new helper
        await log_req_in_bin(links_data['log_msg_obj'], msg.from_user, links_data)
        if prog_msg: await prog_msg.delete()
    elif prog_msg: # If proc_media failed
        try: await prog_msg.edit_text(MSG_ERROR_PROCESSING_MEDIA)
        except Exception: pass


@StreamBot.on_message(filters.channel & filters.incoming & (filters.document | filters.video | filters.photo | filters.audio | filters.voice | filters.animation | filters.video_note) & ~filters.chat(Var.BIN_CHANNEL), group=-1)
async def channel_receive_handler(cli: Client, bcst_msg: Message, shortener: bool = True): # Renamed bcst to bcst_msg
    if hasattr(Var, 'BANNED_CHANNELS') and bcst_msg.chat.id in Var.BANNED_CHANNELS:
        try: await cli.leave_chat(bcst_msg.chat.id)
        except Exception as e_leave: logger.warning(f"Failed to leave banned channel {bcst_msg.chat.id}: {e_leave}")
        return

    can_edit = False
    try:
        member = await cli.get_chat_member(bcst_msg.chat.id, cli.me.id)
        can_edit = member.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except Exception as e_perm:
        logger.warning(f"Failed to check permissions in channel {bcst_msg.chat.id}: {e_perm}")

    links_data = await proc_media(cli, bcst_msg, bcst_msg, shortener=shortener) # Use refactored proc_media
    if links_data and links_data.get('log_msg_obj'):
        await log_req_in_bin(links_data['log_msg_obj'], bcst_msg.chat, links_data) # Log with chat object
        if can_edit:
            btns_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton(MSG_BUTTON_STREAM_NOW, url=links_data['stream_link'])],
                [InlineKeyboardButton(MSG_BUTTON_DOWNLOAD, url=links_data['online_link'])]
            ])
            try:
                await cli.edit_message_reply_markup(
                    chat_id=bcst_msg.chat.id, message_id=bcst_msg.id, reply_markup=btns_markup
                )
            except FloodWait as e_edit_fw:
                logger.warning(f"FloodWait: channel_handler edit_message_reply_markup for {bcst_msg.chat.id} msg {bcst_msg.id}, sleep {e_edit_fw.value}s")
                await asyncio.sleep(e_edit_fw.value + 1)
            except Exception as e_err_edit:
                logger.warning(f"Could not edit channel message {bcst_msg.id} in {bcst_msg.chat.id}: {e_err_edit}")
    # No specific error reply to channel, just log if proc_media failed (it logs its own errors)
