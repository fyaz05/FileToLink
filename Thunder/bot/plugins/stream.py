# Thunder/bot/plugins/stream.py

import asyncio
import html
import secrets
import time
from typing import Any

from pyrogram import Client, enums, filters
from pyrogram.errors import MessageDeleteForbidden, MessageIdInvalid, MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from Thunder.bot import StreamBot
from Thunder.utils.bot_utils import (
    format_link_message,
    gen_canonical_links,
    gen_links,
    is_admin,
    log_newusr,
    notify_own,
    reply_user_err,
)
from Thunder.utils.canonical_files import get_or_create_canonical_file
from Thunder.utils.database import db
from Thunder.utils.decorators import preflight
from Thunder.utils.flag_cache import flags
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
    MSG_NEW_FILE_REQUEST,
    MSG_PROCESSING_BATCH,
    MSG_PROCESSING_FILE,
    MSG_PROCESSING_REQUEST,
    MSG_PROCESSING_RESULT,
    MSG_PROCESSING_STATUS,
)
from Thunder.utils.rate_limiter import handle_rate_limited_request
from Thunder.utils.safe_call import (
    delete_safe,
    edit_safe,
    reply_safe,
    send_safe,
    tg_call,
)
from Thunder.vars import Var

BATCH_SIZE = 10
LINK_CHUNK_SIZE = 20
BATCH_UPDATE_INTERVAL = 5
MESSAGE_DELAY = 0.5
# M4b: overall batch deadline = 30 + 2n seconds
_BATCH_DEADLINE_BASE = 30


async def fwd_media(m_msg: Message) -> Message | None:
    try:
        result = await tg_call(m_msg.copy, chat_id=Var.BIN_CHANNEL)
    except Exception as e:
        if "MEDIA_CAPTION_TOO_LONG" in str(e):
            logger.debug(f"MEDIA_CAPTION_TOO_LONG error, retrying without caption: {e}")
            try:
                result = await tg_call(m_msg.copy, chat_id=Var.BIN_CHANNEL, caption=None)
            except Exception as e2:
                logger.error(f"Error fwd_media copy (no caption): {e2}", exc_info=True)
                return None
        else:
            logger.error(f"Error fwd_media copy: {e}", exc_info=True)
            return None
    if isinstance(result, list):  # defensive: pyrogram returns a list for multi-chat copies
        return result[0] if result else None
    return result


def get_link_buttons(links):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(MSG_BUTTON_STREAM_NOW, url=links["stream_link"]),
                InlineKeyboardButton(MSG_BUTTON_DOWNLOAD, url=links["online_link"]),
            ]
        ]
    )


async def validate_request_common(client: Client, message: Message) -> bool | None:
    """M12: one preflight chain for every stream entry point.

    Order (documented contract, see AGENTS.md):
    banned -> private-mode -> token-activation -> force-sub -> shortener-status
    """
    shortener_val = await preflight(client, message)
    if shortener_val is None:
        return None
    from Thunder.utils.decorators import force_sub_gate

    if not await force_sub_gate(client, message):
        return None
    return shortener_val


async def send_channel_links(
    links: dict[str, Any],
    source_info: str,
    source_id: int,
    *,
    target_msg: Message | None = None,
    reply_to_message_id: int | None = None,
):
    text = MSG_NEW_FILE_REQUEST.format(
        # source_info (display name / chat title) is user-controlled and renders
        # as HTML under pyrofork's DEFAULT parse mode (M7)
        source_info=html.escape(source_info),
        id_=source_id,
        online_link=links["online_link"],
        stream_link=links["stream_link"],
    )
    try:
        if target_msg:
            await tg_call(
                target_msg.reply_text,
                text,
                disable_web_page_preview=True,
                quote=True,
                parse_mode=enums.ParseMode.HTML,  # M7 template is HTML
            )
        else:
            await send_safe(
                StreamBot,
                Var.BIN_CHANNEL,
                text=text,
                disable_web_page_preview=True,
                reply_to_message_id=reply_to_message_id,
                parse_mode=enums.ParseMode.HTML,  # M7 template is HTML
            )
    except Exception as e:
        logger.error(f"Error sending channel links: {e}", exc_info=True)


async def safe_edit_message(message: Message, text: str, **kwargs):
    try:
        return await edit_safe(message, text, **kwargs)
    except MessageNotModified:
        pass
    except MessageDeleteForbidden:
        logger.debug(f"Failed to edit message {message.id} due to permissions.")
    except Exception as e:
        logger.error(f"Error editing message {message.id}: {e}", exc_info=True)


