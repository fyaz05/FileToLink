# Thunder/bot/plugins/stream.py

import time
import asyncio
from urllib.parse import quote_plus
from typing import Optional, Tuple, Dict, Union, List

from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, RPCError
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

# ==============================
# Database Initialization
# ==============================

db: Database = Database(Var.DATABASE_URL, Var.NAME)

# ==============================
# Cache Configurations
# ==============================

CACHE: Dict[str, Dict[str, Union[str, float]]] = {}
CACHE_EXPIRY: int = 86400  # 24 hours

# ==============================
# Helper Functions
# ==============================

async def handle_flood_wait(e: FloodWait) -> None:
    """
    Handles FloodWait exceptions by logging a warning and sleeping for the required duration.

    Args:
        e (FloodWait): The FloodWait exception containing the wait duration.
    """
    logger.warning(f"FloodWait encountered. Sleeping for {e.value} seconds.")
    await asyncio.sleep(e.value + 1)


async def notify_owner(client: Client, text: str) -> None:
    """
    Sends a notification message to the bot owners and BIN_CHANNEL if configured.

    Args:
        client (Client): The Pyrogram client instance.
        text (str): The notification message to send.
    """
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
        logger.error(
            f"Failed to send message to owner or BIN_CHANNEL: {e}",
            exc_info=True
        )


async def handle_user_error(message: Message, error_msg: str) -> None:
    """
    Sends an error message to the user in response to an issue.

    Args:
        message (Message): The original message from the user.
        error_msg (str): The error message to send.
    """
    try:
        await message.reply_text(
            f"❌ {error_msg}\nPlease try again or contact support.",
            quote=True
        )
    except Exception as e:
        logger.error(
            f"Failed to send error message to user: {e}",
            exc_info=True
        )


def get_file_unique_id(media_message: Message) -> Optional[str]:
    """
    Retrieves the unique file identifier from a media message.

    Args:
        media_message (Message): The media message to extract the unique ID from.

    Returns:
        Optional[str]: The unique file identifier if found, else None.
    """
    media_types = [
        'document', 'video', 'audio', 'photo', 'animation',
        'voice', 'video_note', 'sticker'
    ]
    for media_type in media_types:
        media = getattr(media_message, media_type, None)
        if media:
            return media.file_unique_id
    return None


async def forward_media(media_message: Message) -> Message:
    """
    Forwards a media message to the BIN_CHANNEL.

    Args:
        media_message (Message): The media message to forward.

    Returns:
        Message: The forwarded message in BIN_CHANNEL.

    Raises:
        Exception: If forwarding fails after handling FloodWait.
    """
    try:
        return await media_message.forward(chat_id=Var.BIN_CHANNEL)
    except FloodWait as e:
        await handle_flood_wait(e)
        return await forward_media(media_message)
    except Exception as e:
        error_text = f"Error forwarding media message: {e}"
        logger.error(error_text, exc_info=True)
        await notify_owner(media_message._client, error_text)
        raise


async def generate_media_links(log_msg: Message) -> Tuple[str, str, str, str]:
    """
    Generates streaming and download links for the forwarded media message.

    Args:
        log_msg (Message): The forwarded message in BIN_CHANNEL.

    Returns:
        Tuple[str, str, str, str]: A tuple containing the stream link, online download link,
                                    media name, and media size.

    Raises:
        Exception: If link generation fails.
    """
    try:
        base_url = Var.URL.rstrip("/")
        file_id = log_msg.id
        media_name = get_name(log_msg)
        if isinstance(media_name, bytes):
            media_name = media_name.decode('utf-8', errors='replace')
        else:
            media_name = str(media_name)
        media_size = humanbytes(get_media_file_size(log_msg))
        file_name_encoded = quote_plus(media_name)
        hash_value = get_hash(log_msg)
        stream_link = f"{base_url}/watch/{file_id}/{file_name_encoded}?hash={hash_value}"
        online_link = f"{base_url}/{file_id}/{file_name_encoded}?hash={hash_value}"
        logger.info(f"Generated media links for file_id {file_id}")
        return stream_link, online_link, media_name, media_size
    except Exception as e:
        error_text = f"Error generating media links: {e}"
        logger.error(error_text, exc_info=True)
        await notify_owner(log_msg._client, error_text)
        raise


