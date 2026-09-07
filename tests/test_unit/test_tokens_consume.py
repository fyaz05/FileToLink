"""consume() status ladder (review C-1): corrupt/expired tokens read "invalid", never "already"."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import Thunder.utils.tokens as tokens_module
from Thunder.utils.tokens import consume


class _FakeTokenCol:
    """find_one_and_update always loses the CAS (returns None); find_one returns the
    pre-check row, or the scripted post-CAS doc when a projection arg is passed."""

    def __init__(self, doc, post_cas_doc=None):
        self._doc = doc
        self._post_cas_doc = post_cas_doc

    async def find_one(self, *args, **_kwargs):
        if self._post_cas_doc is not None and len(args) > 1:
            return self._post_cas_doc
        return self._doc

    async def find_one_and_update(self, *_args, **_kwargs):
        return None


@pytest.mark.unit
async def test_corrupt_token_without_expires_at_is_invalid(monkeypatch):
    doc = {"token": "t", "user_id": 7, "activated": False}  # expires_at missing
    monkeypatch.setattr(tokens_module, "db", SimpleNamespace(token_col=_FakeTokenCol(doc)))
    status, hours = await consume("t", 7)
    assert (status, hours) == ("invalid", 0.0)


@pytest.mark.unit
async def test_expired_unactivated_token_is_invalid_not_already(monkeypatch):
    doc = {
        "token": "t",
        "user_id": 7,
        "activated": False,
        "expires_at": datetime.now(UTC) - timedelta(hours=1),
    }
    monkeypatch.setattr(tokens_module, "db", SimpleNamespace(token_col=_FakeTokenCol(doc)))
    status, hours = await consume("t", 7)
    assert (status, hours) == ("invalid", 0.0)


@pytest.mark.unit
async def test_cas_loss_to_concurrent_winner_is_already(monkeypatch):
    doc = {
        "token": "t",
        "user_id": 7,
        "activated": False,
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    col = _FakeTokenCol(doc, post_cas_doc={"activated": True})
    monkeypatch.setattr(tokens_module, "db", SimpleNamespace(token_col=col))
    status, _hours = await consume("t", 7)
    assert status == "already"


@pytest.mark.unit
async def test_cas_loss_to_expiry_is_invalid(monkeypatch):
    doc = {
        "token": "t",
        "user_id": 7,
        "activated": False,
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    col = _FakeTokenCol(doc, post_cas_doc={"activated": False})
    monkeypatch.setattr(tokens_module, "db", SimpleNamespace(token_col=col))
    status, _hours = await consume("t", 7)
    assert status == "invalid"
