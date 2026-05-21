import asyncio
import secrets
from typing import Any

import pytdbot
from pytdbot import types

from Thunder.bot import StreamBot
from Thunder.utils.bot_utils import gen_canonical_links, gen_links, is_admin, log_newusr, notify_own, reply_user_err
from Thunder.utils.canonical_files import get_or_create_canonical_file
from Thunder.utils.compat import (
    Filters,
    _get_media_file,
)
from Thunder.utils.database import db
from Thunder.utils.decorators import check_banned, get_shortener_status, require_token
from Thunder.utils.force_channel import force_channel_check
from Thunder.utils.logger import logger
from Thunder.utils.messages import (
    MSG_BATCH_LINKS_READY,
    MSG_BUTTON_DOWNLOAD,
    MSG_BUTTON_START_CHAT,
    MSG_BUTTON_STREAM_NOW,
    MSG_CRITICAL_ERROR,
    MSG_DM_BATCH_PREFIX,
    MSG_DM_SINGLE_PREFIX,
    MSG_ERROR_DM_FAILED,
    MSG_ERROR_INVALID_NUMBER,
    MSG_ERROR_NO_FILE,
    MSG_ERROR_NOT_ADMIN,
    MSG_ERROR_NUMBER_RANGE,
    MSG_ERROR_PROCESSING_MEDIA,
    MSG_ERROR_REPLY_FILE,
    MSG_ERROR_START_BOT,
    MSG_LINKS,
    MSG_NEW_FILE_REQUEST,
    MSG_PROCESSING_BATCH,
    MSG_PROCESSING_FILE,
    MSG_PROCESSING_REQUEST,
    MSG_PROCESSING_RESULT,
    MSG_PROCESSING_STATUS,
)
from Thunder.utils.rate_limiter import handle_rate_limited_request
from Thunder.vars import Var

BATCH_SIZE = 10
LINK_CHUNK_SIZE = 20
BATCH_UPDATE_INTERVAL = 5
MESSAGE_DELAY = 0.5
BATCH_CONCURRENCY = 3


async def fwd_media(m_msg: types.Message) -> types.Message | None:
    for attempt in range(3):
        try:
            result = await StreamBot.sendCopy(
                chat_id=Var.BIN_CHANNEL,
                from_chat_id=m_msg.chat_id,
                message_id=m_msg.id,
            )
            if isinstance(result, types.Error):
                logger.error(f"Error fwd_media copy (attempt {attempt + 1}): {result.message}")
                if attempt < 2:
                    await asyncio.sleep(1 * (2 ** attempt))
                    continue
                return None
            return result
        except Exception as e:
            logger.error(f"Error fwd_media copy (attempt {attempt + 1}): {e}", exc_info=True)
            if attempt < 2:
                await asyncio.sleep(1 * (2 ** attempt))
                continue
            return None
    return None


def get_link_buttons(links):
    return types.ReplyMarkupInlineKeyboard(rows=[[
        types.InlineKeyboardButton(
            text=MSG_BUTTON_STREAM_NOW,
            type=types.InlineKeyboardButtonTypeUrl(url=links['stream_link'])
        ),
        types.InlineKeyboardButton(
            text=MSG_BUTTON_DOWNLOAD,
            type=types.InlineKeyboardButtonTypeUrl(url=links['online_link'])
        )
    ]])


async def validate_request_common(client: pytdbot.Client, message: types.Message) -> bool | None:
    if not await check_banned(client, message):
        return None
    if not await require_token(client, message):
        return None
    if not await force_channel_check(client, message):
        return None
    return await get_shortener_status(client, message)


async def send_channel_links(
    links: dict[str, Any],
    source_info: str,
    source_id: int,
    *,
    target_msg: types.Message | None = None,
    reply_to_message_id: int | None = None
):
    try:
        text = MSG_NEW_FILE_REQUEST.format(
            source_info=source_info,
            id_=source_id,
            online_link=links['online_link'],
            stream_link=links['stream_link']
        )
        if target_msg:
            await target_msg.reply_text(text)
        else:
            result = await StreamBot.sendTextMessage(
                chat_id=Var.BIN_CHANNEL,
                text=text,
                reply_to_message_id=reply_to_message_id or 0
            )
            if isinstance(result, types.Error):
                logger.warning(f"Failed to send channel links: {result.message}")
    except Exception as e:
        logger.error(f"Error sending channel links: {e}", exc_info=True)


