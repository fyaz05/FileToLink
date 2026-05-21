from __future__ import annotations

import datetime
from typing import TypedDict


class FileRecord(TypedDict, total=False):
    file_unique_id: str
    public_hash: str
    canonical_message_id: int
    file_id: str | None
    file_name: str | None
    mime_type: str
    file_size: int
    media_type: str
    first_source_chat_id: int | None
    first_source_message_id: int | None
    created_at: datetime.datetime
    last_seen_at: datetime.datetime
    seen_count: int
    reuse_count: int
