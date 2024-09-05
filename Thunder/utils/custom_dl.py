import asyncio
import logging
from typing import Dict, Union

from hydrogram import Client, raw
from hydrogram.session import Session, Auth
from hydrogram.errors import AuthBytesInvalid, RPCError
from hydrogram.file_id import FileId, FileType, ThumbnailSource

from Thunder.vars import Var
from Thunder.bot import work_loads
from Thunder.server.exceptions import FileNotFound
from .file_properties import get_file_ids

# Configure logging
logging.basicConfig(level=logging.DEBUG)
LOGGER = logging.getLogger(__name__)

class ByteStreamer:
    def __init__(self, client: Client):
        self.client = client
        self.clean_timer = 30 * 60  # Cache clean interval in seconds
        self.cached_file_ids: Dict[int, FileId] = {}
        asyncio.create_task(self.clean_cache())

    async def get_file_properties(self, message_id: int) -> FileId:
        """Get file properties from cache or generate if not available."""
        if message_id not in self.cached_file_ids:
            self.cached_file_ids[message_id] = await self.generate_file_properties(message_id)
        return self.cached_file_ids[message_id]

    async def generate_file_properties(self, message_id: int) -> FileId:
        """Fetch and generate file properties for a given message ID."""
        file_id = await get_file_ids(self.client, Var.BIN_CHANNEL, message_id)
        if not file_id:
            raise FileNotFound(f"File with message ID {message_id} not found.")
        LOGGER.debug(f"Generated and cached file properties for message ID {message_id}.")
        return file_id

    async def generate_media_session(self, file_id: FileId) -> Session:
        """Create or reuse a media session based on the file's data centre ID."""
        media_session = self.client.media_sessions.get(file_id.dc_id)
        if media_session is None:
            media_session = await self._create_or_reuse_media_session(file_id)
            self.client.media_sessions[file_id.dc_id] = media_session
        return media_session

    async def _create_or_reuse_media_session(self, file_id: FileId) -> Session:
        """Create a new media session or reuse an existing one."""
        client_dc_id = await self.client.storage.dc_id()
        
        if file_id.dc_id != client_dc_id:
            session = await self._create_media_session(file_id)
            await self._authenticate_session(file_id, session)
        else:
            session = Session(
                self.client,
                file_id.dc_id,
                await self.client.storage.auth_key(),
                await self.client.storage.test_mode(),
                is_media=True,
            )
            await session.start()
        
        return session

    async def _create_media_session(self, file_id: FileId) -> Session:
        """Creates a new media session for a given file."""
        session = Session(
            self.client,
            file_id.dc_id,
            await Auth(self.client, file_id.dc_id, await self.client.storage.test_mode()).create(),
            await self.client.storage.test_mode(),
            is_media=True,
        )
        await session.start()
        return session

    async def _authenticate_session(self, file_id: FileId, session: Session) -> None:
        """Authenticate a session, retrying up to three times if necessary."""
        for attempt in range(3):
            try:
                exported_auth = await self.client.invoke(raw.functions.auth.ExportAuthorization(dc_id=file_id.dc_id))
                LOGGER.debug(f"Attempt {attempt + 1}: Exported auth for DC {file_id.dc_id}")

                await asyncio.sleep(1)  # Delay for transient issue management
                await session.send(raw.functions.auth.ImportAuthorization(id=exported_auth.id, bytes=exported_auth.bytes))
                LOGGER.info(f"Authorization imported successfully for DC {file_id.dc_id}")
                return

            except AuthBytesInvalid:
                LOGGER.warning(f"Attempt {attempt + 1}: Invalid auth bytes for DC {file_id.dc_id}")
                if attempt == 2:
                    await asyncio.sleep(2)
                    await session.stop()
                    raise

            except RPCError as e:
                LOGGER.error(f"RPC error during auth attempt: {e}")
                await asyncio.sleep(1)

    @staticmethod
    async def get_location(file_id: FileId) -> Union[
        raw.types.InputPhotoFileLocation,
        raw.types.InputDocumentFileLocation,
        raw.types.InputPeerPhotoFileLocation
    ]:
        """Get the appropriate location object for the file type."""
        file_type = file_id.file_type

        if file_type == FileType.CHAT_PHOTO:
            peer = ByteStreamer._create_chat_peer(file_id)
            return raw.types.InputPeerPhotoFileLocation(
                peer=peer,
                volume_id=file_id.volume_id,
                local_id=file_id.local_id,
                big=file_id.thumbnail_source == ThumbnailSource.CHAT_PHOTO_BIG,
            )

        if file_type == FileType.PHOTO:
            return raw.types.InputPhotoFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )

        return raw.types.InputDocumentFileLocation(
            id=file_id.media_id,
            access_hash=file_id.access_hash,
            file_reference=file_id.file_reference,
            thumb_size=file_id.thumbnail_size,
        )

    @staticmethod
    def _create_chat_peer(file_id: FileId) -> Union[raw.types.InputPeerUser, raw.types.InputPeerChat, raw.types.InputPeerChannel]:
        """Create a chat peer for a given chat ID."""
        if file_id.chat_id > 0:
            return raw.types.InputPeerUser(user_id=file_id.chat_id, access_hash=file_id.chat_access_hash)

        if file_id.chat_access_hash == 0:
            return raw.types.InputPeerChat(chat_id=-file_id.chat_id)

        return raw.types.InputPeerChannel(channel_id=file_id.chat_id & 0x7FFFFFFFFFFFFFFF, access_hash=file_id.chat_access_hash)

    async def yield_file(self, file_id: FileId, index: int, offset: int, first_part_cut: int,
                         last_part_cut: int, part_count: int, chunk_size: int) -> Union[str, None]:
        """Yield chunks of a file, handling cuts at the first and last parts."""
        client = self.client
        work_loads[index] += 1
        LOGGER.debug(f"Starting to yield file with client index {index}.")

        media_session = await self.generate_media_session(file_id)
        current_part = 1
        location = await self.get_location(file_id)

        try:
            while current_part <= part_count:
                response = await media_session.send(
                    raw.functions.upload.GetFile(location=location, offset=offset, limit=chunk_size)
                )
                if not isinstance(response, raw.types.upload.File):
                    break

                chunk = response.bytes
                if not chunk:
                    break

                if part_count == 1:
                    yield chunk[first_part_cut:last_part_cut]
                elif current_part == 1:
                    yield chunk[first_part_cut:]
                elif current_part == part_count:
                    yield chunk[:last_part_cut]
                else:
                    yield chunk

                current_part += 1
                offset += chunk_size
        except (TimeoutError, AttributeError) as e:
            LOGGER.error(f"Error while fetching file part: {e}")
        finally:
            LOGGER.debug(f"Finished yielding file, processed {current_part - 1} parts.")
            work_loads[index] -= 1

    async def clean_cache(self) -> None:
        """Periodically clean the cache of stored file IDs."""
        while True:
            await asyncio.sleep(self.clean_timer)
            self.cached_file_ids.clear()
            LOGGER.debug("Cache cleaned.")