async def safe_edit_message(message: types.Message, text: str, **kwargs):
    try:
        result = await message.editTextMessage(
            chat_id=message.chat_id,
            message_id=message.id,
            text=text
        )
        if isinstance(result, types.Error):
            logger.debug(f"Failed to edit message: {result.message}")
    except Exception as e:
        logger.debug(f"Error editing message: {e}")


async def safe_delete_message(message: types.Message):
    try:
        await message.delete()
    except Exception as e:
        logger.debug(f"Failed to delete message: {e}")


async def send_dm_links(bot: pytdbot.Client, user_id: int, links: dict[str, Any], chat_title: str):
    try:
        dm_text = MSG_DM_SINGLE_PREFIX.format(chat_title=chat_title) + "\n" + \
                  MSG_LINKS.format(
                      file_name=links['media_name'],
                      file_size=links['media_size'],
                      download_link=links['online_link'],
                      stream_link=links['stream_link']
                  )
        result = await bot.sendTextMessage(
            chat_id=user_id,
            text=dm_text,
            reply_markup=get_link_buttons(links)
        )
        if isinstance(result, types.Error):
            logger.warning(f"Failed to send DM links to user {user_id}: {result.message}")
    except Exception as e:
        logger.error(f"Error sending DM to user {user_id}: {e}", exc_info=True)


async def send_link(msg: types.Message, links: dict[str, Any]):
    try:
        await msg.reply_text(
            MSG_LINKS.format(
                file_name=links['media_name'],
                file_size=links['media_size'],
                download_link=links['online_link'],
                stream_link=links['stream_link']
            ),
            reply_markup=get_link_buttons(links)
        )
    except Exception as e:
        logger.error(f"Error sending link: {e}", exc_info=True)


def _is_group_chat(message: types.Message) -> bool:
    chat = getattr(message, "chat", None)
    if chat and isinstance(chat.type, (types.ChatTypeBasicGroup, types.ChatTypeSupergroup)):
        if isinstance(chat.type, types.ChatTypeSupergroup):
            return not chat.type.is_channel
        return True
    return False


def _is_channel_chat(message: types.Message) -> bool:
    chat = getattr(message, "chat", None)
    if chat and isinstance(chat.type, types.ChatTypeSupergroup):
        return chat.type.is_channel
    return False


@StreamBot.on_message(filters=Filters.command("link") & ~Filters.private)
async def link_handler(bot: pytdbot.Client, msg: types.Message, **kwargs):
    async def _actual_link_handler(client: pytdbot.Client, message: types.Message, **handler_kwargs):
        shortener_val = await validate_request_common(client, message)
        if shortener_val is None:
            return

        from_id = getattr(message, "from_id", 0)
        if from_id and not await db.is_user_exist(from_id):
            me = await client.getMe()
            bot_username = "bot"
            if not isinstance(me, types.Error):
                if hasattr(me, "usernames") and me.usernames:
                    bot_username = me.usernames.editable_username or "bot"
                else:
                    bot_username = getattr(me, "username", "bot")
            invite_link = f"https://t.me/{bot_username}?start=start"
            try:
                await message.reply_text(
                    MSG_ERROR_START_BOT.format(invite_link=invite_link),
                    reply_markup=types.ReplyMarkupInlineKeyboard(rows=[[
                        types.InlineKeyboardButton(
                            text=MSG_BUTTON_START_CHAT,
                            type=types.InlineKeyboardButtonTypeUrl(url=invite_link)
                        )
                    ]])
                )
            except Exception:
                logger.debug(f"Failed to send start bot invite to user {from_id}")
                pass
            return

        if _is_group_chat(message) and not await is_admin(client, message.chat_id):
            await reply_user_err(message, MSG_ERROR_NOT_ADMIN)
            return

        reply_to = getattr(message, "reply_to", None)
        replied_has_media = False
        if reply_to and hasattr(reply_to, "message_id"):
            ref = await client.getMessage(chat_id=message.chat_id, message_id=reply_to.message_id)
            if not isinstance(ref, types.Error) and ref:
                replied_has_media = _get_media_file(ref) is not None

        if not reply_to or not replied_has_media:
            await reply_user_err(
                message,
                MSG_ERROR_REPLY_FILE if not reply_to else MSG_ERROR_NO_FILE)
            return

        notification_msg = handler_kwargs.get('notification_msg')

        text = getattr(message, "text", "") or ""
        parts = text.split()
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

        status_msg = await message.reply_text(MSG_PROCESSING_REQUEST)
        shortener_val = handler_kwargs.get('shortener', shortener_val)
        if num_files == 1:
            ref_msg = await client.getMessage(chat_id=message.chat_id, message_id=reply_to.message_id)
            if isinstance(ref_msg, types.Error):
                await reply_user_err(message, MSG_ERROR_NO_FILE)
                return
            await process_single(client, message, ref_msg, status_msg, shortener_val, notification_msg=notification_msg)
        else:
            await process_batch(client, message, reply_to.message_id, num_files, status_msg, shortener_val, notification_msg=notification_msg)

    await handle_rate_limited_request(bot, msg, _actual_link_handler, **kwargs)


