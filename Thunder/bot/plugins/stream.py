# Thunder/bot/plugins/stream.py

import time
import asyncio
import random
from urllib.parse import quote
from typing import Optional, Tuple, Dict, Union, List, Set
from datetime import datetime, timedelta

from pyrogram import Client, filters, enums
from pyrogram.errors import (
    FloodWait, 
    RPCError, 
    MediaEmpty, 
    FileReferenceExpired,
    FileReferenceInvalid
)
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
    Chat
)

from Thunder.bot import StreamBot
from Thunder.utils.database import Database
from Thunder.utils.file_properties import get_hash, get_media_file_size, get_name
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.logger import logger
from Thunder.vars import Var

# Database Initialization
db = Database(Var.DATABASE_URL, Var.NAME)

# Cache Implementation
class LRUCache:
    def __init__(self, max_size=1000, ttl=86400):
        self.cache = {}
        self.max_size = max_size
        self.ttl = ttl
        self.access_order = []
        self._lock = asyncio.Lock()
    
    async def get(self, key):
        async with self._lock:
            if key not in self.cache:
                return None
            
            # Check if expired
            item = self.cache[key]
            if time.time() - item['timestamp'] > self.ttl:
                del self.cache[key]
                self.access_order.remove(key)
                return None
            
            # Update access order
            self.access_order.remove(key)
            self.access_order.append(key)
            
            return item
    
    async def set(self, key, value):
        async with self._lock:
            if key in self.cache:
                # Already exists, update access order
                self.access_order.remove(key)
            elif len(self.cache) >= self.max_size:
                # Remove least recently used item
                lru_key = self.access_order.pop(0)
                del self.cache[lru_key]
            
            # Add to cache and update access order
            self.cache[key] = value
            self.access_order.append(key)
    
    async def delete(self, key):
        async with self._lock:
            if key in self.cache:
                del self.cache[key]
                self.access_order.remove(key)
    
    async def clean_expired(self):
        count = 0
        current_time = time.time()
        async with self._lock:
            for key in list(self.cache.keys()):
                if current_time - self.cache[key]['timestamp'] > self.ttl:
                    del self.cache[key]
                    self.access_order.remove(key)
                    count += 1
        return count

# Initialize cache
CACHE = LRUCache(max_size=getattr(Var, "CACHE_SIZE", 1000), ttl=86400)

# Rate Limiter Implementation
class RateLimiter:
    def __init__(self, max_calls, time_period):
        self.max_calls = max_calls
        self.time_period = time_period
        self.calls = {}
        self._lock = asyncio.Lock()
    
    async def is_rate_limited(self, user_id):
        async with self._lock:
            now = time.time()
            if user_id not in self.calls:
                self.calls[user_id] = []
            
            # Remove old timestamps
            self.calls[user_id] = [
                ts for ts in self.calls[user_id] 
                if now - ts <= self.time_period
            ]
            
            # Check if rate limited
            if len(self.calls[user_id]) >= self.max_calls:
                return True
            
            # Add timestamp
            self.calls[user_id].append(now)
            return False
    
    async def get_reset_time(self, user_id):
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
    logger.warning(f"FloodWait encountered. Sleeping for {wait_time} seconds.")
    
    # Add small jitter to prevent thundering herd
    jitter = random.uniform(0, 0.1 * wait_time)
    await asyncio.sleep(wait_time + jitter + 1)

async def notify_owner(client, text):
    try:
        owner_ids = Var.OWNER_ID
        if isinstance(owner_ids, (list, tuple, set)):
            tasks = [
                client.send_message(chat_id=owner_id, text=text)
                for owner_id in owner_ids
            ]
            await asyncio.gather(*tasks)
        else:
            await client.send_message(chat_id=owner_ids, text=text)
        
        if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
            await client.send_message(chat_id=Var.BIN_CHANNEL, text=text)
    except Exception as e:
        logger.error(f"Failed to send message to owner: {e}")

