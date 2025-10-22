# Thunder/bot/plugins/stream.py

import asyncio
import secrets
from typing import Any, Dict, Optional

from pyrogram import Client, enums, filters
from pyrogram.errors import FloodWait, MessageNotModified, MessageDeleteForbidden, MessageIdInvalid
from pyrogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                            Message)

from Thunder.bot import StreamBot
from Thunder.utils.bot_utils import (gen_links, is_admin, log_newusr, notify_own,
                                     reply_user_err)
from Thunder.utils.database import db
from Thunder.utils.decorators import (check_banned, get_shortener_status,
                                      require_token)
from Thunder.utils.force_channel import force_channel_check
from Thunder.utils.logger import logger
from Thunder.utils.messages import (
    MSG_BATCH_LINKS_READY, MSG_BUTTON_DOWNLOAD, MSG_BUTTON_START_CHAT,
    MSG_BUTTON_STREAM_NOW, MSG_CRITICAL_ERROR, MSG_DM_BATCH_PREFIX,
    MSG_DM_SINGLE_PREFIX, MSG_ERROR_DM_FAILED, MSG_ERROR_INVALID_NUMBER,
    MSG_ERROR_NO_FILE, MSG_ERROR_NOT_ADMIN, MSG_ERROR_NUMBER_RANGE,
    MSG_ERROR_PROCESSING_MEDIA, MSG_ERROR_REPLY_FILE, MSG_ERROR_START_BOT,
    MSG_LINKS, MSG_NEW_FILE_REQUEST, MSG_PROCESSING_BATCH,
    MSG_PROCESSING_FILE, MSG_PROCESSING_REQUEST, MSG_PROCESSING_RESULT,
    MSG_PROCESSING_STATUS
)
from Thunder.utils.rate_limiter import handle_rate_limited_request
from Thunder.vars import Var

BATCH_SIZE = 10
LINK_CHUNK_SIZE = 20
BATCH_UPDATE_INTERVAL = 5
MESSAGE_DELAY = 0.5


async def fwd_media(m_msg: Message) -> Optional[Message]:
    try:
        try:
            return await m_msg.copy(chat_id=Var.BIN_CHANNEL)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            return await m_msg.copy(chat_id=Var.BIN_CHANNEL)
    except Exception as e:
        if "MEDIA_CAPTION_TOO_LONG" in str(e):
            logger.debug(f"MEDIA_CAPTION_TOO_LONG error, retrying without caption: {e}")
            try:
                return await m_msg.copy(chat_id=Var.BIN_CHANNEL, caption=None)
            except FloodWait as e:
                await asyncio.sleep(e.value)
                return await m_msg.copy(chat_id=Var.BIN_CHANNEL, caption=None)
        logger.error(f"Error fwd_media copy: {e}", exc_info=True)
        return None


def get_link_buttons(links):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(MSG_BUTTON_STREAM_NOW, url=links['stream_link']),
        InlineKeyboardButton(MSG_BUTTON_DOWNLOAD, url=links['online_link'])
    ]])

async def validate_request_common(client: Client, message: Message) -> Optional[bool]:
    if not await check_banned(client, message):
        return None
    if not await require_token(client, message):
        return None
    if not await force_channel_check(client, message):
        return None
    return await get_shortener_status(client, message)


async def send_channel_links(target_msg: Message, links: Dict[str, Any], source_info: str, source_id: int):
    try:
        await target_msg.reply_text(
            MSG_NEW_FILE_REQUEST.format(
                source_info=source_info,
                id_=source_id,
                online_link=links['online_link'],
                stream_link=links['stream_link']
            ),
            disable_web_page_preview=True,
            quote=True
        )
    except FloodWait as e:
        await asyncio.sleep(e.value)
        await target_msg.reply_text(
            MSG_NEW_FILE_REQUEST.format(
                source_info=source_info,
                id_=source_id,
                online_link=links['online_link'],
                stream_link=links['stream_link']
            ),
            disable_web_page_preview=True,
            quote=True
        )


async def safe_edit_message(message: Message, text: str, **kwargs):
    try:
        try:
            return await message.edit_text(text, **kwargs)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            return await message.edit_text(text, **kwargs)
    except MessageNotModified:
        pass
    except MessageDeleteForbidden:
        logger.debug(f"Failed to edit message {message.id} due to permissions.")
    except Exception as e:
        logger.error(f"Error editing message {message.id}: {e}", exc_info=True)