@StreamBot.on_message(filters=Filters.private & Filters.incoming & Filters.media, group=4)
async def private_receive_handler(bot: pytdbot.Client, msg: types.Message, **kwargs):
    async def _actual_private_receive_handler(client: pytdbot.Client, message: types.Message, **handler_kwargs):
        shortener_val = await validate_request_common(client, message)
        if shortener_val is None:
            return
        from_id = getattr(message, "from_id", None)
        if not from_id:
            return

        notification_msg = handler_kwargs.get('notification_msg')

        await log_newusr(client, from_id, "")
        status_msg = await message.reply_text(MSG_PROCESSING_FILE)
        await process_single(client, message, message, status_msg, shortener_val, notification_msg=notification_msg)

    await handle_rate_limited_request(bot, msg, _actual_private_receive_handler, **kwargs)


@StreamBot.on_message(filters=Filters.channel & Filters.incoming & (Filters.document | Filters.video | Filters.audio))
async def channel_receive_handler(bot: pytdbot.Client, msg: types.Message):
    async def _actual_channel_receive_handler(client: pytdbot.Client, message: types.Message, **handler_kwargs):
        if not Var.CHANNEL:
            return
        notification_msg = handler_kwargs.get('notification_msg')

        is_banned_statically = hasattr(Var, 'BANNED_CHANNELS') and message.chat_id in Var.BANNED_CHANNELS
        is_banned_dynamically = await db.is_channel_banned(message.chat_id) is not None

        if is_banned_statically or is_banned_dynamically:
            result = await client.leaveChat(chat_id=message.chat_id)
            if isinstance(result, types.Error):
                logger.warning(f"Error leaving banned channel {message.chat_id}: {result.message}")
            return
        if not await is_admin(client, message.chat_id):
            return

        try:
            shortener_val = await get_shortener_status(client, message)
            canonical_record, stored_msg, reused_existing = await get_or_create_canonical_file(message, fwd_media)
            if reused_existing and stored_msg:
                await safe_delete_message(stored_msg)
                stored_msg = None
            if canonical_record:
                links = await gen_canonical_links(
                    file_name=canonical_record["file_name"],
                    file_size=int(canonical_record.get("file_size", 0) or 0),
                    public_hash=canonical_record["public_hash"],
                    shortener=shortener_val
                )
                reply_to_message_id = int(canonical_record["canonical_message_id"])
            else:
                if not stored_msg:
                    stored_msg = await fwd_media(message)
                    if not stored_msg:
                        return
                links = await gen_links(stored_msg, shortener=shortener_val)
                reply_to_message_id = stored_msg.id
            source_info = message.chat.title if hasattr(message.chat, "title") else "Unknown Channel"

            if notification_msg:
                try:
                    await notification_msg.editTextMessage(
                        chat_id=notification_msg.chat_id,
                        message_id=notification_msg.id,
                        text=MSG_NEW_FILE_REQUEST.format(
                            source_info=source_info,
                            id_=message.chat_id,
                            online_link=links['online_link'],
                            stream_link=links['stream_link']
                        )
                    )
                except Exception as e:
                    logger.error(f"Error editing notification message: {e}", exc_info=True)
                    await send_channel_links(links, source_info, message.chat_id, target_msg=stored_msg, reply_to_message_id=reply_to_message_id)
            else:
                await send_channel_links(links, source_info, message.chat_id, target_msg=stored_msg, reply_to_message_id=reply_to_message_id)

            try:
                result = await client.editMessageReplyMarkup(
                    chat_id=message.chat_id,
                    message_id=message.id,
                    reply_markup=get_link_buttons(links),
                )
                if isinstance(result, types.Error):
                    await send_link(message, links)
            except Exception:
                await send_link(message, links)
        except Exception as e:
            logger.error(f"Error in channel_receive_handler: {e}", exc_info=True)

    rl_user_id = None
    sender_chat = getattr(msg, "sender_chat", None)
    if sender_chat:
        rl_user_id = getattr(sender_chat, "id", None)
    from_id = getattr(msg, "from_id", None)
    if rl_user_id is None and from_id:
        rl_user_id = from_id

    if rl_user_id is None:
        await _actual_channel_receive_handler(bot, msg)
        return

    await handle_rate_limited_request(bot, msg, _actual_channel_receive_handler, rl_user_id=rl_user_id)