async def handle_user_error(message, error_msg):
    try:
        await message.reply_text(
            f"❌ {error_msg}\nPlease try again or contact support.",
            quote=True
        )
    except Exception:
        pass

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
            return await media_message.copy(chat_id=Var.BIN_CHANNEL)
        except Exception:
            try:
                return await media_message.forward(chat_id=Var.BIN_CHANNEL)
            except FloodWait as flood_error:
                if retry < 2:
                    await handle_flood_wait(flood_error)
                else:
                    raise
            except Exception as forward_error:
                if retry == 2:
                    logger.error(f"Error forwarding media: {forward_error}")
                    raise
        
        await asyncio.sleep(1)
    
    raise Exception("Failed to forward media after multiple attempts")

async def generate_media_links(log_msg):
    try:
        base_url = Var.URL.rstrip("/")
        file_id = log_msg.id
        
        media_name = get_name(log_msg)
        if isinstance(media_name, bytes):
            media_name = media_name.decode('utf-8', errors='replace')
        else:
            media_name = str(media_name)
        
        media_size = humanbytes(get_media_file_size(log_msg))
        file_name_encoded = quote(media_name)
        hash_value = get_hash(log_msg)
        
        stream_link = f"{base_url}/watch/{hash_value}{file_id}/{file_name_encoded}"
        online_link = f"{base_url}/{hash_value}{file_id}/{file_name_encoded}"
        
        return stream_link, online_link, media_name, media_size
    except Exception as e:
        logger.error(f"Error generating media links: {e}")
        raise

async def send_links_to_user(client, command_message, media_name, media_size, stream_link, online_link):
    msg_text = (
        "🔗 **Your Links are Ready!**\n\n"
        f"📄 **File Name:** `{media_name}`\n"
        f"📂 **File Size:** `{media_size}`\n\n"
        f"📥 **Download Link:**\n`{online_link}`\n\n"
        f"🖥️ **Watch Now:**\n`{stream_link}`\n\n"
        "⏰ **Note:** Links are available as long as the bot is active."
    )
    
    try:
        await command_message.reply_text(
            msg_text,
            quote=True,
            disable_web_page_preview=True,
            parse_mode=enums.ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🖥️ Watch Now", url=stream_link),
                    InlineKeyboardButton("📥 Download", url=online_link)
                ]
            ]),
        )
    except Exception as e:
        logger.error(f"Error sending links to user: {e}")
        raise

async def log_request(log_msg, user, stream_link, online_link):
    try:
        await log_msg.reply_text(
            f"👤 **Requested by:** [{user.first_name}](tg://user?id={user.id})\n"
            f"🆔 **User ID:** `{user.id}`\n\n"
            f"📥 **Download Link:** `{online_link}`\n"
            f"🖥️ **Watch Now Link:** `{stream_link}`",
            disable_web_page_preview=True,
            quote=True
        )
    except Exception:
        pass

async def check_admin_privileges(client, chat_id):
    try:
        member = await client.get_chat_member(chat_id, client.me.id)
        return member.status in [
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.OWNER
        ]
    except Exception:
        return False

async def log_new_user(bot, user_id, first_name):
    try:
        if not await db.is_user_exist(user_id):
            await db.add_user(user_id)
            
            if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
                await bot.send_message(
                    Var.BIN_CHANNEL,
                    f"👋 **New User Alert!**\n\n"
                    f"✨ **Name:** [{first_name}](tg://user?id={user_id})\n"
                    f"🆔 **User ID:** `{user_id}`\n\n"
                    "has started the bot!"
                )
    except Exception:
        pass

async def process_media_message(client, command_message, media_message, notify=True):
    retries = 0
    max_retries = 3
    
    while retries < max_retries:
        try:
            cache_key = get_file_unique_id(media_message)
            if cache_key is None:
                if notify:
                    await command_message.reply_text(
                        "⚠️ Could not extract file identifier from the media.",
                        quote=True
                    )
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
            
            log_msg = await forward_media(media_message)
            stream_link, online_link, media_name, media_size = await generate_media_links(log_msg)
            
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
            await asyncio.sleep(1)
            continue
        
        except MediaEmpty:
            if notify:
                await command_message.reply_text(
                    "⚠️ The media appears to be empty or corrupted.",
                    quote=True
                )
            return None
        
        except Exception as e:
            logger.error(f"Error processing media: {e}")
            
            if retries < max_retries - 1:
                retries += 1
                await asyncio.sleep(1)
                continue
            
            if notify:
                await handle_user_error(
                    command_message, 
                    "An unexpected error occurred while processing your media."
                )
            
            await notify_owner(
                client, 
                f"⚠️ Critical error processing media:\n{e}"
            )
            return None
    
    return None