async def send_links_to_user(
    client: Client,
    command_message: Message,
    media_name: str,
    media_size: str,
    stream_link: str,
    online_link: str
) -> None:
    """
    Sends the generated links to the user via a reply message.

    Args:
        client (Client): The Pyrogram client instance.
        command_message (Message): The original command message from the user.
        media_name (str): The name of the media file.
        media_size (str): The size of the media file in human-readable format.
        stream_link (str): The link to stream the media.
        online_link (str): The direct download link for the media.
    """
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
        logger.info(f"Sent links to user {command_message.from_user.id}")
    except Exception as e:
        error_text = f"Error sending links to user: {e}"
        logger.error(error_text, exc_info=True)
        await notify_owner(client, error_text)
        raise


async def log_request(
    log_msg: Message,
    user: Union[User, Chat],
    stream_link: str,
    online_link: str
) -> None:
    """
    Logs the user's request in BIN_CHANNEL by replying to the forwarded message.

    Args:
        log_msg (Message): The forwarded message in BIN_CHANNEL.
        user (Union[User, Chat]): The user who requested the links.
        stream_link (str): The streaming link generated for the media.
        online_link (str): The direct download link generated for the media.
    """
    try:
        await log_msg.reply_text(
            f"👤 **Requested by:** [{user.first_name}](tg://user?id={user.id})\n"
            f"🆔 **User ID:** `{user.id}`\n\n"
            f"📥 **Download Link:** `{online_link}`\n"
            f"🖥️ **Watch Now Link:** `{stream_link}`",
            disable_web_page_preview=True,
            quote=True
        )
        logger.info(f"Logged request in BIN_CHANNEL for user {user.id}")
    except Exception as e:
        error_text = f"Error logging request: {e}"
        logger.error(error_text, exc_info=True)


async def check_admin_privileges(client: Client, chat_id: int) -> bool:
    """
    Checks if the bot is an admin in the specified group or supergroup.

    Args:
        client (Client): The Pyrogram client instance.
        chat_id (int): The ID of the chat to check.

    Returns:
        bool: True if the bot is an admin, False otherwise.
    """
    try:
        # Retrieve the bot's member status in the chat
        member = await client.get_chat_member(chat_id, client.me.id)
        # Check if the bot has admin status or is the creator of the group
        return member.status in [
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.OWNER
        ]
    except Exception as e:
        # Log any errors and return False if the check fails
        logger.error(
            f"Error checking admin privileges in chat {chat_id}: {e}",
            exc_info=True
        )
        return False

# ==============================
# Command Handlers
# ==============================

@StreamBot.on_message(filters.command("link") & ~filters.private)
async def link_handler(client: Client, message: Message) -> None:
    """
    Handles the /link command in groups and ensures the bot has admin privileges
    before proceeding with the command execution.

    Args:
        client (Client): The Pyrogram client instance.
        message (Message): The incoming message triggering the command.
    """
    user_id: int = message.from_user.id

    # Check if the user has started the bot in private (registration check)
    if not await db.is_user_exist(user_id):
        try:
            invite_link: str = f"https://t.me/{client.me.username}?start=start"
            await message.reply_text(
                "⚠️ You need to start the bot in private first to use this command.\n"
                f"👉 [Click here]({invite_link}) to start a private chat.",
                disable_web_page_preview=True,
                parse_mode=enums.ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📩 Start Chat", url=invite_link)]
                ]),
                quote=True
            )
            logger.info(f"User {user_id} prompted to start bot in private.")
        except Exception as e:
            logger.error(
                f"Error sending start prompt to user: {e}",
                exc_info=True
            )
            await message.reply_text(
                "⚠️ Please start the bot in private by sending /start to me.",
                quote=True
            )
        return

    # Check for admin privileges if in a group or supergroup
    if message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        is_admin: bool = await check_admin_privileges(client, message.chat.id)
        if not is_admin:
            await message.reply_text(
                "🔒 The bot needs to be an admin in this group to function properly.\n"
                "Please promote the bot to admin and try again.",
                quote=True
            )
            return

    # Proceed if the bot has admin privileges or if this is a private chat
    if not message.reply_to_message:
        await message.reply_text(
            "⚠️ Please use the /link command in reply to a file.",
            quote=True
        )
        return

    reply_msg: Message = message.reply_to_message
    if not reply_msg.media:
        await message.reply_text(
            "⚠️ The message you're replying to does not contain any file.",
            quote=True
        )
        return

    command_parts: List[str] = message.text.strip().split()
    num_files: int = 1
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

    if num_files == 1:
        await process_media_message(client, message, reply_msg)
    else:
        await process_multiple_messages(client, message, reply_msg, num_files)