async def safe_delete_message(message: Message):
    try:
        try:
            await message.delete()
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await message.delete()
    except MessageDeleteForbidden:
        logger.debug(f"Failed to delete message {message.id} due to permissions.")
    except Exception as e:
        logger.error(f"Error deleting message {message.id}: {e}", exc_info=True)


async def send_dm_links(bot: Client, user_id: int, links: Dict[str, Any], chat_title: str):
    try:
        dm_text = MSG_DM_SINGLE_PREFIX.format(chat_title=chat_title) + "\n" + \
                  MSG_LINKS.format(
                      file_name=links['media_name'],
                      file_size=links['media_size'],
                      download_link=links['online_link'],
                      stream_link=links['stream_link']
                  )
        try:
            await bot.send_message(
                chat_id=user_id,
                text=dm_text,
                disable_web_page_preview=True,
                parse_mode=enums.ParseMode.MARKDOWN,
                reply_markup=get_link_buttons(links)
            )
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await bot.send_message(
                chat_id=user_id,
                text=dm_text,
                disable_web_page_preview=True,
                parse_mode=enums.ParseMode.MARKDOWN,
                reply_markup=get_link_buttons(links)
            )
    except Exception as e:
        logger.error(f"Error sending DM to user {user_id}: {e}", exc_info=True)


async def send_link(msg: Message, links: Dict[str, Any]):
    try:
        await msg.reply_text(
            MSG_LINKS.format(
                file_name=links['media_name'],
                file_size=links['media_size'],
                download_link=links['online_link'],
                stream_link=links['stream_link']
            ),
            quote=True,
            parse_mode=enums.ParseMode.MARKDOWN,
            disable_web_page_preview=True,
            reply_markup=get_link_buttons(links)
        )
    except FloodWait as e:
        await asyncio.sleep(e.value)
        await msg.reply_text(
            MSG_LINKS.format(
                file_name=links['media_name'],
                file_size=links['media_size'],
                download_link=links['online_link'],
                stream_link=links['stream_link']
            ),
            quote=True,
            parse_mode=enums.ParseMode.MARKDOWN,
            disable_web_page_preview=True,
            reply_markup=get_link_buttons(links)
        )


@StreamBot.on_message(filters.command("link") & ~filters.private)
async def link_handler(bot: Client, msg: Message, **kwargs):
    async def _actual_link_handler(client: Client, message: Message, **handler_kwargs):
        shortener_val = await validate_request_common(client, message)
        if shortener_val is None:
            return
        if message.from_user and not await db.is_user_exist(message.from_user.id):
            invite_link = f"https://t.me/{client.me.username}?start=start"
            try:
                await message.reply_text(
                    MSG_ERROR_START_BOT.format(invite_link=invite_link),
                    disable_web_page_preview=True,
                    parse_mode=enums.ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(MSG_BUTTON_START_CHAT, url=invite_link)]]),
                    quote=True
                )
            except FloodWait as e:
                await asyncio.sleep(e.value)
                await message.reply_text(
                    MSG_ERROR_START_BOT.format(invite_link=invite_link),
                    disable_web_page_preview=True,
                    parse_mode=enums.ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(MSG_BUTTON_START_CHAT, url=invite_link)]]),
                    quote=True
                )
            return

        if (message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]
                and not await is_admin(client, message.chat.id)):
            await reply_user_err(message, MSG_ERROR_NOT_ADMIN)
            return

        if not message.reply_to_message or not message.reply_to_message.media:
            await reply_user_err(
                message,
                MSG_ERROR_REPLY_FILE if not message.reply_to_message else MSG_ERROR_NO_FILE)
            return

        notification_msg = handler_kwargs.get('notification_msg')

        parts = message.text.split()
        num_files = 1
        if len(parts) > 1:
            try:
                num_files = int(parts[1])
                if not 1 <= num_files <= Var.MAX_BATCH_FILES:
                    await reply_user_err(
                        message,
                        MSG_ERROR_NUMBER_RANGE.format(max_files=Var.MAX_BATCH_FILES))
                    return
            except ValueError:
                await reply_user_err(message, MSG_ERROR_INVALID_NUMBER)
                return

        try:
            status_msg = await message.reply_text(MSG_PROCESSING_REQUEST, quote=True)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            status_msg = await message.reply_text(MSG_PROCESSING_REQUEST, quote=True)
        shortener_val = handler_kwargs.get('shortener', shortener_val)
        if num_files == 1:
            await process_single(client, message, message.reply_to_message, status_msg, shortener_val, notification_msg=notification_msg)
        else:
            await process_batch(client, message, message.reply_to_message.id, num_files, status_msg, shortener_val, notification_msg=notification_msg)

    await handle_rate_limited_request(bot, msg, _actual_link_handler, **kwargs)


