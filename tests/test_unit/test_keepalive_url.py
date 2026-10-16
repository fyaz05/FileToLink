"""Keepalive probe target -- the historical bug here was probing GitHub."""

import pytest

from Thunder.utils import keepalive
from Thunder.vars import Var


@pytest.mark.unit
def test_no_host_pings_local_health(monkeypatch):
    monkeypatch.delenv("KEEPALIVE_HOST", raising=False)
    monkeypatch.setattr(Var, "BIND_ADDRESS", "0.0.0.0")
    monkeypatch.setattr(Var, "PORT", 8080)

    assert keepalive._health_url() == "http://127.0.0.1:8080/health"


@pytest.mark.unit
def test_full_url_is_used_verbatim(monkeypatch):
    monkeypatch.setenv("KEEPALIVE_HOST", "https://bot.example.com/")
    assert keepalive._health_url() == "https://bot.example.com/health"


@pytest.mark.unit
def test_host_only_value_assumes_https(monkeypatch):
    """Following config_sample.env used to build http://host:8080/health,
    which cannot reach a PaaS app served over https on 443."""
    monkeypatch.setenv("KEEPALIVE_HOST", "bot.onrender.com")
    assert keepalive._health_url() == "https://bot.onrender.com/health"


@pytest.mark.unit
def test_explicit_http_scheme_is_respected(monkeypatch):
    monkeypatch.setenv("KEEPALIVE_HOST", "http://127.0.0.1:8080")
    assert keepalive._health_url() == "http://127.0.0.1:8080/health"