async def process_multiple_messages(
    client: Client,
    command_message: Message,
    reply_msg: Message,
    num_files: int
) -> None:
    """
    Processes multiple media messages based on the number specified by the user.

    Args:
        client (Client): The Pyrogram client instance.
        command_message (Message): The original command message from the user.
        reply_msg (Message): The message to which the command was replied.
        num_files (int): The number of files to process.
    """
    chat_id: int = command_message.chat.id
    start_message_id: int = reply_msg.id
    end_message_id: int = start_message_id + num_files - 1
    message_ids: List[int] = list(range(start_message_id, end_message_id + 1))

    try:
        messages: List[Optional[Message]] = await client.get_messages(
            chat_id=chat_id,
            message_ids=message_ids
        )
    except RPCError as e:
        await command_message.reply_text(
            f"❌ Failed to fetch messages: {e}",
            quote=True
        )
        logger.error(f"Failed to fetch messages: {e}", exc_info=True)
        return

    processed_count: int = 0
    download_links: List[str] = []
    for msg in messages:
        if msg and msg.media:
            download_link: Optional[str] = await process_media_message(
                client,
                command_message,
                msg
            )
            if download_link:
                download_links.append(download_link)
                processed_count += 1
        else:
            logger.info(
                f"Message {msg.id if msg else 'Unknown'} does not contain media or is inaccessible, skipping."
            )

    if download_links:
        links_text: str = "\n".join(download_links)
        message_text: str = (
            f"📥 **Here are your {processed_count} combined download links:**\n\n`{links_text}`"
        )
        await command_message.reply_text(
            message_text,
            quote=True,
            disable_web_page_preview=True
        )

    await command_message.reply_text(
        f"✅ **Processed {processed_count} files starting from the replied message.**",
        quote=True
    )


@StreamBot.on_message(
    filters.private & filters.incoming &
    (
        filters.document | filters.video | filters.photo | filters.audio |
        filters.voice | filters.animation | filters.video_note
    ),
    group=4
)
async def private_receive_handler(client: Client, message: Message) -> None:
    """
    Handles incoming media messages in private chats.

    Args:
        client (Client): The Pyrogram client instance.
        message (Message): The incoming media message.
    """
    await process_media_message(client, message, message)


async def process_media_message(
    client: Client,
    command_message: Message,
    media_message: Message
) -> Optional[str]:
    """
    Processes a single media message by forwarding, generating links, caching,
    and sending links to the user.

    Args:
        client (Client): The Pyrogram client instance.
        command_message (Message): The original command or media message.
        media_message (Message): The media message to process.

    Returns:
        Optional[str]: The online download link if successful, else None.
    """
    retries: int = 0
    max_retries: int = 5
    while retries < max_retries:
        try:
            cache_key: Optional[str] = get_file_unique_id(media_message)
            if cache_key is None:
                await command_message.reply_text(
                    "⚠️ Could not extract file identifier from the media."
                )
                return None

            cached_data: Optional[Dict[str, Union[str, float]]] = CACHE.get(cache_key)
            if cached_data and (time.time() - cached_data['timestamp'] < CACHE_EXPIRY):
                await send_links_to_user(
                    client,
                    command_message,
                    cached_data['media_name'],
                    cached_data['media_size'],
                    cached_data['stream_link'],
                    cached_data['online_link']
                )
                logger.info(
                    f"Served links from cache for user {command_message.from_user.id}"
                )
                return cached_data['online_link']

            log_msg: Message = await forward_media(media_message)
            stream_link, online_link, media_name, media_size = await generate_media_links(log_msg)

            CACHE[cache_key] = {
                'media_name': media_name,
                'media_size': media_size,
                'stream_link': stream_link,
                'online_link': online_link,
                'timestamp': time.time()
            }

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
        except Exception as e:
            error_text: str = f"Error processing media message: {e}"
            logger.error(error_text, exc_info=True)
            await handle_user_error(command_message, "An unexpected error occurred.")
            await notify_owner(client, f"⚠️ Critical error occurred:\n{e}")
            return None

    return None