@StreamBot.on_message(
    filters.private &
    filters.incoming &
    (filters.document | filters.video | filters.photo | filters.audio |
     filters.voice | filters.animation | filters.video_note),
    group=4
)
async def private_receive_handler(bot: Client, msg: Message, **kwargs):
    async def _actual_private_receive_handler(client: Client, message: Message, **handler_kwargs):
        shortener_val = await validate_request_common(client, message)
        if shortener_val is None:
            return
        if not message.from_user:
            return

        notification_msg = handler_kwargs.get('notification_msg')

        await log_newusr(client, message.from_user.id, message.from_user.first_name or "")
        try:
            status_msg = await message.reply_text(MSG_PROCESSING_FILE, quote=True)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            status_msg = await message.reply_text(MSG_PROCESSING_FILE, quote=True)
        await process_single(client, message, message, status_msg, shortener_val, notification_msg=notification_msg)

    await handle_rate_limited_request(bot, msg, _actual_private_receive_handler, **kwargs)


@StreamBot.on_message(
    filters.channel &
    filters.incoming &
    (filters.document | filters.video | filters.audio) &
    ~filters.chat(Var.BIN_CHANNEL),
    group=-1
)
async def channel_receive_handler(bot: Client, msg: Message):
    async def _actual_channel_receive_handler(client: Client, message: Message, **handler_kwargs):
        if not Var.CHANNEL:
            return
        notification_msg = handler_kwargs.get('notification_msg')

        is_banned_statically = hasattr(Var, 'BANNED_CHANNELS') and message.chat.id in Var.BANNED_CHANNELS
        is_banned_dynamically = await db.is_channel_banned(message.chat.id) is not None

        if is_banned_statically or is_banned_dynamically:
            try:
                try:
                    await client.leave_chat(message.chat.id)
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    await client.leave_chat(message.chat.id)
            except Exception as e:
                logger.error(f"Error leaving banned channel {message.chat.id}: {e}")
            return
        if not await is_admin(client, message.chat.id):
            logger.debug(
                f"Bot is not admin in channel {message.chat.id} "
                f"({message.chat.title or 'Unknown'}). Ignoring message.")
            return

        try:
            stored_msg = await fwd_media(message)
            if not stored_msg:
                logger.error(
                    f"Failed to forward media from channel {message.chat.id}. Ignoring.")
                return
            shortener_val = await get_shortener_status(client, message)
            links = await gen_links(stored_msg, shortener=shortener_val)
            source_info = message.chat.title or "Unknown Channel"

            if notification_msg:
                try:
                    try:
                        await notification_msg.edit_text(
                            MSG_NEW_FILE_REQUEST.format(
                                source_info=source_info,
                                id_=message.chat.id,
                                online_link=links['online_link'],
                                stream_link=links['stream_link']
                            ),
                            disable_web_page_preview=True
                        )
                    except FloodWait as e:
                        await asyncio.sleep(e.value)
                        await notification_msg.edit_text(
                            MSG_NEW_FILE_REQUEST.format(
                                source_info=source_info,
                                id_=message.chat.id,
                                online_link=links['online_link'],
                                stream_link=links['stream_link']
                            ),
                            disable_web_page_preview=True
                        )
                except Exception as e:
                    logger.error(f"Error editing notification message with links: {e}", exc_info=True)
                    await send_channel_links(stored_msg, links, source_info, message.chat.id)
            else:
                await send_channel_links(stored_msg, links, source_info, message.chat.id)

            try:
                try:
                    await message.edit_reply_markup(reply_markup=get_link_buttons(links))
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    await message.edit_reply_markup(reply_markup=get_link_buttons(links))
            except (MessageNotModified, MessageDeleteForbidden, MessageIdInvalid):
                logger.debug(f"Failed to edit reply markup for message {message.id} due to not modified, permissions or invalid ID. Sending new link instead.")
                await send_link(message, links)
            except Exception as e:
                logger.error(f"Error editing reply markup for message {message.id}: {e}", exc_info=True)
                await send_link(message, links)
        except Exception as e:
            logger.error(f"Error in _actual_channel_receive_handler for message {message.id}: {e}", exc_info=True)

    rl_user_id = None
    if msg.sender_chat and msg.sender_chat.id:
        rl_user_id = msg.sender_chat.id
    elif msg.from_user:
        rl_user_id = msg.from_user.id
    
    if rl_user_id is None:
        logger.debug(f"No identifiable user/channel for rate limiting for message {msg.id}. Skipping rate limit check and processing directly.")
        await _actual_channel_receive_handler(bot, msg)
        return

    await handle_rate_limited_request(bot, msg, _actual_channel_receive_handler, rl_user_id=rl_user_id)