async def safe_delete_message(message: Message):
    try:
        await delete_safe(message)
    except MessageDeleteForbidden:
        logger.debug(f"Failed to delete message {message.id} due to permissions.")
    except Exception as e:
        logger.error(f"Error deleting message {message.id}: {e}", exc_info=True)


async def send_dm_links(bot: Client, user_id: int, links: dict[str, Any], chat_title: str):
    try:
        dm_text = (
            MSG_DM_SINGLE_PREFIX.format(chat_title=html.escape(chat_title))
            + "\n"
            + format_link_message(links)
        )
        await send_safe(
            bot,
            user_id,
            text=dm_text,
            disable_web_page_preview=True,
            parse_mode=enums.ParseMode.HTML,
            reply_markup=get_link_buttons(links),
        )
    except Exception as e:
        logger.error(f"Error sending DM to user {user_id}: {e}", exc_info=True)


async def send_link(msg: Message, links: dict[str, Any]):
    await reply_safe(
        msg,
        format_link_message(links),
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=get_link_buttons(links),
    )


@StreamBot.on_message(filters.command("link") & ~filters.private)
async def link_handler(bot: Client, msg: Message, **kwargs):
    # A channel-posted /link has no from_user: key the limiter on the
    # sender chat instead of dropping the request.
    if kwargs.get("rl_user_id") is None and msg.sender_chat and msg.sender_chat.id:
        kwargs["rl_user_id"] = msg.sender_chat.id

    async def _actual_link_handler(client: Client, message: Message, **handler_kwargs):
        shortener_val = await validate_request_common(client, message)
        if shortener_val is None:
            return
        if message.from_user and not await db.is_user_exist(message.from_user.id):
            # client.me is populated after client.start(); the stub union
            # is unavoidable here.
            invite_link = f"https://t.me/{client.me.username}?start=start"  # type: ignore[union-attr]
            try:
                await reply_safe(
                    message,
                    MSG_ERROR_START_BOT.format(invite_link=invite_link),
                    disable_web_page_preview=True,
                    parse_mode=enums.ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton(MSG_BUTTON_START_CHAT, url=invite_link)]]
                    ),
                )
            except Exception as e:
                logger.error(f"Error sending start-bot hint: {e}", exc_info=True)
            return

        if message.chat.type in [
            enums.ChatType.GROUP,
            enums.ChatType.SUPERGROUP,
        ] and not await is_admin(client, message.chat.id):
            await reply_user_err(message, MSG_ERROR_NOT_ADMIN)
            return

        if not message.reply_to_message or not message.reply_to_message.media:
            await reply_user_err(
                message, MSG_ERROR_REPLY_FILE if not message.reply_to_message else MSG_ERROR_NO_FILE
            )
            return

        notification_msg = handler_kwargs.get("notification_msg")

        # filters.command matches captions too, where .text is None --
        # parse the caption or a captioned /link dies silently
        parts = (message.text or message.caption or "").split()
        num_files = 1
        if len(parts) > 1:
            try:
                num_files = int(parts[1])
                if not 1 <= num_files <= Var.MAX_BATCH_FILES:
                    await reply_user_err(
                        message, MSG_ERROR_NUMBER_RANGE.format(max_files=Var.MAX_BATCH_FILES)
                    )
                    return
            except ValueError:
                await reply_user_err(message, MSG_ERROR_INVALID_NUMBER)
                return

        try:
            status_msg = await reply_safe(message, MSG_PROCESSING_REQUEST)
        except Exception as e:
            logger.error(f"Could not send processing status: {e}", exc_info=True)
            return
        if num_files == 1:
            await process_single(
                client,
                message,
                message.reply_to_message,
                status_msg,
                shortener_val,
                notification_msg=notification_msg,
            )
        else:
            await process_batch(
                client,
                message,
                message.reply_to_message.id,
                num_files,
                status_msg,
                shortener_val,
                notification_msg=notification_msg,
            )

    await handle_rate_limited_request(bot, msg, _actual_link_handler, **kwargs)


