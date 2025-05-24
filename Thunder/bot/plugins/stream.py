"""
Thunder/bot/plugins/stream.py - Streaming and file link plugin handlers for Thunder bot.
"""

import time
import asyncio
import random
import uuid
from urllib.parse import quote
from typing import Optional, Tuple, Dict, Union, List, Set
from datetime import datetime, timedelta

from pyrogram.client import Client
from pyrogram import filters, enums
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
    CallbackQuery,
    LinkPreviewOptions
)

from pyrogram.enums import ChatMemberStatus
from Thunder.bot import StreamBot
from Thunder.utils.database import db
from Thunder.utils.messages import *
from Thunder.utils.file_properties import get_hash, get_media_file_size, get_name
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.logger import logger
from Thunder.vars import Var
from Thunder.utils.decorators import check_banned, require_token, shorten_link
from Thunder.utils.force_channel import force_channel_check
from Thunder.utils.shortener import shorten
from Thunder.utils.bot_utils import (
    notify_owner,
    handle_user_error,
    log_new_user,
    generate_media_links,
    send_links_to_user,
    check_admin_privileges
)


class LRUCache:
    def __init__(self, max_size=1000, ttl=86400):
        self.cache = {}
        self.max_size = max_size
        self.ttl = ttl
        self.access_order = []
        self._lock = asyncio.Lock()

    async def get(self, key):
        if key is None:
            return None
        
        async with self._lock:
            if key not in self.cache:
                return None
            
            item = self.cache[key]
            if time.time() - item['timestamp'] > self.ttl:
                self._remove_item(key)
                return None
            
            self._update_access_order(key)
            return item

    def _update_access_order(self, key):
        try:
            self.access_order.remove(key)
        except ValueError:
            pass
        self.access_order.append(key)

    def _remove_item(self, key):
        try:
            del self.cache[key]
            self.access_order.remove(key)
        except (KeyError, ValueError):
            pass

    async def set(self, key, value):
        if key is None or not isinstance(value, dict):
            return
        
        async with self._lock:
            if key in self.cache:
                self._update_access_order(key)
            elif len(self.cache) >= self.max_size:
                lru_key = self.access_order[0]
                self._remove_item(lru_key)
            
            self.cache[key] = value
            self._update_access_order(key)

    async def clean_expired(self):
        count = 0
        current_time = time.time()
        
        async with self._lock:
            keys_to_remove = []
            for key in self.cache:
                if current_time - self.cache[key]['timestamp'] > self.ttl:
                    keys_to_remove.append(key)
            
            for key in keys_to_remove:
                self._remove_item(key)
                count += 1
        
        return count


# Initialize cache
CACHE = LRUCache(max_size=getattr(Var, "CACHE_SIZE", 1000), ttl=86400)


async def clean_cache_task():
    while True:
        try:
            cleaned_count = await CACHE.clean_expired()
            if cleaned_count > 0:
                logger.info(f"Cache cleanup: Removed {cleaned_count} expired items")
            await asyncio.sleep(3600)
        except Exception as e:
            logger.error(f"Error in cache cleaning task: {e}")
            await asyncio.sleep(60)


class RateLimiter:
    def __init__(self, max_calls, time_period):
        self.max_calls = max_calls
        self.time_period = time_period
        self.calls = {}
        self._lock = asyncio.Lock()
        self._cleanup_task = None
        self._stopping = False

    def start_cleanup_task(self):
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
            self._cleanup_task.add_done_callback(self._cleanup_task_done)

    def stop_cleanup_task(self):
        self._stopping = True
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()

    def _cleanup_task_done(self, task):
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in rate limiter cleanup task: {e}")
            if not self._stopping:
                asyncio.create_task(self._delayed_restart())

    async def _delayed_restart(self):
        await asyncio.sleep(5)
        if not self._stopping:
            self.start_cleanup_task()

    async def _periodic_cleanup(self):
        while not self._stopping:
            try:
                await asyncio.sleep(60)
                await self._cleanup_old_entries()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in periodic cleanup: {e}")
                await asyncio.sleep(30)

    async def _cleanup_old_entries(self):
        now = time.time()
        async with self._lock:
            for user_id in list(self.calls.keys()):
                self.calls[user_id] = [ts for ts in self.calls[user_id]
                                     if now - ts <= self.time_period]
                if not self.calls[user_id]:
                    del self.calls[user_id]

    async def is_rate_limited(self, user_id):
        if user_id is None:
            return False
        
        async with self._lock:
            now = time.time()
            if user_id not in self.calls:
                self.calls[user_id] = []
            
            self.calls[user_id] = [
                ts for ts in self.calls[user_id]
                if now - ts <= self.time_period
            ]
            
            if len(self.calls[user_id]) >= self.max_calls:
                return True
            
            self.calls[user_id].append(now)
            return False

    async def get_reset_time(self, user_id):
        if user_id is None:
            return 0
        
        async with self._lock:
            if user_id not in self.calls or not self.calls[user_id]:
                return 0
            
            now = time.time()
            oldest_call = min(self.calls[user_id])
            return max(0, self.time_period - (now - oldest_call))