async def process_single(
    bot: Client,
    msg: Message,
    file_msg: Message,
    status_msg: Message,
    shortener_val: bool,
    original_request_msg: Optional[Message] = None,
    notification_msg: Optional[Message] = None
):
    try:
        stored_msg = await fwd_media(file_msg)
        if not stored_msg:
            logger.error(f"Failed to forward media for message {file_msg.id}. Skipping.")
            return None
        links = await gen_links(stored_msg, shortener=shortener_val)
        if notification_msg:
            await safe_edit_message(
                notification_msg,
                MSG_LINKS.format(
                    file_name=links['media_name'],
                    file_size=links['media_size'],
                    download_link=links['online_link'],
                    stream_link=links['stream_link']
                ),
                parse_mode=enums.ParseMode.MARKDOWN,
                disable_web_page_preview=True,
                reply_markup=get_link_buttons(links)
            )
        elif not original_request_msg:
            await send_link(msg, links)
        if msg.chat.type != enums.ChatType.PRIVATE and msg.from_user and not original_request_msg:
            await send_dm_links(bot, msg.from_user.id, links, msg.chat.title or "the chat")
        source_msg = original_request_msg if original_request_msg else msg
        source_info = ""
        source_id = 0
        if source_msg.from_user:
            source_info = source_msg.from_user.full_name
            if not source_info:
                source_info = f"@{source_msg.from_user.username}" if source_msg.from_user.username else "Unknown User"
            source_id = source_msg.from_user.id
        elif source_msg.chat.type == enums.ChatType.CHANNEL:
            source_info = source_msg.chat.title or "Unknown Channel"
            source_id = source_msg.chat.id
        if source_info and source_id:
            try:
                await stored_msg.reply_text(
                    MSG_NEW_FILE_REQUEST.format(
                        source_info=source_info,
                        id_=source_id,
                        online_link=links['online_link'],
                        stream_link=links['stream_link']
                    ),
                    disable_web_page_preview=True,
                    quote=True
                )
            except FloodWait as e:
                await asyncio.sleep(e.value)
                await stored_msg.reply_text(
                    MSG_NEW_FILE_REQUEST.format(
                        source_info=source_info,
                        id_=source_id,
                        online_link=links['online_link'],
                        stream_link=links['stream_link']
                    ),
                    disable_web_page_preview=True,
                    quote=True
                )
        if status_msg:
            await safe_delete_message(status_msg)
        return links
    except Exception as e:
        logger.error(f"Error processing single file for message {file_msg.id}: {e}", exc_info=True)
        if status_msg:
            await safe_edit_message(status_msg, MSG_ERROR_PROCESSING_MEDIA)
        
        await notify_own(bot, MSG_CRITICAL_ERROR.format(
            error=str(e),
            error_id=secrets.token_hex(6)
        ))
        return None