@StreamBot.on_message(
    filters.private
    & filters.incoming
    & (
        filters.document
        | filters.video
        | filters.photo
        | filters.audio
        | filters.voice
        | filters.animation
        | filters.video_note
    ),
    group=4,
)
async def private_receive_handler(bot: Client, msg: Message, **kwargs):
    async def _actual_private_receive_handler(client: Client, message: Message, **handler_kwargs):
        shortener_val = await validate_request_common(client, message)
        if shortener_val is None:
            return
        if not message.from_user:
            return

        notification_msg = handler_kwargs.get("notification_msg")

        await log_newusr(client, message.from_user.id, message.from_user.first_name or "")
        try:
            status_msg = await reply_safe(message, MSG_PROCESSING_FILE)
        except Exception as e:
            logger.error(f"Could not send processing status: {e}", exc_info=True)
            return
        await process_single(
            client, message, message, status_msg, shortener_val, notification_msg=notification_msg
        )

    await handle_rate_limited_request(bot, msg, _actual_private_receive_handler, **kwargs)


@StreamBot.on_message(
    filters.channel
    & filters.incoming
    & (filters.document | filters.video | filters.audio)
    & ~filters.chat(Var.BIN_CHANNEL),
    group=-1,
)
async def channel_receive_handler(bot: Client, msg: Message):
    async def _actual_channel_receive_handler(client: Client, message: Message, **handler_kwargs):
        if not Var.CHANNEL:
            return
        # M12: PRIVATE_MODE promises "owner + authorized users only" -- channels
        # have no from_user for the gates, so fail closed: no public links.
        if Var.PRIVATE_MODE:
            logger.debug(f"Ignoring channel post from {message.chat.id} (PRIVATE_MODE).")
            return
        notification_msg = handler_kwargs.get("notification_msg")

        is_banned_statically = (
            hasattr(Var, "BANNED_CHANNELS") and message.chat.id in Var.BANNED_CHANNELS
        )
        # Flag-cached (one DB hit per channel per TTL).  Fail-open is DELIBERATE:
        # a hit triggers leave_chat (irreversible) -- a Mongo outage must not
        # mass-leave served channels (H7 fail-closed applies to user-ban gates).
        is_banned_dynamically = (
            await flags.get_or_load(
                ("banned_channel", message.chat.id),
                lambda: db.is_channel_banned(message.chat.id),
            )
            is not None
        )

        if is_banned_statically or is_banned_dynamically:
            try:
                await tg_call(client.leave_chat, message.chat.id, retries=1)
            except Exception as e:
                logger.error(f"Error leaving banned channel {message.chat.id}: {e}")
            return
        if not await is_admin(client, message.chat.id):
            logger.debug(
                f"Bot is not admin in channel {message.chat.id} "
                f"({message.chat.title or 'Unknown'}). Ignoring message."
            )
            return

        try:
            shortener_val = await _shortener_status_for(client, message)
            canonical_record, stored_msg, reused_existing = await get_or_create_canonical_file(
                message, fwd_media, client
            )
            if reused_existing and stored_msg:
                await safe_delete_message(stored_msg)
                stored_msg = None
            if canonical_record:
                links = await gen_canonical_links(
                    file_name=canonical_record["file_name"],
                    file_size=int(canonical_record.get("file_size", 0) or 0),
                    public_hash=canonical_record["public_hash"],
                    shortener=shortener_val,
                )
                reply_to_message_id = int(canonical_record["canonical_message_id"])
            else:
                if not stored_msg:
                    stored_msg = await fwd_media(message)
                    if not stored_msg:
                        logger.error(
                            f"Failed to forward media from channel {message.chat.id}. Ignoring."
                        )
                        return
                links = await gen_links(stored_msg, shortener=shortener_val)
                reply_to_message_id = stored_msg.id
            source_info = message.chat.title or "Unknown Channel"
            # stored_msg is intentionally None after reusing a canonical BIN copy:
            # send_channel_links then threads the log to the canonical message via reply_to_message_id.

            if notification_msg:
                try:
                    await edit_safe(
                        notification_msg,
                        MSG_NEW_FILE_REQUEST.format(
                            source_info=html.escape(source_info),
                            id_=message.chat.id,
                            online_link=links["online_link"],
                            stream_link=links["stream_link"],
                        ),
                        disable_web_page_preview=True,
                        parse_mode=enums.ParseMode.HTML,  # M7 template is HTML
                    )
                except Exception as e:
                    logger.error(
                        f"Error editing notification message with links: {e}", exc_info=True
                    )
                    await send_channel_links(
                        links,
                        source_info,
                        message.chat.id,
                        target_msg=stored_msg,
                        reply_to_message_id=reply_to_message_id,
                    )
            else:
                await send_channel_links(
                    links,
                    source_info,
                    message.chat.id,
                    target_msg=stored_msg,
                    reply_to_message_id=reply_to_message_id,
                )

            try:
                await tg_call(message.edit_reply_markup, reply_markup=get_link_buttons(links))
            except (MessageNotModified, MessageDeleteForbidden, MessageIdInvalid):
                logger.debug(
                    f"Failed to edit reply markup for message {message.id} due to not modified, permissions or invalid ID. Sending new link instead."
                )
                await send_link(message, links)
            except Exception as e:
                logger.error(
                    f"Error editing reply markup for message {message.id}: {e}", exc_info=True
                )
                await send_link(message, links)
        except Exception as e:
            logger.error(
                f"Error in _actual_channel_receive_handler for message {message.id}: {e}",
                exc_info=True,
            )

    rl_user_id = None
    if msg.sender_chat and msg.sender_chat.id:
        rl_user_id = msg.sender_chat.id
    elif msg.from_user:
        rl_user_id = msg.from_user.id

    if rl_user_id is None:
        logger.debug(
            f"No identifiable user/channel for rate limiting for message {msg.id}. Skipping rate limit check and processing directly."
        )
        await _actual_channel_receive_handler(bot, msg)
        return

    await handle_rate_limited_request(
        bot, msg, _actual_channel_receive_handler, rl_user_id=rl_user_id
    )