# Initialize rate limiter
rate_limiter = RateLimiter(max_calls=20, time_period=60)


# Helper Functions
async def handle_flood_wait(e):
    wait_time = e.value
    logger.debug(f"FloodWait encountered. Sleeping for {wait_time} seconds.")
    jitter = random.uniform(0, 0.05 * wait_time)
    await asyncio.sleep(wait_time + jitter)


def get_file_unique_id(media_message):
    media_types = [
        'document', 'video', 'audio', 'photo', 'animation',
        'voice', 'video_note', 'sticker'
    ]
    
    for media_type in media_types:
        media = getattr(media_message, media_type, None)
        if media:
            return media.file_unique_id
    
    return None


async def forward_media(media_message):
    for retry in range(3):
        try:
            result = await media_message.copy(chat_id=Var.BIN_CHANNEL)
            await asyncio.sleep(0.1)
            return result
        except Exception:
            try:
                result = await media_message.forward(chat_id=Var.BIN_CHANNEL)
                await asyncio.sleep(0.1)
                return result
            except FloodWait as flood_error:
                if retry < 2:
                    await handle_flood_wait(flood_error)
                else:
                    raise
            except Exception as forward_error:
                if retry == 2:
                    logger.error(f"Error forwarding media: {forward_error}")
                    raise
                await asyncio.sleep(0.5)
    
    raise Exception("Failed to forward media after multiple attempts")


async def log_request(log_msg, user, stream_link, online_link):
    try:
        user_name = user.first_name if hasattr(user, 'first_name') else user.title
        await log_msg.reply_text(
            MSG_NEW_FILE_REQUEST.format(
                user_name=user_name,
                user_id=user.id,
                online_link=online_link,
                stream_link=stream_link
            ),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            quote=True
        )
        await asyncio.sleep(0.3)
    except Exception:
        pass


async def process_media_message(client, command_message, media_message, notify=True, shortener=True):
    retries = 0
    max_retries = 3
    cache_key = get_file_unique_id(media_message)
    
    if cache_key is None:
        if notify:
            await command_message.reply_text(MSG_ERROR_FILE_ID_EXTRACT, quote=True)
        return None

    cached_data = await CACHE.get(cache_key)
    if cached_data:
        if notify:
            await send_links_to_user(
                client,
                command_message,
                cached_data['media_name'],
                cached_data['media_size'],
                cached_data['stream_link'],
                cached_data['online_link']
            )
        return cached_data['online_link']

    while retries < max_retries:
        try:
            log_msg = await forward_media(media_message)
            stream_link, online_link, media_name, media_size = await generate_media_links(log_msg, shortener=shortener)
            
            await CACHE.set(cache_key, {
                'media_name': media_name,
                'media_size': media_size,
                'stream_link': stream_link,
                'online_link': online_link,
                'message_id': log_msg.id,
                'timestamp': time.time()
            })
            
            if notify:
                await send_links_to_user(
                    client,
                    command_message,
                    media_name,
                    media_size,
                    stream_link,
                    online_link
                )
            
            await log_request(log_msg, command_message.from_user, stream_link, online_link)
            return online_link
            
        except FloodWait as e:
            await handle_flood_wait(e)
            retries += 1
            continue
        except (FileReferenceExpired, FileReferenceInvalid):
            retries += 1
            await asyncio.sleep(0.3)
            continue
        except MediaEmpty:
            if notify:
                await command_message.reply_text(MSG_MEDIA_ERROR, quote=True)
            return None
        except Exception as e:
            logger.error(f"Error processing media: {e}")
            if retries < max_retries - 1:
                retries += 1
                await asyncio.sleep(0.3)
                continue
            
            if notify:
                await handle_user_error(
                    command_message,
                    MSG_ERROR_PROCESSING_MEDIA
                )
            
            await notify_owner(
                client,
                MSG_CRITICAL_ERROR.format(error=e, error_id=uuid.uuid4().hex[:8])
            )
            return None
    
    return None


