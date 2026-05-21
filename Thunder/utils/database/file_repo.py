import datetime
from typing import Any

from Thunder.utils.file_record import FileRecord
from Thunder.utils.logger import logger


class _FileRepo:
    files_col: object

    async def get_file_by_unique_id(self, file_unique_id: str) -> FileRecord | None:
        try:
            return await self.files_col.find_one({"file_unique_id": file_unique_id})
        except Exception as e:
            logger.error(f"Error getting file by unique_id {file_unique_id}: {e}", exc_info=True)
            return None

    async def get_file_by_hash(
        self,
        public_hash: str,
        *,
        raise_on_error: bool = True
    ) -> FileRecord | None:
        try:
            return await self.files_col.find_one({"public_hash": public_hash})
        except Exception as e:
            logger.error(f"Error getting file by hash {public_hash}: {e}", exc_info=True)
            if raise_on_error:
                raise
            return None

    async def get_file_by_message_id(self, canonical_message_id: int) -> FileRecord | None:
        try:
            return await self.files_col.find_one({"canonical_message_id": canonical_message_id})
        except Exception as e:
            logger.error(
                f"Error getting file by message_id {canonical_message_id}: {e}",
                exc_info=True
            )
            return None

    async def create_file_record(self, file_record: FileRecord) -> None:
        try:
            await self.files_col.insert_one(file_record)
        except Exception as e:
            logger.error(
                f"Error creating canonical file record for {file_record.get('file_unique_id')}: {e}",
                exc_info=True
            )
            raise

    async def replace_file_record(self, file_record: FileRecord) -> None:
        try:
            await self.files_col.replace_one(
                {"file_unique_id": file_record["file_unique_id"]},
                file_record,
                upsert=True
            )
        except Exception as e:
            logger.error(
                f"Error replacing canonical file record for {file_record.get('file_unique_id')}: {e}",
                exc_info=True
            )
            raise

    async def touch_file_record(
        self,
        public_hash: str,
        *,
        reused: bool = False,
        raise_on_error: bool = False
    ) -> bool:
        try:
            update_doc: dict[str, Any] = {
                "$set": {"last_seen_at": datetime.datetime.now(datetime.UTC)},
                "$inc": {"seen_count": 1}
            }
            if reused:
                update_doc["$inc"]["reuse_count"] = 1
            await self.files_col.update_one({"public_hash": public_hash}, update_doc)
            return True
        except Exception as e:
            logger.error(f"Error touching canonical file {public_hash}: {e}", exc_info=True)
            if raise_on_error:
                raise
            return False

    async def update_file_id(
        self,
        public_hash: str,
        file_id: str,
        *,
        raise_on_error: bool = False
    ) -> bool:
        try:
            await self.files_col.update_one(
                {"public_hash": public_hash},
                {
                    "$set": {
                        "file_id": file_id,
                        "last_seen_at": datetime.datetime.now(datetime.UTC)
                    }
                }
            )
            return True
        except Exception as e:
            logger.error(f"Error updating file_id for {public_hash}: {e}", exc_info=True)
            if raise_on_error:
                raise
            return False
