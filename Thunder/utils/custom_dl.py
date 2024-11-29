# Thunder/utils/custom_dl.py

import math
import asyncio
from typing import Dict, Union
from pyrogram import Client, utils, raw
from pyrogram.session import Session, Auth
from pyrogram.errors import AuthBytesInvalid, RPCError, FloodWait
from pyrogram.file_id import FileId, FileType, ThumbnailSource
from Thunder.vars import Var
from Thunder.bot import work_loads
from Thunder.server.exceptions import FileNotFound
from .file_properties import get_file_ids
from Thunder.utils.logger import logger

class ByteStreamer:
    """
    A custom class that handles streaming of media files from Telegram servers.

    Attributes:
        client (Client): The Pyrogram client instance.
        clean_timer (int): Interval in seconds to clean the cache.
        cached_file_ids (Dict[int, FileId]): A cache for file properties.
        cache_lock (asyncio.Lock): An asyncio lock to ensure thread-safe access to the cache.
    """

    def __init__(self, client: Client):
        """
        Initialize the ByteStreamer with a Pyrogram client.

        Args:
            client (Client): The Pyrogram client instance.
        """
        self.client = client
        self.clean_timer = 30 * 60  # Cache clean interval in seconds (30 minutes)
        self.cached_file_ids: Dict[int, FileId] = {}
        self.cache_lock = asyncio.Lock()
        asyncio.create_task(self.clean_cache())
        logger.info("ByteStreamer initialized with client.")

    async def get_file_properties(self, message_id: int) -> FileId:
        """
        Get file properties from cache or generate if not available.

        Args:
            message_id (int): The message ID of the file.

        Returns:
            FileId: The file properties object.

        Raises:
            FileNotFound: If the file is not found in the channel.
        """
        logger.debug(f"Fetching file properties for message ID {message_id}.")
        async with self.cache_lock:
            file_id = self.cached_file_ids.get(message_id)
        
        if not file_id:
            logger.debug(f"File ID for message {message_id} not found in cache, generating...")
            file_id = await self.generate_file_properties(message_id)
            async with self.cache_lock:
                self.cached_file_ids[message_id] = file_id
            logger.info(f"Cached new file properties for message ID {message_id}.")
        
        return file_id

    async def generate_file_properties(self, message_id: int) -> FileId:
        """
        Generate file properties for a given message ID.

        Args:
            message_id (int): The message ID of the file.

        Returns:
            FileId: The file properties object.

        Raises:
            FileNotFound: If the file is not found.
        """
        logger.debug(f"Generating file properties for message ID {message_id}.")
        file_id = await get_file_ids(self.client, Var.BIN_CHANNEL, message_id)
        
        if not file_id:
            logger.warning(f"Message ID {message_id} not found in the channel.")
            raise FileNotFound(f"File with message ID {message_id} not found.")
        
        async with self.cache_lock:
            self.cached_file_ids[message_id] = file_id
        logger.info(f"Generated and cached file properties for message ID {message_id}.")
        
        return file_id

    async def generate_media_session(self, client: Client, file_id: FileId) -> Session:
        """
        Generates the media session for the DC that contains the media file.
        This is required for getting the bytes from Telegram servers.
        """
        media_session = client.media_sessions.get(file_id.dc_id, None)

        if media_session is None:
            if file_id.dc_id != await client.storage.dc_id():
                media_session = Session(
                    client,
                    file_id.dc_id,
                    await Auth(
                        client, file_id.dc_id, await client.storage.test_mode()
                    ).create(),
                    await client.storage.test_mode(),
                    is_media=True,
                )
                await media_session.start()

                for _ in range(6):
                    exported_auth = await client.invoke(
                        raw.functions.auth.ExportAuthorization(dc_id=file_id.dc_id)
                    )

                    try:
                        await media_session.send(
                            raw.functions.auth.ImportAuthorization(
                                id=exported_auth.id, bytes=exported_auth.bytes
                            )
                        )
                        break
                    except AuthBytesInvalid:
                        logger.debug(
                            f"Invalid authorization bytes for DC {file_id.dc_id}"
                        )
                        continue
                else:
                    await media_session.stop()
                    raise AuthBytesInvalid
            else:
                media_session = Session(
                    client,
                    file_id.dc_id,
                    await client.storage.auth_key(),
                    await client.storage.test_mode(),
                    is_media=True,
                )
                await media_session.start()
            logger.debug(f"Created media session for DC {file_id.dc_id}")
            client.media_sessions[file_id.dc_id] = media_session
        else:
            logger.debug(f"Using cached media session for DC {file_id.dc_id}")
        return media_session

    @staticmethod
    async def get_location(file_id: FileId) -> Union[
        raw.types.InputPhotoFileLocation,
        raw.types.InputDocumentFileLocation,
        raw.types.InputPeerPhotoFileLocation,
    ]:
        """
        Get the appropriate location object for the file type.

        Args:
            file_id (FileId): The file properties object.

        Returns:
            Union[InputPhotoFileLocation, InputDocumentFileLocation, InputPeerPhotoFileLocation]: The location object.
        """
        logger.debug(f"Determining location for file type {file_id.file_type}.")
        file_type = file_id.file_type

        if file_type == FileType.CHAT_PHOTO:
            if file_id.chat_id > 0:
                peer = raw.types.InputPeerUser(
                    user_id=file_id.chat_id, access_hash=file_id.chat_access_hash
                )
            else:
                if file_id.chat_access_hash == 0:
                    peer = raw.types.InputPeerChat(chat_id=-file_id.chat_id)
                else:
                    peer = raw.types.InputPeerChannel(
                        channel_id=utils.get_channel_id(file_id.chat_id),
                        access_hash=file_id.chat_access_hash,
                    )
            location = raw.types.InputPeerPhotoFileLocation(
                peer=peer,
                volume_id=file_id.volume_id,
                local_id=file_id.local_id,
                big=file_id.thumbnail_source == ThumbnailSource.CHAT_PHOTO_BIG,
            )
        elif file_type == FileType.PHOTO:
            location = raw.types.InputPhotoFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        else:
            location = raw.types.InputDocumentFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        logger.debug(f"Location determined for file ID {file_id.media_id}.")
        return location

    async def yield_file(
        self,
        file_id: FileId,
        index: int,
        offset: int,
        first_part_cut: int,
        last_part_cut: int,
        part_count: int,
        chunk_size: int,
    ) -> Union[bytes, None]:
        """
        Yield chunks of a file, handling cuts at the first and last parts.

        Args:
            file_id (FileId): The file properties object.
            index (int): The client index.
            offset (int): The offset to start reading from.
            first_part_cut (int): The number of bytes to cut from the first chunk.
            last_part_cut (int): The number of bytes to cut from the last chunk.
            part_count (int): The total number of parts.
            chunk_size (int): The size of each chunk.

        Yields:
            bytes: The next chunk of data.
        """
        client = self.client
        work_loads[index] += 1
        logger.debug(f"Starting to yield file with client index {index}.")

        media_session = await self.generate_media_session(client, file_id)
        current_part = 1
        location = await self.get_location(file_id)

        try:
            r = await media_session.send(
                raw.functions.upload.GetFile(
                    location=location, offset=offset, limit=chunk_size
                ),
            )
            if isinstance(r, raw.types.upload.File):
                while True:
                    chunk = r.bytes
                    if not chunk:
                        break
                    elif part_count == 1:
                        yield chunk[first_part_cut:last_part_cut]
                    elif current_part == 1:
                        yield chunk[first_part_cut:]
                    elif current_part == part_count:
                        yield chunk[:last_part_cut]
                    else:
                        yield chunk

                    current_part += 1
                    offset += chunk_size

                    if current_part > part_count:
                        break

                    r = await media_session.send(
                        raw.functions.upload.GetFile(
                            location=location, offset=offset, limit=chunk_size
                        ),
                    )
        except (TimeoutError, AttributeError):
            logger.error(f"Error while yielding file: TimeoutError or AttributeError encountered.")
            pass
        finally:
            logger.debug(f"Finished yielding file with {current_part} parts.")
            work_loads[index] -= 1

    async def clean_cache(self) -> None:
        """
        Periodically clean the cache of stored file IDs.
        """
        while True:
            await asyncio.sleep(self.clean_timer)
            async with self.cache_lock:
                self.cached_file_ids.clear()
            logger.debug("Cache cleaned.")
