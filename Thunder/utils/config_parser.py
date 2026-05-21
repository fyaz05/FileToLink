# Thunder/utils/config_parser.py

import os

from Thunder.utils.logger import logger


class TokenParser:
    def __init__(self, config_file: str | None = None):
        self.tokens: dict[int, str] = {}
        self.config_file = config_file

    def parse_from_env(self) -> dict[int, str]:
        try:
            multi_tokens = {
                key: value.strip()
                for key, value in os.environ.items()
                if key.startswith("MULTI_TOKEN") and value.strip()
            }

            if not multi_tokens:
                return {}

            sorted_tokens = sorted(
                multi_tokens.items(),
                key=lambda item: int(''.join(filter(str.isdigit, item[0])) or 0)
            )

            self.tokens = {
                index + 1: token
                for index, (_, token) in enumerate(sorted_tokens)
            }

            return self.tokens
        except Exception as e:
            logger.error(f"Error in parse_from_env: {e}", exc_info=True)
            return {}