async def _shortener_status_for(client: Client, message: Message) -> bool:
    """Channel messages skip the user gates but still honor shortener config."""
    from Thunder.utils.decorators import get_shortener_status

    return await get_shortener_status(client, message)


async def process_single(
    bot: Client,
    msg: Message,
    file_msg: Message,
    status_msg: Message | None,
    shortener_val: bool,
    original_request_msg: Message | None = None,
    notification_msg: Message | None = None,
):
    try:
        canonical_record, stored_msg, reused_existing = await get_or_create_canonical_file(
            file_msg, fwd_media, bot
        )
        if reused_existing and stored_msg:
            await safe_delete_message(stored_msg)
            stored_msg = None
        if canonical_record:
            links = await gen_canonical_links(
                file_name=canonical_record["file_name"],
                file_size=int(canonical_record.get("file_size", 0) or 0),
                public_hash=canonical_record["public_hash"],
                shortener=shortener_val,
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
            result = await safe_edit_message(
                notification_msg,
                format_link_message(links),
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=get_link_buttons(links),
            )
            if not result:
                await send_link(msg, links)
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
                source_info = (
                    f"@{source_msg.from_user.username}"
                    if source_msg.from_user.username
                    else "Unknown User"
                )
            source_id = source_msg.from_user.id
        elif source_msg.chat.type == enums.ChatType.CHANNEL:
            source_info = source_msg.chat.title or "Unknown Channel"
            source_id = source_msg.chat.id
        if source_info and source_id:
            await send_channel_links(
                links,
                source_info,
                source_id,
                target_msg=stored_msg,
                reply_to_message_id=canonical_reply_id,
            )
        if status_msg:
            await safe_delete_message(status_msg)
        return links
    except Exception as e:
        logger.error(f"Error processing single file for message {file_msg.id}: {e}", exc_info=True)
        if status_msg:
            await safe_edit_message(status_msg, MSG_ERROR_PROCESSING_MEDIA)

        await notify_own(
            bot, MSG_CRITICAL_ERROR.format(error=str(e), error_id=secrets.token_hex(6))
        )
        return None


async def process_batch(
    bot: Client,
    msg: Message,
    start_id: int,
    count: int,
    status_msg: Message,
    shortener_val: bool,
    notification_msg: Message | None = None,
):
    """M4b: worker-pooled batch with order preservation.

    * messages are pre-fetched in chunks of ``BATCH_SIZE`` (same API usage
      as before); processing then runs on ``BATCH_WORKERS`` workers;
    * results are collected into an index-keyed dict so link order is
      preserved regardless of completion order;
    * non-media messages count as **skipped** (ThunderGo semantics), not
      failed;
    * the whole batch runs under a ``30 + 2n`` second deadline;
    * progress edits are throttled to every 5 completions.
    """
    total_started = time.monotonic()
    deadline = total_started + _BATCH_DEADLINE_BASE + 2 * count
    worker_count = max(1, int(Var.BATCH_WORKERS))

    ids: list[int] = list(range(start_id, start_id + count))
    results: dict[int, dict[str, Any] | None] = {}
    skipped = 0
    counters = {"done": 0, "failed": 0}

    # ---- pre-fetch phase (chunked) ----
    fetched: dict[int, Message | None] = {}
    fetch_failed: set[int] = set()
    for chunk_start in range(0, count, BATCH_SIZE):
        if time.monotonic() > deadline:
            break
        chunk_ids = ids[chunk_start : chunk_start + BATCH_SIZE]
        try:
            fetched_msgs = await tg_call(bot.get_messages, msg.chat.id, chunk_ids, retries=1)
            if fetched_msgs is None:
                messages = []
            elif isinstance(fetched_msgs, Message):  # single id -> single message
                messages = [fetched_msgs]
            else:
                messages = list(fetched_msgs)
        except Exception as e:
            # a failed chunk counts as FAILED, not skipped -- skipping would
            # hide whole-chunk outages from the summary
            logger.error(f"Error getting messages in batch: {e}", exc_info=True)
            fetch_failed.update(chunk_ids)
            messages = []
        for mid, m in zip(chunk_ids, messages, strict=False):
            fetched[mid] = m if (m is not None and getattr(m, "media", None)) else None

    queue: asyncio.Queue[int | None] = asyncio.Queue()
    for mid in ids:
        queue.put_nowait(mid)
    for _ in range(worker_count):
        queue.put_nowait(None)

    async def progress_edit():
        try:
            await edit_safe(
                status_msg,
                MSG_PROCESSING_STATUS.format(
                    processed=counters["done"] - counters["failed"],
                    total=count,
                    failed=counters["failed"],
                ),
            )
        except MessageNotModified:
            pass
        except Exception:
            pass

    async def worker():
        nonlocal skipped
        while True:
            mid = await queue.get()
            if mid is None:
                return
            if time.monotonic() > deadline:
                results[mid] = None
                skipped += 1
                counters["done"] += 1
                continue
            m = fetched.get(mid)
            if mid in fetch_failed:
                results[mid] = None
                counters["failed"] += 1
            elif m is not None:
                links = await process_single(
                    bot, msg, m, None, shortener_val, original_request_msg=msg
                )
                results[mid] = links
                if not links:
                    counters["failed"] += 1
            else:
                results[mid] = None
                skipped += 1
            counters["done"] += 1
            if counters["done"] % BATCH_UPDATE_INTERVAL == 0 and counters["done"] < count:
                await progress_edit()

    # initial status (guarded: a deleted/undeletable status message must
    # not abort the batch before it starts)
    try:
        await edit_safe(
            status_msg,
            MSG_PROCESSING_BATCH.format(
                batch_number=1,
                total_batches=(count + BATCH_SIZE - 1) // BATCH_SIZE,
                file_count=count,
            ),
        )
    except Exception as e:
        logger.debug(f"Could not update batch status message: {e}")

    workers = [asyncio.create_task(worker(), name=f"batch_worker_{i}") for i in range(worker_count)]
    await asyncio.gather(*workers)

    failed = counters["failed"]
    processed = sum(1 for r in results.values() if r)

    links_list = [rec["online_link"] for mid in ids if (rec := results.get(mid))]
    for i in range(0, len(links_list), LINK_CHUNK_SIZE):
        chunk = links_list[i : i + LINK_CHUNK_SIZE]
        chunk_text = (
            MSG_BATCH_LINKS_READY.format(count=len(chunk))
            + f"\n\n<code>{chr(10).join(chunk)}</code>"
        )
        try:
            await reply_safe(
                msg, chunk_text, disable_web_page_preview=True, parse_mode=enums.ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Error sending batch chunk: {e}", exc_info=True)
        if msg.chat.type != enums.ChatType.PRIVATE and msg.from_user:
            try:
                await send_safe(
                    bot,
                    msg.from_user.id,
                    text=MSG_DM_BATCH_PREFIX.format(
                        chat_title=html.escape(msg.chat.title or "the chat")
                    )
                    + "\n"
                    + chunk_text,
                    disable_web_page_preview=True,
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception as e:
                logger.error(f"Error sending DM in batch: {e}", exc_info=True)
                await reply_user_err(msg, MSG_ERROR_DM_FAILED)
        if i + LINK_CHUNK_SIZE < len(links_list):
            await asyncio.sleep(MESSAGE_DELAY)

    try:
        await edit_safe(
            status_msg,
            MSG_PROCESSING_RESULT.format(
                processed=processed,
                total=count,
                failed=failed,
            ),
        )
    except MessageNotModified:
        pass
    except Exception as e:
        logger.debug(f"Could not finalize batch status: {e}")
    if notification_msg:
        await safe_delete_message(notification_msg)
