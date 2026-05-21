import datetime

from Thunder.utils.logger import logger


class _TokenRepo:
    token_col: object
    authorized_users_col: object

    async def save_main_token(
        self,
        user_id: int,
        token_value: str,
        expires_at: datetime.datetime,
        created_at: datetime.datetime,
        activated: bool
    ) -> None:
        try:
            await self.token_col.update_one(
                {"user_id": user_id, "token": token_value},
                {"$set": {
                    "expires_at": expires_at,
                    "created_at": created_at,
                    "activated": activated
                    }
                },
                upsert=True
            )
            if len(token_value) <= 8:
                masked = "****"
            elif len(token_value) <= 16:
                masked = f"{token_value[:3]}...{token_value[-3:]}"
            else:
                masked = f"{token_value[:4]}...{token_value[-4:]}"
            logger.debug(f"Saved main token {masked} for user {user_id} with activated status {activated}.")
        except Exception as e:
            logger.error(f"Error saving main token for user {user_id}: {e}", exc_info=True)
            raise

    async def is_user_authorized(self, user_id: int) -> bool:
        try:
            user = await self.authorized_users_col.find_one({'user_id': user_id}, {'_id': 1})
            return bool(user)
        except Exception as e:
            logger.error(f"Error in is_user_authorized for user {user_id}: {e}", exc_info=True)
            return False