async def retry_failed_media(client, command_message, media_messages, status_msg=None, shortener=True):
    results = []
    for i, msg in enumerate(media_messages):
        try:
            result = await process_media_message(client, command_message, msg, notify=False, shortener=shortener)
            if result:
                results.append(result)
            
            if status_msg and i % 2 == 0:
                try:
                    await status_msg.edit(f"🔄 **Retrying Failed Files:** {len(results)}/{len(media_messages)} processed")
                except MessageNotModified:
                    pass
        except Exception as e:
            logger.error(f"Error retrying media: {e}")
    
    return results


async def process_multiple_messages(client, command_message, reply_msg, num_files, status_msg, shortener=True):
    chat_id = command_message.chat.id
    start_message_id = reply_msg.id
    end_message_id = start_message_id + num_files - 1
    message_ids = list(range(start_message_id, end_message_id + 1))
    
    batch_size = 10
    processed_count = 0
    failed_count = 0
    download_links = []
    failed_messages = []
    last_status_text = ""

    try:
        for i in range(0, len(message_ids), batch_size):
            batch_ids = message_ids[i:i+batch_size]
            new_status_text = MSG_PROCESSING_BATCH.format(
                batch_number=(i//batch_size)+1,
                total_batches=(len(message_ids)+batch_size-1)//batch_size,
                file_count=len(batch_ids)
            )
            
            if new_status_text != last_status_text:
                try:
                    await status_msg.edit(new_status_text)
                    last_status_text = new_status_text
                except MessageNotModified:
                    pass
            
            await asyncio.sleep(1.0)
            
            messages = []
            for retry in range(3):
                try:
                    messages = await client.get_messages(
                        chat_id=chat_id,
                        message_ids=batch_ids
                    )
                    break
                except FloodWait as e:
                    await handle_flood_wait(e)
                except Exception as e:
                    if retry == 2:
                        logger.error(f"Failed to get batch {i//batch_size+1}: {e}")
                    await asyncio.sleep(0.5)
            
            for msg in messages:
                if msg and msg.media:
                    try:
                        result = await process_media_message(
                            client, command_message, msg, notify=False, shortener=shortener
                        )
                        
                        if result:
                            download_links.append(result)
                            processed_count += 1
                        else:
                            failed_count += 1
                            failed_messages.append(msg)
                    except Exception as e:
                        failed_count += 1
                        failed_messages.append(msg)
                        logger.error(f"Failed to process {msg.id}: {e}")
                
                await asyncio.sleep(1.0)
            
            if processed_count % 5 == 0 or processed_count + failed_count == len(message_ids):
                new_status_text = MSG_PROCESSING_STATUS.format(
                    processed=processed_count,
                    total=num_files,
                    failed=failed_count
                )
                
                if new_status_text != last_status_text:
                    try:
                        await status_msg.edit(new_status_text)
                        last_status_text = new_status_text
                    except MessageNotModified:
                        pass

        # Handle retries for failed messages
        if failed_messages and len(failed_messages) < num_files / 2:
            new_status_text = MSG_RETRYING_FILES.format(count=len(failed_messages))
            if new_status_text != last_status_text:
                try:
                    await status_msg.edit(new_status_text)
                    last_status_text = new_status_text
                except MessageNotModified:
                    pass
            
            retry_results = await retry_failed_media(
                client, command_message, failed_messages, status_msg, shortener
            )
            
            if retry_results:
                download_links.extend(retry_results)
                processed_count += len(retry_results)
                failed_count -= len(retry_results)

        # Send batch links to user
        def chunk_list(lst, n):
            for i in range(0, len(lst), n):
                yield lst[i:i + n]

        for chunk in chunk_list(download_links, 20):
            links_text = "\n".join(chunk)
            
            # Message for the group chat
            group_message_content = MSG_BATCH_LINKS_READY.format(count=len(chunk)) + f"\n\n`{links_text}`"
            
            # Message for the user's DM
            dm_prefix = MSG_DM_BATCH_PREFIX.format(chat_title=command_message.chat.title)
            dm_message_text = f"{dm_prefix}\n{group_message_content}"

            # Send to original chat (group/supergroup)
            try:
                await command_message.reply_text(
                    group_message_content,
                    quote=True,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                    parse_mode=enums.ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.error(f"Error sending batch links to original chat {command_message.chat.id}: {e}")

            # Send to user's DM (if in a group/supergroup)
            if command_message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
                try:
                    await client.send_message(
                        chat_id=command_message.from_user.id,
                        text=dm_message_text,
                        link_preview_options=LinkPreviewOptions(is_disabled=True),
                        parse_mode=enums.ParseMode.MARKDOWN
                    )
                except Exception as e:
                    logger.error(f"Error sending batch links to user DM {command_message.from_user.id}: {e}")
                    await command_message.reply_text(
                        MSG_ERROR_DM_FAILED,
                        quote=True
                    )
            
            if len(chunk) > 10:
                await asyncio.sleep(0.5)

        # Update final status
        new_status_text = MSG_PROCESSING_RESULT.format(
            processed=processed_count,
            total=num_files,
            failed=failed_count
        )
        
        if new_status_text != last_status_text:
            try:
                await status_msg.edit(new_status_text)
                last_status_text = new_status_text
            except MessageNotModified:
                pass

    except Exception as e:
        logger.error(f"Error in batch processing: {e}")
        new_status_text = MSG_PROCESSING_ERROR.format(
            error=str(e),
            processed=processed_count,
            total=num_files,
            error_id=uuid.uuid4().hex[:8]
        )
        
        if new_status_text != last_status_text:
            try:
                await status_msg.edit(new_status_text)
                last_status_text = new_status_text
            except MessageNotModified:
                pass


@StreamBot.on_message(filters.command("link") & ~filters.private)
@check_banned
@require_token
@shorten_link
@force_channel_check
async def link_handler(client, message, shortener=True):
    user_id = message.from_user.id
    
    if not await db.is_user_exist(user_id):
        try:
            invite_link = f"https://t.me/{client.me.username}?start=start"
            await message.reply_text(
                MSG_ERROR_START_BOT.format(invite_link=invite_link),
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                parse_mode=enums.ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(MSG_BUTTON_START_CHAT, url=invite_link)]]),
                quote=True
            )
        except Exception:
            pass
        return

    if await rate_limiter.is_rate_limited(user_id):
        reset_time = await rate_limiter.get_reset_time(user_id)
        await message.reply_text(
            MSG_ERROR_RATE_LIMIT.format(seconds=f"{reset_time:.0f}"),
            quote=True
        )
        return

    if message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        is_admin = await check_admin_privileges(client, message.chat.id)
        if not is_admin:
            await message.reply_text(
                MSG_ERROR_NOT_ADMIN,
                quote=True
            )
            return

    if not message.reply_to_message:
        await message.reply_text(
            MSG_ERROR_REPLY_FILE,
            quote=True
        )
        return

    reply_msg = message.reply_to_message
    if not reply_msg.media:
        await message.reply_text(
            MSG_ERROR_NO_FILE,
            quote=True
        )
        return

    command_parts = message.text.strip().split()
    num_files = 1
    
    if len(command_parts) > 1:
        try:
            num_files = int(command_parts[1])
            if num_files < 1 or num_files > 100:
                await message.reply_text(
                    MSG_ERROR_NUMBER_RANGE,
                    quote=True
                )
                return
        except ValueError:
            await message.reply_text(
                MSG_ERROR_INVALID_NUMBER,
                quote=True
            )
            return

    processing_msg = await message.reply_text(
        MSG_PROCESSING_REQUEST,
        quote=True
    )

    try:
        if num_files == 1:
            result = await process_media_message(client, message, reply_msg, shortener=shortener)
            if result:
                if message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
                    try:
                        cached_data = await CACHE.get(get_file_unique_id(reply_msg))
                        if cached_data:
                            msg_text = (
                                MSG_LINKS.format(
                                    file_name=cached_data['media_name'],
                                    file_size=cached_data['media_size'],
                                    download_link=cached_data['online_link'],
                                    stream_link=cached_data['stream_link']
                                )
                            )
                            await client.send_message(
                                chat_id=message.from_user.id,
                                text=MSG_LINK_FROM_GROUP.format(
                                    chat_title=message.chat.title,
                                    links_message=msg_text
                                ),
                                link_preview_options=LinkPreviewOptions(is_disabled=True),
                                parse_mode=enums.ParseMode.MARKDOWN,
                                reply_markup=InlineKeyboardMarkup([
                                    [
                                        InlineKeyboardButton(MSG_BUTTON_STREAM_NOW, url=cached_data['stream_link']),
                                        InlineKeyboardButton(MSG_BUTTON_DOWNLOAD, url=cached_data['online_link'])
                                    ]
                                ]),
                            )
                    except Exception as e:
                        logger.debug(f"Error sending DM to user {message.from_user.id} from group: {e}")
                        await message.reply_text(
                            MSG_ERROR_DM_FAILED,
                            quote=True
                        )
                
                await processing_msg.delete()
            else:
                try:
                    await processing_msg.edit(MSG_ERROR_PROCESSING_MEDIA)
                except MessageNotModified:
                    pass
        else:
            await process_multiple_messages(client, message, reply_msg, num_files, processing_msg, shortener)

    except Exception as e:
        logger.error(f"Error handling link command: {e}")
        try:
            await processing_msg.edit(MSG_ERROR_PROCESSING_MEDIA)
        except MessageNotModified:
            pass


