# Thunder/utils/config_parser.py

import os
from typing import Dict, Optional
from Thunder.utils.logger import logger


class TokenParser:
    """
    A class to parse multiple bot tokens from environment variables.
    """

    def __init__(self, config_file: Optional[str] = None):
        """
        Initialize the TokenParser.

        Args:
            config_file (Optional[str]): Path to a config file (not used in this implementation).
        """
        self.tokens: Dict[int, str] = {}
        self.config_file = config_file

    def parse_from_env(self) -> Dict[int, str]:
        """
        Parse bot tokens from environment variables.

        Looks for environment variables that start with "MULTI_TOKEN" and maps them
        to client indices.

        Example:
            MULTI_TOKEN1=token_1
            MULTI_TOKEN2=token_2
            ...

        Returns:
            Dict[int, str]: A dictionary mapping client numbers to bot tokens.

        Raises:
            ValueError: If no tokens are found or tokens have invalid formats.
        """
        # Filter environment variables that start with "MULTI_TOKEN"
        multi_tokens = {
            key: value for key, value in os.environ.items() if key.startswith("MULTI_TOKEN")
        }

        if not multi_tokens:
            logger.error("No MULTI_TOKEN environment variables found.")
            raise ValueError("No MULTI_TOKEN environment variables found.")

        # Extract the numeric part of the token key and sort them
        sorted_tokens = sorted(
            multi_tokens.items(),
            key=lambda item: int(''.join(filter(str.isdigit, item[0])) or 0)
        )

        # Map to a dictionary with integer keys starting at 1
        self.tokens = {
            index + 1: token for index, (key, token) in enumerate(sorted_tokens)
        }

        if not self.tokens:
            logger.error("No valid MULTI_TOKEN environment variables found.")
            raise ValueError("No valid MULTI_TOKEN environment variables found.")

        logger.debug(f"Parsed tokens: {self.tokens}")
        return self.tokens
