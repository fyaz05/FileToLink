# Thunder/utils/config_parser.py

import os


class TokenParser:
    def __init__(self):
        self.tokens: dict[int, str] = {}

    def parse_from_env(self) -> dict[int, str]:
        # sort key cannot raise: digit filter yields "" at worst, `or 0` -> int
        multi_tokens = {
            key: value.strip()
            for key, value in os.environ.items()
            if key.startswith("MULTI_TOKEN") and value.strip()
        }

        if not multi_tokens:
            return {}

        sorted_tokens = sorted(
            multi_tokens.items(),
            key=lambda item: int("".join(filter(str.isdigit, item[0])) or 0),
        )

        self.tokens = {index + 1: token for index, (_, token) in enumerate(sorted_tokens)}

        return self.tokens