@StreamBot.on_message(
    filters.private & filters.incoming & (
        filters.document | filters.video | filters.photo | filters.audio |
        filters.voice | filters.animation | filters.video_note
    ),
    group=4
)
@check_banned
@require_token
@shorten_link
@force_channel_check
async def private_receive_handler(client, message, shortener=True):
    if not message.from_user:
        return

    if await rate_limiter.is_rate_limited(message.from_user.id):
        reset_time = await rate_limiter.get_reset_time(message.from_user.id)
        await message.reply_text(
            MSG_ERROR_RATE_LIMIT.format(seconds=f"{reset_time:.0f}"),
            quote=True
        )
        return

    await log_new_user(
        bot=client,
        user_id=message.from_user.id,
        first_name=message.from_user.first_name
    )

    processing_msg = await message.reply_text(
        MSG_PROCESSING_FILE,
        quote=True
    )

    try:
        result = await process_media_message(client, message, message, shortener=shortener)
        if result:
            await processing_msg.delete()
        else:
            try:
                await processing_msg.edit(MSG_ERROR_PROCESSING_MEDIA)
            except MessageNotModified:
                pass
    except Exception as e:
        logger.error(f"Error in private handler: {e}")
        try:
            await processing_msg.edit(MSG_ERROR_PROCESSING_MEDIA)
        except MessageNotModified:
            pass


