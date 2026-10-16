"""Consume() status ladder: corrupt/expired tokens read "invalid", never "already"."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import Thunder.utils.tokens as tokens_module
from Thunder.utils.tokens import consume


class _FakeTokenCol:
    """Records every CAS filter it is handed.

    ``cas_result`` is what the atomic update returns (None = the CAS lost the
    race). ``find_one`` returns the pre-check row, or the scripted post-CAS doc
    when a projection arg is passed.
    """

    def __init__(self, doc, post_cas_doc=None):
        self._doc = doc
        self._post_cas_doc = post_cas_doc
        self.cas_filters: list[dict] = []
        self.cas_result = None

    async def find_one(self, *args, **_kwargs):
        if self._post_cas_doc is not None and len(args) > 1:
            return self._post_cas_doc
        return self._doc

    async def find_one_and_update(self, filter_, *_args, **_kwargs):
        self.cas_filters.append(filter_)
        return self.cas_result


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


@pytest.mark.unit
async def test_naive_future_expiry_treated_as_utc(monkeypatch):
    """Legacy naive datetimes are UTC instants: future stays activatable."""
    col = _FakeTokenCol(
        {
            "token": "t",
            "user_id": 7,
            "activated": False,
            "expires_at": datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
        }
    )
    col.cas_result = {"activated": True}
    monkeypatch.setattr(tokens_module, "db", SimpleNamespace(token_col=col))
    monkeypatch.setattr(tokens_module.Var, "TOKEN_TTL_HOURS", 7)
    monkeypatch.setattr(tokens_module.flags, "invalidate", lambda *keys: None)
    status, _hours = await consume("t", 7)
    assert status == "ok"


@pytest.mark.unit
async def test_naive_past_expiry_is_invalid(monkeypatch):
    doc = {
        "token": "t",
        "user_id": 7,
        "activated": False,
        "expires_at": datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1),
    }
    monkeypatch.setattr(tokens_module, "db", SimpleNamespace(token_col=_FakeTokenCol(doc)))
    status, hours = await consume("t", 7)
    assert (status, hours) == ("invalid", 0.0)


@pytest.mark.unit
async def test_non_datetime_expiry_is_invalid_not_500(monkeypatch):
    """Corrupt expires_at shapes fail closed instead of raising TypeError."""
    doc = {"token": "t", "user_id": 7, "activated": False, "expires_at": "tomorrow-ish"}
    monkeypatch.setattr(tokens_module, "db", SimpleNamespace(token_col=_FakeTokenCol(doc)))
    status, hours = await consume("t", 7)
    assert (status, hours) == ("invalid", 0.0)


@pytest.mark.unit
async def test_token_for_other_user_is_wrong_user(monkeypatch):
    doc = {
        "token": "t",
        "user_id": 7,
        "activated": False,
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    monkeypatch.setattr(tokens_module, "db", SimpleNamespace(token_col=_FakeTokenCol(doc)))
    status, hours = await consume("t", 999)
    assert (status, hours) == ("wrong_user", 0.0)


@pytest.mark.unit
async def test_ok_path_activates_and_invalidates_flags(monkeypatch):
    col = _FakeTokenCol(
        {
            "token": "t",
            "user_id": 7,
            "activated": False,
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
        }
    )
    col.cas_result = {"activated": True}
    invalidated: list = []
    monkeypatch.setattr(tokens_module, "db", SimpleNamespace(token_col=col))
    monkeypatch.setattr(tokens_module.Var, "TOKEN_TTL_HOURS", 7)
    monkeypatch.setattr(tokens_module.flags, "invalidate", lambda *keys: invalidated.append(keys))
    status, hours = await consume("t", 7)
    assert status == "ok"
    assert hours == 7.0  # round(ttl_hours, 1)
    assert (("allowed", 7), ("token_ok", 7)) in invalidated


@pytest.mark.unit
async def test_cas_predicate_carries_the_whole_guarded_update(monkeypatch):
    """The atomicity lives entirely in this filter, not in the Python around
    it: a concurrent second activation, a wrong user or a token that expired
    between the pre-check and the write must simply not match. This used to be
    invisible here, and only the Docker-backed integration tier could catch a
    dropped clause.
    """
    col = _FakeTokenCol(
        {
            "token": "t",
            "user_id": 7,
            "activated": False,
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
        }
    )
    col.cas_result = {"activated": True}
    monkeypatch.setattr(tokens_module, "db", SimpleNamespace(token_col=col))
    monkeypatch.setattr(tokens_module.Var, "TOKEN_TTL_HOURS", 7)
    monkeypatch.setattr(tokens_module.flags, "invalidate", lambda *keys: None)

    assert (await consume("t", 7))[0] == "ok"

    assert len(col.cas_filters) == 1
    flt = col.cas_filters[0]
    assert flt["token"] == "t"
    assert flt["user_id"] == 7
    # refuses to match an already-activated token: the double-activation guard
    assert flt["activated"] == {"$ne": True}
    # refuses to match a token that expired between pre-check and CAS
    assert isinstance(flt["expires_at"], dict) and "$gt" in flt["expires_at"]


@pytest.mark.unit
async def test_cas_is_never_reached_for_expired_or_foreign_tokens(monkeypatch):
    """The pre-check must short-circuit before any write is attempted."""
    for doc in (
        {"token": "t", "user_id": 7, "activated": True},  # already activated
        {
            "token": "t",
            "user_id": 7,
            "activated": False,
            "expires_at": datetime.now(UTC) - timedelta(minutes=1),
        },  # expired
    ):
        col = _FakeTokenCol(doc)
        monkeypatch.setattr(tokens_module, "db", SimpleNamespace(token_col=col))
        await consume("t", 7)
        assert col.cas_filters == [], f"CAS attempted for {doc}"
