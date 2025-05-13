from typing import Dict
import config
from Thunder.utils.logger import logger

class TokenParser:
    """Parse bot tokens from configuration for multi-client setup."""
    
    def parse_from_config(self) -> Dict[int, str]:
        """Get bot tokens from config and map them to client IDs."""
        multi_tokens = getattr(config, "MULTI_BOT_TOKENS", [])

        if not multi_tokens:
            logger.warning("No additional bot tokens found in configuration.")
            return {}

        tokens = {index + 1: token for index, token in enumerate(multi_tokens)}
        
        logger.info(f"Found {len(tokens)} additional bot tokens in configuration.")
        return tokens
