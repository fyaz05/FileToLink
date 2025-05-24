# Thunder/utils/custom_dl.py

import math
import asyncio
import time
from typing import Dict, Union, Optional, AsyncGenerator
from pyrogram import Client, utils, raw
from pyrogram.session import Session, Auth
from pyrogram.errors import AuthBytesInvalid, RPCError, FloodWait, FileReferenceExpired, FileReferenceInvalid
from pyrogram.file_id import FileId, FileType, ThumbnailSource
from Thunder.vars import Var
from Thunder.bot import work_loads
from Thunder.server.exceptions import FileNotFound
from .file_properties import get_file_ids
from Thunder.utils.logger import logger

class ByteStreamer:
    # A custom class that handles streaming of media files from Telegram servers
    
    def __init__(self, client: Client):
        self.client = client
        self.clean_timer = 30 * 60  # Cache clean interval in seconds (30 minutes)
        self.cached_file_ids: Dict[int, FileId] = {}
        self.file_references_cache: Dict[int, Dict[str, Union[FileId, float]]] = {}
        self.cache_lock = asyncio.Lock()
        self.media_sessions_lock = asyncio.Lock()
        
        # Start cache cleaning tasks
        self.cache_cleaner_task = asyncio.create_task(self.clean_cache())
        self.session_cleaner_task = asyncio.create_task(self.cleanup_media_sessions())
    
    async def shutdown(self):
        if hasattr(self, 'cache_cleaner_task'):
            self.cache_cleaner_task.cancel()
            try:
                await self.cache_cleaner_task
            except asyncio.CancelledError:
                pass
        
        if hasattr(self, 'session_cleaner_task'):
            self.session_cleaner_task.cancel()
            try:
                await self.session_cleaner_task
            except asyncio.CancelledError:
                pass
        
        # Clean up all media sessions
        for dc_id, session in list(self.client.media_sessions.items()):
            try:
                await session.stop()
            except Exception as e:
                logger.debug(f"Error stopping media session for DC {dc_id}: {e}")
        
        self.client.media_sessions.clear()
    
    async def get_file_properties(self, message_id: int) -> FileId:
        async with self.cache_lock:
            file_id = self.cached_file_ids.get(message_id)
        
        if not file_id:
            file_id = await self.generate_file_properties(message_id)
            async with self.cache_lock:
                self.cached_file_ids[message_id] = file_id
        
        return file_id
    
    async def generate_file_properties(self, message_id: int) -> FileId:
        file_id = await get_file_ids(self.client, Var.BIN_CHANNEL, message_id)
        
        if not file_id:
            logger.debug(f"Message ID {message_id} not found in the channel.")
            raise FileNotFound(f"File with message ID {message_id} not found.")
        
        async with self.cache_lock:
            self.cached_file_ids[message_id] = file_id
        
        return file_id
    
    async def refresh_file_reference(self, file_id: FileId) -> Optional[FileId]:
        logger.debug(f"Refreshing file reference for message ID {file_id.message_id}")
        
        # Check if we have a fresh cached reference
        async with self.cache_lock:
            cached_ref = self.file_references_cache.get(file_id.message_id)
            if cached_ref and time.time() - cached_ref["timestamp"] < 3600:  # Less than 1 hour old
                return cached_ref["file_id"]
        
        try:
            # Fetch the message again to get fresh file reference
            message = await self.client.get_messages(
                chat_id=Var.BIN_CHANNEL,
                message_ids=file_id.message_id
            )
            
            if not message or not message.media:
                logger.debug(f"Media message {file_id.message_id} not found when refreshing file reference")
                return None
            
            # Extract new file ID
            new_file_id = await get_file_ids(self.client, Var.BIN_CHANNEL, file_id.message_id)
            if not new_file_id:
                logger.error(f"Failed to extract file ID from message {file_id.message_id}")
                return None
            
            # Cache the new file reference
            async with self.cache_lock:
                self.file_references_cache[file_id.message_id] = {
                    "file_id": new_file_id,
                    "timestamp": time.time()
                }
            
            return new_file_id
        except Exception as e:
            logger.error(f"Error refreshing file reference: {e}")
            return None
    
    async def generate_media_session(self, client: Client, file_id: FileId) -> Session:
        async with self.media_sessions_lock:
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
                    
                    for attempt in range(6):
                        if attempt > 0:
                            # Add exponential backoff between attempts
                            await asyncio.sleep(1 * (2 ** (attempt - 1)))
                        
                        try:
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
                                continue
                        except FloodWait as e:
                            logger.warning(f"FloodWait during auth export: {e.value} seconds")
                            await asyncio.sleep(e.value + 1)
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
                
                # Mark the session creation time for cleanup
                setattr(media_session, 'last_used', time.time())
                client.media_sessions[file_id.dc_id] = media_session
            else:
                # Update last used time
                setattr(media_session, 'last_used', time.time())
        
        return media_session
    
    @staticmethod
    async def get_location(file_id: FileId) -> Union[
        raw.types.InputPhotoFileLocation,
        raw.types.InputDocumentFileLocation,
        raw.types.InputPeerPhotoFileLocation,
        None
    ]:
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
            if not file_id.file_reference:
                logger.debug("Missing file_reference for PHOTO type")
                return None
            location = raw.types.InputPhotoFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        else:
            if not file_id.file_reference:
                logger.debug("Missing file_reference for DOCUMENT type")
                return None
            location = raw.types.InputDocumentFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
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
    ) -> AsyncGenerator[bytes, None]:
        client = self.client
        work_loads[index] += 1
        
        try:
            media_session = await self.generate_media_session(client, file_id)
            current_part = 1
            
            # Get file location
            location = await self.get_location(file_id)
            
            # Handle missing file reference
            if location is None:
                logger.warning(f"Missing file reference for {file_id.message_id}, attempting to refresh")
                refreshed_file_id = await self.refresh_file_reference(file_id)
                if refreshed_file_id:
                    location = await self.get_location(refreshed_file_id)
                    file_id = refreshed_file_id
                
                if location is None:
                    logger.error(f"Failed to get location for {file_id.message_id} even after refreshing file reference")
                    raise FileNotFound(f"File location not found after refresh for {file_id.message_id}")
            
            max_retries = 5
            
            while current_part <= part_count:
                try:
                    r = await media_session.send(
                        raw.functions.upload.GetFile(
                            location=location, offset=offset, limit=chunk_size
                        ),
                        timeout=Var.TIMEOUT
                    )
                except FloodWait as e:
                    logger.warning(f"FloodWait encountered: waiting for {e.value} seconds.")
                    await asyncio.sleep(e.value + 1)
                    continue
                except (FileReferenceExpired, FileReferenceInvalid) as e:
                    logger.warning(f"File reference for {file_id.message_id} expired/invalid: {e}. Attempting refresh.")
                    refreshed_file_id = await self.refresh_file_reference(file_id)
                    if refreshed_file_id:
                        file_id = refreshed_file_id # Update file_id
                        location = await self.get_location(file_id)
                        if location is None:
                            logger.error(f"Failed to get location for {file_id.message_id} after file reference refresh.")
                            raise FileNotFoundError(f"File location not found after refresh for {file_id.message_id}")
                        logger.info(f"Successfully refreshed file reference for {file_id.message_id} and got new location.")
                        retry_count = 0
                        continue
                    else:
                        logger.error(f"Failed to refresh file reference for {file_id.message_id}.")
                        raise FileNotFoundError(f"Failed to refresh file reference for {file_id.message_id}")
                except asyncio.TimeoutError as e: # Specific handling for asyncio.TimeoutError
                    retry_count += 1
                    logger.warning(f"TimeoutError during file fetch for {file_id.message_id} (attempt {retry_count}/{max_retries}): {e}")
                    if retry_count >= max_retries:
                        logger.error(f"Max retries reached for TimeoutError on {file_id.message_id}: {e}")
                        raise TimeoutError(f"Max retries for TimeoutError on {file_id.message_id} after {max_retries} attempts.")
                    await asyncio.sleep(1 * (2 ** (retry_count - 1)))
                    continue
                except RPCError as e:
                    retry_count += 1
                    logger.warning(f"RPC Error during file fetch for {file_id.message_id} (attempt {retry_count}/{max_retries}): {e}")
                    if retry_count >= max_retries:
                        logger.error(f"Max retries reached for RPCError on {file_id.message_id}: {e}")
                        raise RPCError(f"Max retries for RPCError on {file_id.message_id}: {e}")
                    await asyncio.sleep(1 * (2 ** (retry_count - 1)))
                    continue
                except ConnectionError as e:
                    retry_count += 1
                    logger.warning(f"Connection error during file fetch for {file_id.message_id} (attempt {retry_count}/{max_retries}): {e}")
                    if retry_count >= max_retries:
                        logger.error(f"Max retries reached for ConnectionError on {file_id.message_id}: {e}")
                        raise ConnectionError(f"Max retries for ConnectionError on {file_id.message_id}: {e}")
                    await asyncio.sleep(1)  # Brief pause before retry
                    continue
                except Exception as e:
                    logger.error(f"Unexpected error during file fetch for {file_id.message_id}: {str(e)}", exc_info=True)
                    raise
                
                if isinstance(r, raw.types.upload.File):
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
                    retry_count = 0  # Reset retry count after success

                    if current_part > part_count:
                        break
        except (AttributeError) as e:
            logger.error(f"Error while yielding file for {file_id.message_id if 'file_id' in locals() else 'unknown file'} (AttributeError): {e}", exc_info=True)
            raise
        finally:
            work_loads[index] -= 1
    
    async def clean_cache(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.clean_timer)
                
                async with self.cache_lock:
                    # Instead of clearing everything, we could selectively clean
                    # based on timestamps if we tracked them
                    self.cached_file_ids.clear()
                    
                    # Clean file reference cache based on age
                    now = time.time()
                    expired_refs = [
                        key for key, value in self.file_references_cache.items()
                        if now - value["timestamp"] > 3600  # 1 hour expiry
                    ]
                    for key in expired_refs:
                        self.file_references_cache.pop(key, None)
                
                if expired_refs:
                    logger.debug(f"Cache cleaned. Removed {len(expired_refs)} expired file references.")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cache cleaning task: {e}")
                await asyncio.sleep(60)  # Wait before retrying
    
    async def cleanup_media_sessions(self) -> None:
        while True:
            try:
                await asyncio.sleep(1800)  # 30 minutes
                
                async with self.media_sessions_lock:
                    now = time.time()
                    sessions_to_remove = []
                    
                    for dc_id, session in list(self.client.media_sessions.items()):
                        if hasattr(session, 'last_used') and now - session.last_used > 1800:  # 30 minutes
                            sessions_to_remove.append(dc_id)
                    
                    for dc_id in sessions_to_remove:
                        try:
                            await self.client.media_sessions[dc_id].stop()
                            self.client.media_sessions.pop(dc_id, None)
                        except Exception as e:
                            logger.error(f"Error stopping media session for DC {dc_id}: {e}")
                
                if sessions_to_remove:
                    logger.debug(f"Cleaned up {len(sessions_to_remove)} inactive media sessions")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in media session cleaning task: {e}")
                await asyncio.sleep(60)  # Wait before retrying