async def process_multiple_messages(client, command_message, reply_msg, num_files, status_msg):
    chat_id = command_message.chat.id
    start_message_id = reply_msg.id
    end_message_id = start_message_id + num_files - 1
    message_ids = list(range(start_message_id, end_message_id + 1))
    
    try:
        batch_size = 10
        processed_count = 0
        download_links = []
        
        for i in range(0, len(message_ids), batch_size):
            batch_ids = message_ids[i:i+batch_size]
            
            try:
                await status_msg.edit(
                    f"⏳ **Processing messages {i+1} to {min(i+batch_size, len(message_ids))} of {len(message_ids)}...**"
                )
                
                messages = await client.get_messages(
                    chat_id=chat_id,
                    message_ids=batch_ids
                )
                
                # Process each message that contains media
                batch_tasks = []
                for msg in messages:
                    if msg and msg.media:
                        task = asyncio.create_task(
                            process_media_message(
                                client,
                                command_message,
                                msg,
                                notify=False
                            )
                        )
                        batch_tasks.append(task)
                
                batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
                
                for result in batch_results:
                    if isinstance(result, Exception):
                        continue
                    
                    if result:
                        download_links.append(result)
                        processed_count += 1
                
                await status_msg.edit(
                    f"⏳ **Processed {processed_count}/{num_files} files...**"
                )
            
            except Exception:
                await status_msg.edit(
                    f"⚠️ **Error fetching some messages**\n"
                    f"Continuing with successfully processed files..."
                )
                await asyncio.sleep(1)
        
        # Send all links in a single message
        if download_links:
            links_text = "\n".join(download_links)
            
            # Send to group chat
            await command_message.reply_text(
                f"📥 **Here are your {processed_count} download links:**\n\n`{links_text}`",
                quote=True,
                disable_web_page_preview=True
            )
            
            # Also send to DM if in a group
            if command_message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
                try:
                    await client.send_message(
                        chat_id=command_message.from_user.id,
                        text=f"📥 **Here are your {processed_count} download links from {command_message.chat.title}:**\n\n`{links_text}`",
                        disable_web_page_preview=True
                    )
                except Exception:
                    # User might not have started the bot
                    pass
        
        await status_msg.edit(
            f"✅ **Processed {processed_count} files out of {num_files} requested.**"
        )
    
    except Exception as e:
        await status_msg.edit(
            f"❌ **Error processing files: {e}**\n"
            f"Successfully processed: {processed_count}/{num_files}"
        )

