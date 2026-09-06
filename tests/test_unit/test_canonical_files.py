"""Hash building (L4 dual lengths) + merge precedence (H2 target)."""

import pytest

from Thunder.utils.canonical_files import (
    LEGACY_PUBLIC_HASH_LENGTH,
    PUBLIC_HASH_LENGTH,
    _merge_replacement_record,
    build_public_hash,
)


@pytest.mark.unit
def test_new_hashes_are_32_hex():
    h = build_public_hash("unique-id-1")
    assert len(h) == PUBLIC_HASH_LENGTH == 32
    int(h, 16)  # hex-parseable


@pytest.mark.unit
def test_hash_is_deterministic():
    assert build_public_hash("abc") == build_public_hash("abc")
    assert build_public_hash("abc") != build_public_hash("abd")


@pytest.mark.unit
def test_legacy_length_constant_still_20():
    # L4: the old family must remain representable for dual validation
    assert LEGACY_PUBLIC_HASH_LENGTH == 20


@pytest.mark.unit
def test_merge_keeps_created_at_and_increments_seen():
    existing = {
        "created_at": "2026-01-01",
        "seen_count": 7,
        "reuse_count": 3,
        "first_source_chat_id": -100111,
        "first_source_message_id": 222,
    }
    refreshed = {
        "created_at": "2026-09-01",
        "seen_count": 0,
        "reuse_count": 0,
        "first_source_chat_id": None,
        "first_source_message_id": None,
        "file_unique_id": "x",
    }
    merged = _merge_replacement_record(existing, refreshed)
    assert merged["created_at"] == "2026-01-01"
    assert merged["seen_count"] == 8
    assert merged["reuse_count"] == 3
    assert merged["first_source_chat_id"] == -100111
    assert merged["first_source_message_id"] == 222


@pytest.mark.unit
def test_merge_falls_back_to_refreshed_sources():
    existing = {"seen_count": 0, "reuse_count": 0}
    refreshed = {
        "created_at": "c",
        "seen_count": 0,
        "reuse_count": 0,
        "first_source_chat_id": -1,
        "first_source_message_id": 1,
    }
    merged = _merge_replacement_record(existing, refreshed)
    assert merged["first_source_chat_id"] == -1
    assert merged["first_source_message_id"] == 1


@pytest.mark.unit
def test_merge_preserves_legacy_public_hash():
    """Self-heal replacement must keep the existing public_hash: rewriting a
    legacy 20-char hash to 32-hex would permanently break published links."""
    from Thunder.utils.canonical_files import _merge_replacement_record

    existing = {
        "public_hash": "a" * 20,
        "created_at": "t0",
        "seen_count": 3,
        "reuse_count": 1,
        "first_source_chat_id": 11,
        "first_source_message_id": 22,
    }
    refreshed = {"public_hash": "b" * 32, "created_at": "t1"}
    merged = _merge_replacement_record(existing, refreshed)
    assert merged["public_hash"] == "a" * 20
    assert merged["seen_count"] == 4
    assert merged["reuse_count"] == 1
    assert merged["first_source_chat_id"] == 11
    assert merged["first_source_message_id"] == 22
