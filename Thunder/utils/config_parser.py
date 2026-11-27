"""Collect MULTI_TOKEN<digits> into ordered slots, retaining env names for diagnostics."""

import os
import re
from typing import Dict, List, Tuple
from Thunder.utils.logger import logger

KEY_PATTERN = re.compile(r"^MULTI_TOKEN(\d+)$")


class TokenParser:
    def __init__(self):
        self.tokens: Dict[int, Tuple[str, str]] = {}
        self.warnings: List[str] = []

    def parse_from_env(self) -> Dict[int, Tuple[str, str]]:
        try:
            found: List[Tuple[int, str, str]] = []
            for key, value in os.environ.items():
                if not key.startswith("MULTI_TOKEN"):
                    continue
                token = value.strip()
                if not token:
                    continue
                match = KEY_PATTERN.match(key)
                if not match:
                    suggested = "MULTI_TOKEN1"
                    digits = "".join(filter(str.isdigit, key))
                    if digits:
                        suggested = f"MULTI_TOKEN{digits}"
                    self.warnings.append(f"{key} is invalid and ignored. Rename it to {suggested}.")
                    continue
                found.append((int(match.group(1)), key, token))

            if not found:
                return {}

            found.sort(key=lambda item: item[0])
            self.tokens = {
                index + 1: (key, token)
                for index, (_number, key, token) in enumerate(found)
            }
            duplicates = {
                self.tokens[slot][1] for slot in self.tokens
            }
            if len(duplicates) < len(self.tokens):
                self.warnings.append("Duplicate MULTI_TOKEN values count one bot twice. Remove the duplicate.")
            return self.tokens
        except Exception as e:
            logger.error(f"Error in parse_from_env: {e}", exc_info=True)
            return {}