async def process_single(
    bot: pytdbot.Client,
    msg: types.Message,
    file_msg: types.Message,
    status_msg: types.Message | None,
    shortener_val: bool,
    original_request_msg: types.Message | None = None,
    notification_msg: types.Message | None = None
):
    try:
        canonical_record, stored_msg, reused_existing = await get_or_create_canonical_file(file_msg, fwd_media)
        if reused_existing and stored_msg:
            await safe_delete_message(stored_msg)
            stored_msg = None
        if canonical_record:
            links = await gen_canonical_links(
                file_name=canonical_record["file_name"],
                file_size=int(canonical_record.get("file_size", 0) or 0),
                public_hash=canonical_record["public_hash"],
                shortener=shortener_val
            )
            canonical_reply_id = int(canonical_record["canonical_message_id"])
        else:
            if not stored_msg:
                stored_msg = await fwd_media(file_msg)
                if not stored_msg:
                    logger.error(f"Failed to forward media for message {file_msg.id}. Skipping.")
                    return None
            links = await gen_links(stored_msg, shortener=shortener_val)
            canonical_reply_id = stored_msg.id

        if notification_msg:
            result = await notification_msg.editTextMessage(
                chat_id=notification_msg.chat_id,
                message_id=notification_msg.id,
                text=MSG_LINKS.format(
                    file_name=links['media_name'],
                    file_size=links['media_size'],
                    download_link=links['online_link'],
                    stream_link=links['stream_link']
                ),
                reply_markup=get_link_buttons(links)
            )
            if isinstance(result, types.Error):
                await send_link(msg, links)
        elif not original_request_msg:
            await send_link(msg, links)

        is_group = _is_group_chat(msg)
        from_id = getattr(msg, "from_id", None)
        if is_group and from_id and not original_request_msg:
            chat_title = msg.chat.title if hasattr(msg.chat, "title") else "the chat"
            try:
                await send_dm_links(bot, from_id, links, chat_title)
            except Exception:
                await reply_user_err(msg, MSG_ERROR_DM_FAILED)

        source_msg = original_request_msg if original_request_msg else msg
        source_info = ""
        source_id = 0
        source_from_id = getattr(source_msg, "from_id", None)
        if source_from_id:
            source_info = f"user_{source_from_id}"
            source_id = source_from_id
        if source_msg.chat and hasattr(source_msg.chat, "title") and source_msg.chat.title:
            source_info = source_msg.chat.title
        if source_info and source_id:
            try:
                await send_channel_links(
                    links, source_info, source_id,
                    target_msg=stored_msg,
                    reply_to_message_id=canonical_reply_id
                )
            except Exception as e:
                logger.error(f"Error sending channel links: {e}", exc_info=True)

        if status_msg:
            await safe_delete_message(status_msg)
        return links
    except Exception as e:
        logger.error(f"Error processing single file: {e}", exc_info=True)
        if status_msg:
            await safe_edit_message(status_msg, MSG_ERROR_PROCESSING_MEDIA)

        await notify_own(bot, MSG_CRITICAL_ERROR.format(
            error=str(e),
            error_id=secrets.token_hex(6)
        ))
        return None


