# Thunder/utils/config_parser.py

import os
from typing import Dict, Optional
from Thunder.utils.error_handling import log_errors

class TokenParser:
    def __init__(self, config_file: Optional[str] = None):
        self.tokens: Dict[int, str] = {}
        self.config_file = config_file
        self._env_cache = None

    @log_errors
    def parse_from_env(self) -> Dict[int, str]:
        if self._env_cache is None:
            self._env_cache = dict(os.environ)
        
        multi_tokens = {
            key: value.strip() 
            for key, value in self._env_cache.items()
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