# Command Handlers
@StreamBot.on_message(filters.command("link") & ~filters.private)
async def link_handler(client, message):
    user_id = message.from_user.id
    
    if not await db.is_user_exist(user_id):
        try:
            invite_link = f"https://t.me/{client.me.username}?start=start"
            await message.reply_text(
                "⚠️ You need to start the bot in private first to use this command.\n"
                f"👉 [Click here]({invite_link}) to start a private chat.",
                disable_web_page_preview=True,
                parse_mode=enums.ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📩 Start Chat", url=invite_link)]]),
                quote=True
            )
        except Exception:
            pass
        
        return
    
    if await rate_limiter.is_rate_limited(user_id):
        reset_time = await rate_limiter.get_reset_time(user_id)
        await message.reply_text(
            f"⚠️ **Rate limit reached.** Please try again in {reset_time:.0f} seconds.",
            quote=True
        )
        return
    
    if message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        is_admin = await check_admin_privileges(client, message.chat.id)
        if not is_admin:
            await message.reply_text(
                "🔒 The bot needs to be an admin in this group to function properly.",
                quote=True
            )
            return
    
    if not message.reply_to_message:
        await message.reply_text(
            "⚠️ Please use the /link command in reply to a file.",
            quote=True
        )
        return
    
    reply_msg = message.reply_to_message
    
    if not reply_msg.media:
        await message.reply_text(
            "⚠️ The message you're replying to does not contain any file.",
            quote=True
        )
        return
    
    command_parts = message.text.strip().split()
    num_files = 1
    
    if len(command_parts) > 1:
        try:
            num_files = int(command_parts[1])
            if num_files < 1 or num_files > 25:
                await message.reply_text(
                    "⚠️ **Please specify a number between 1 and 25.**",
                    quote=True
                )
                return
        except ValueError:
            await message.reply_text(
                "⚠️ **Invalid number specified.**",
                quote=True
            )
            return
    
    processing_msg = await message.reply_text(
        "⏳ **Processing your request...**",
        quote=True
    )
    
    try:
        if num_files == 1:
            result = await process_media_message(client, message, reply_msg)
            if result:
                # Also send to DM if in a group
                if message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
                    try:
                        cached_data = await CACHE.get(get_file_unique_id(reply_msg))
                        if cached_data:
                            await client.send_message(
                                chat_id=message.from_user.id,
                                text=f"📥 **Here is your download link from {message.chat.title}:**\n\n`{cached_data['online_link']}`",
                                disable_web_page_preview=True,
                                reply_markup=InlineKeyboardMarkup([
                                    [
                                        InlineKeyboardButton("🖥️ Watch Now", url=cached_data['stream_link']),
                                        InlineKeyboardButton("📥 Download", url=cached_data['online_link'])
                                    ]
                                ]),
                            )
                    except Exception:
                        # User might not have started the bot
                        pass
                await processing_msg.delete()
        else:
            await process_multiple_messages(client, message, reply_msg, num_files, processing_msg)
    except Exception as e:
        logger.error(f"Error handling link command: {e}")
        await processing_msg.edit(
            "❌ **An error occurred while processing your request. Please try again later.**"
        )

@StreamBot.on_message(
    filters.private & filters.incoming &
    (
        filters.document | filters.video | filters.photo | filters.audio |
        filters.voice | filters.animation | filters.video_note
    ),
    group=4
)
async def private_receive_handler(client, message):
    if not message.from_user:
        return
    
    if await rate_limiter.is_rate_limited(message.from_user.id):
        reset_time = await rate_limiter.get_reset_time(message.from_user.id)
        await message.reply_text(
            f"⚠️ **Rate limit reached.** Please try again in {reset_time:.0f} seconds.",
            quote=True
        )
        return
    
    await log_new_user(
        bot=client,
        user_id=message.from_user.id,
        first_name=message.from_user.first_name
    )
    
    processing_msg = await message.reply_text(
        "⏳ **Processing your file...**",
        quote=True
    )
    
    try:
        result = await process_media_message(client, message, message)
        if result:
            await processing_msg.delete()
    except Exception as e:
        logger.error(f"Error in private handler: {e}")
        await processing_msg.edit(
            "❌ **An error occurred while processing your file. Please try again later.**"
        )

@StreamBot.on_message(
    filters.channel & filters.incoming &
    (
        filters.document | filters.video | filters.photo | filters.audio |
        filters.voice | filters.animation | filters.video_note
    ) &
    ~filters.forwarded,
    group=-1
)
async def channel_receive_handler(client, broadcast):
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
            stream_link, online_link, media_name, media_size = await generate_media_links(log_msg)
            await log_request(log_msg, broadcast.chat, stream_link, online_link)
            
            if can_edit:
                try:
                    await client.edit_message_reply_markup(
                        chat_id=broadcast.chat.id,
                        message_id=broadcast.id,
                        reply_markup=InlineKeyboardMarkup([
                            [
                                InlineKeyboardButton("🖥️ Watch Now", url=stream_link),
                                InlineKeyboardButton("📥 Download", url=online_link)
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
            await asyncio.sleep(1)
            continue
        
        except Exception as e:
            logger.error(f"Error handling channel message: {e}")
            
            if retries < max_retries - 1:
                retries += 1
                await asyncio.sleep(1)
                continue
            
            await notify_owner(
                client, 
                f"⚠️ Critical error in channel handler:\n{e}"
            )
            break

# Background Tasks
async def clean_cache_task():
    while True:
        try:
            await asyncio.sleep(3600)  # Run every hour
            await CACHE.clean_expired()
        except Exception as e:
            logger.error(f"Error in cache cleaning task: {e}")

# Start the cache cleaning task
StreamBot.loop.create_task(clean_cache_task())
