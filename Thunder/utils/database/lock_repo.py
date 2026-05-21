import datetime

from pymongo.errors import DuplicateKeyError

from Thunder.utils.logger import logger


class _LockRepo:
    file_ingest_locks_col: object

    async def acquire_file_ingest_claim(
        self,
        file_unique_id: str,
        *,
        ttl_seconds: int = 60
    ) -> bool:
        now = datetime.datetime.now(datetime.UTC)
        claim_fields = {
            "created_at": now,
            "expires_at": now + datetime.timedelta(seconds=ttl_seconds)
        }
        try:
            await self.file_ingest_locks_col.insert_one({
                "_id": file_unique_id,
                **claim_fields
            })
            return True
        except DuplicateKeyError:
            try:
                result = await self.file_ingest_locks_col.find_one_and_update(
                    {
                        "_id": file_unique_id,
                        "$or": [
                            {"expires_at": {"$lte": now}},
                            {"expires_at": {"$exists": False}}
                        ]
                    },
                    {
                        "$set": claim_fields
                    },
                    return_document=False
                )
                return bool(result)
            except Exception as e:
                logger.error(f"Error updating ingest claim for {file_unique_id}: {e}", exc_info=True)
                raise
        except Exception as e:
            logger.error(f"Error acquiring ingest claim for {file_unique_id}: {e}", exc_info=True)
            raise

    async def release_file_ingest_claim(self, file_unique_id: str) -> bool:
        try:
            await self.file_ingest_locks_col.delete_one({"_id": file_unique_id})
            return True
        except Exception as e:
            logger.error(f"Error releasing ingest claim for {file_unique_id}: {e}", exc_info=True)
            return False

    async def is_file_ingest_claim_active(self, file_unique_id: str) -> bool:
        try:
            claim = await self.file_ingest_locks_col.find_one(
                {
                    "_id": file_unique_id,
                    "expires_at": {"$gt": datetime.datetime.now(datetime.UTC)}
                },
                {"_id": 1}
            )
            return bool(claim)
        except Exception as e:
            logger.error(f"Error checking ingest claim for {file_unique_id}: {e}", exc_info=True)
            raise