@StreamBot.on_message(
    filters.channel & filters.incoming &
    (
        filters.document | filters.video | filters.photo | filters.audio |
        filters.voice | filters.animation | filters.video_note
    ) &
    ~filters.forwarded,
    group=-1
)
async def channel_receive_handler(client: Client, broadcast: Message) -> None:
    """
    Handles incoming media messages from channels, forwards them, generates links,
    and updates the message with link buttons.

    Args:
        client (Client): The Pyrogram client instance.
        broadcast (Message): The incoming media message from the channel.
    """
    retries: int = 0
    max_retries: int = 5
    while retries < max_retries:
        try:
            if int(broadcast.chat.id) in Var.BANNED_CHANNELS:
                await client.leave_chat(broadcast.chat.id)
                logger.info(f"Left banned channel: {broadcast.chat.id}")
                return

            log_msg: Message = await forward_media(broadcast)
            stream_link, online_link, media_name, media_size = await generate_media_links(log_msg)
            await log_request(log_msg, broadcast.chat, stream_link, online_link)

            can_edit: bool = False
            try:
                member = await client.get_chat_member(broadcast.chat.id, client.me.id)
                if member.status in ["administrator", "creator"]:
                    can_edit = True
                logger.info(
                    f"Bot can_edit_messages in chat {broadcast.chat.id}: {can_edit}"
                )
            except Exception as e:
                logger.error(
                    f"Error checking bot's admin status: {e}",
                    exc_info=True
                )

            if can_edit:
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
                logger.info(f"Edited broadcast message in channel {broadcast.chat.id}")
            else:
                await client.send_message(
                    chat_id=broadcast.chat.id,
                    text=(
                        "🔗 **Your Links are Ready!**\n\n"
                        f"📄 **File Name:** `{media_name}`\n"
                        f"📂 **File Size:** `{media_size}`\n\n"
                        f"📥 **Download Link:**\n`{online_link}`\n\n"
                        f"🖥️ **Watch Now:**\n`{stream_link}`\n\n"
                        "⏰ **Note:** Links are available as long as the bot is active."
                    ),
                    reply_markup=InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("🖥️ Watch Now", url=stream_link),
                            InlineKeyboardButton("📥 Download", url=online_link)
                        ]
                    ]),
                )
                logger.info(
                    f"Sent new message with links in channel {broadcast.chat.id}"
                )
            break

        except FloodWait as e:
            await handle_flood_wait(e)
            retries += 1
            continue
        except Exception as e:
            error_text: str = f"Error handling channel message: {e}"
            logger.error(error_text, exc_info=True)
            await notify_owner(client, f"⚠️ Critical error occurred in channel handler:\n{e}")
            break

# ==============================
# Background Tasks
# ==============================

async def clean_cache_task() -> None:
    """
    Periodically cleans up expired entries from the cache.
    """
    while True:
        await asyncio.sleep(3600)  # Sleep for 1 hour
        current_time: float = time.time()
        keys_to_delete: List[str] = [
            key for key, value in CACHE.items()
            if current_time - value['timestamp'] > CACHE_EXPIRY
        ]
        for key in keys_to_delete:
            del CACHE[key]
        if keys_to_delete:
            logger.info(f"Cache cleaned up. Removed {len(keys_to_delete)} entries.")


# Start the cache cleaning task
StreamBot.loop.create_task(clean_cache_task())