async def process_batch(
    bot: pytdbot.Client,
    msg: types.Message,
    start_id: int,
    count: int,
    status_msg: types.Message,
    shortener_val: bool,
    notification_msg: types.Message | None = None
):
    processed = 0
    failed = 0
    links_list = []
    for batch_start in range(0, count, BATCH_SIZE):
        batch_size = min(BATCH_SIZE, count - batch_start)
        batch_ids = list(range(start_id + batch_start, start_id + batch_start + batch_size))
        try:
            await status_msg.editTextMessage(
                chat_id=status_msg.chat_id,
                message_id=status_msg.id,
                text=MSG_PROCESSING_BATCH.format(
                    batch_number=(batch_start // BATCH_SIZE) + 1,
                    total_batches=(count + BATCH_SIZE - 1) // BATCH_SIZE,
                    file_count=batch_size
                )
            )
        except Exception:
            logger.debug(f"Failed to edit batch processing status message for batch starting at {batch_start}")

        result = await bot.getMessages(chat_id=msg.chat_id, message_ids=batch_ids)
        messages = []
        if not isinstance(result, types.Error) and result:
            messages = result.messages if hasattr(result, "messages") else []

        sem = asyncio.Semaphore(BATCH_CONCURRENCY)

        async def _process_one(m):
            async with sem:
                if m and _get_media_file(m):
                    return await process_single(bot, msg, m, None, shortener_val, original_request_msg=msg)
                return None

        results = await asyncio.gather(*[_process_one(m) for m in messages])
        for links in results:
            if links:
                links_list.append(links['online_link'])
                processed += 1
            else:
                failed += 1

        if (processed + failed) % BATCH_UPDATE_INTERVAL == 0 or (processed + failed) == count:
            try:
                await status_msg.editTextMessage(
                    chat_id=status_msg.chat_id,
                    message_id=status_msg.id,
                    text=MSG_PROCESSING_STATUS.format(processed=processed, total=count, failed=failed)
                )
            except Exception:
                logger.debug(f"Failed to edit processing status message ({processed}/{count})")

    dm_failed = False
    for i in range(0, len(links_list), LINK_CHUNK_SIZE):
        chunk = links_list[i:i + LINK_CHUNK_SIZE]
        chunk_text = MSG_BATCH_LINKS_READY.format(count=len(chunk)) + f"\n\n<code>{chr(10).join(chunk)}</code>"
        try:
            await msg.reply_text(chunk_text)
        except Exception:
            logger.debug(f"Failed to reply with batch links chunk (index {i})")
        from_id = getattr(msg, "from_id", None)
        if _is_group_chat(msg) and from_id and not dm_failed:
            try:
                chat_title = msg.chat.title if hasattr(msg.chat, "title") else "the chat"
                await bot.sendTextMessage(
                    chat_id=from_id,
                    text=MSG_DM_BATCH_PREFIX.format(chat_title=chat_title) + "\n" + chunk_text
                )
            except Exception as e:
                logger.error(f"Error sending DM in batch: {e}", exc_info=True)
                dm_failed = True
                await reply_user_err(msg, MSG_ERROR_DM_FAILED)
        if i + LINK_CHUNK_SIZE < len(links_list):
            await asyncio.sleep(MESSAGE_DELAY)

    try:
        await status_msg.editTextMessage(
            chat_id=status_msg.chat_id,
            message_id=status_msg.id,
            text=MSG_PROCESSING_RESULT.format(processed=processed, total=count, failed=failed)
        )
    except Exception:
        logger.debug(f"Failed to edit final batch result message ({processed}/{count})")
    if notification_msg:
        await safe_delete_message(notification_msg)