async def process_batch(
    bot: Client,
    msg: Message,
    start_id: int,
    count: int,
    status_msg: Message,
    shortener_val: bool,
    notification_msg: Optional[Message] = None
):
    processed = 0
    failed = 0
    links_list = []
    for batch_start in range(0, count, BATCH_SIZE):
        batch_size = min(BATCH_SIZE, count - batch_start)
        batch_ids = list(range(start_id + batch_start, start_id + batch_start + batch_size))
        try:
            try:
                await status_msg.edit_text(
                    MSG_PROCESSING_BATCH.format(
                        batch_number=(batch_start // BATCH_SIZE) + 1,
                        total_batches=(count + BATCH_SIZE - 1) // BATCH_SIZE,
                        file_count=batch_size
                    )
                )
            except FloodWait as e:
                await asyncio.sleep(e.value)
                await status_msg.edit_text(
                    MSG_PROCESSING_BATCH.format(
                        batch_number=(batch_start // BATCH_SIZE) + 1,
                        total_batches=(count + BATCH_SIZE - 1) // BATCH_SIZE,
                        file_count=batch_size
                    )
                )
        except MessageNotModified:
            pass
        try:
            try:
                messages = await bot.get_messages(msg.chat.id, batch_ids)
            except FloodWait as e:
                await asyncio.sleep(e.value)
                messages = await bot.get_messages(msg.chat.id, batch_ids)
            if messages is None:
                messages = []
        except Exception as e:
            logger.error(f"Error getting messages in batch: {e}", exc_info=True)
            messages = []
        for m in messages:
            if m and m.media:
                links = await process_single(bot, msg, m, None, shortener_val, original_request_msg=msg)
                if links:
                    links_list.append(links['online_link'])
                    processed += 1
                else:
                    failed += 1
            else:
                failed += 1
        if (processed + failed) % BATCH_UPDATE_INTERVAL == 0 or (processed + failed) == count:
            try:
                try:
                    await status_msg.edit_text(
                        MSG_PROCESSING_STATUS.format(
                            processed=processed,
                            total=count,
                            failed=failed
                        )
                    )
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    await status_msg.edit_text(
                        MSG_PROCESSING_STATUS.format(
                            processed=processed,
                            total=count,
                            failed=failed
                        )
                    )
            except MessageNotModified:
                pass
    for i in range(0, len(links_list), LINK_CHUNK_SIZE):
        chunk = links_list[i:i+LINK_CHUNK_SIZE]
        chunk_text = MSG_BATCH_LINKS_READY.format(count=len(chunk)) + f"\n\n`{chr(10).join(chunk)}`"
        try:
            await msg.reply_text(
                chunk_text,
                quote=True,
                disable_web_page_preview=True,
                parse_mode=enums.ParseMode.MARKDOWN
            )
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await msg.reply_text(
                chunk_text,
                quote=True,
                disable_web_page_preview=True,
                parse_mode=enums.ParseMode.MARKDOWN
            )
        if msg.chat.type != enums.ChatType.PRIVATE and msg.from_user:
            try:
                try:
                    await bot.send_message(
                        chat_id=msg.from_user.id,
                        text=MSG_DM_BATCH_PREFIX.format(chat_title=msg.chat.title or "the chat") + "\n" + chunk_text,
                        disable_web_page_preview=True,
                        parse_mode=enums.ParseMode.MARKDOWN
                    )
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    await bot.send_message(
                        chat_id=msg.from_user.id,
                        text=MSG_DM_BATCH_PREFIX.format(chat_title=msg.chat.title or "the chat") + "\n" + chunk_text,
                        disable_web_page_preview=True,
                        parse_mode=enums.ParseMode.MARKDOWN
                    )
            except Exception as e:
                logger.error(f"Error sending DM in batch: {e}", exc_info=True)
                await reply_user_err(msg, MSG_ERROR_DM_FAILED)
        if i + LINK_CHUNK_SIZE < len(links_list):
            await asyncio.sleep(MESSAGE_DELAY)
    try:
        await status_msg.edit_text(
            MSG_PROCESSING_RESULT.format(
                processed=processed,
                total=count,
                failed=failed
            )
        )
    except FloodWait as e:
        await asyncio.sleep(e.value)
        await status_msg.edit_text(
            MSG_PROCESSING_RESULT.format(
                processed=processed,
                total=count,
                failed=failed
            )
        )
    if notification_msg:
        await safe_delete_message(notification_msg)