@StreamBot.on_message(
    filters.channel & filters.incoming & (
        filters.document | filters.video | filters.photo | filters.audio |
        filters.voice | filters.animation | filters.video_note
    ),
    group=-1
)
@shorten_link
async def channel_receive_handler(client, broadcast, shortener=True):
    if hasattr(Var, 'BANNED_CHANNELS') and int(broadcast.chat.id) in Var.BANNED_CHANNELS:
        await client.leave_chat(broadcast.chat.id)
        return

    can_edit = False
    try:
        member = await client.get_chat_member(broadcast.chat.id, client.me.id)
        can_edit = member.status in [
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.OWNER
        ]
    except Exception:
        can_edit = False

    retries = 0
    max_retries = 3
    
    while retries < max_retries:
        try:
            log_msg = await forward_media(broadcast)
            stream_link, online_link, media_name, media_size = await generate_media_links(log_msg, shortener=shortener)
            
            await log_request(log_msg, broadcast.chat, stream_link, online_link)
            
            if can_edit:
                try:
                    await client.edit_message_reply_markup(
                        chat_id=broadcast.chat.id,
                        message_id=broadcast.id,
                        reply_markup=InlineKeyboardMarkup([
                            [
                                InlineKeyboardButton(MSG_BUTTON_STREAM_NOW, url=stream_link),
                                InlineKeyboardButton(MSG_BUTTON_DOWNLOAD, url=online_link)
                            ]
                        ])
                    )
                except Exception:
                    pass
            
            break
            
        except FloodWait as e:
            await handle_flood_wait(e)
            retries += 1
            continue
        except (FileReferenceExpired, FileReferenceInvalid):
            retries += 1
            await asyncio.sleep(0.5)
            continue
        except Exception as e:
            logger.error(f"Error handling channel message: {e}")
            if retries < max_retries - 1:
                retries += 1
                await asyncio.sleep(0.5)
                continue
            
            await notify_owner(
                client,
                MSG_CRITICAL_ERROR.format(error=e, error_id=uuid.uuid4().hex[:8])
            )
            break

# Start background tasks
StreamBot.loop.create_task(clean_cache_task())
rate_limiter.start_cleanup_task()
