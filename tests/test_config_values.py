"""Every config problem is reported in one run.

Cases run in a subprocess because a bad value kills the import.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

GOOD = {
    "API_ID": "123456",
    "API_HASH": "0123456789abcdef0123456789abcdef",
    "BOT_TOKEN": "123456:AA" + "x" * 30,
    "BIN_CHANNEL": "-1001234567890",
    "DATABASE_URL": "mongodb://localhost:27017/thunder_test",
    "OWNER_ID": "123456",
    # URL expectations use the default HAS_SSL=True.
    "HAS_SSL": "True",
}


def run(overrides, probe="import Thunder.vars; print('OK')"):
    env = {**os.environ, **GOOD, **overrides}
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "-c", probe], cwd=REPO, env=env,
        capture_output=True, text=True, timeout=120,
    )


def problems(overrides):
    result = run(overrides, "import Thunder.vars")
    if result.returncode == 0:
        return ""
    assert "ConfigError" in result.stderr or "ConfigError" in result.stdout, result.stderr[-800:]
    marker = "ConfigError: "
    text = result.stderr.split(marker, 1)[-1]
    return text.splitlines()[0]


def test_a_good_configuration_parses():
    result = run({})
    assert result.returncode == 0, result.stderr[-800:]
    assert "OK" in result.stdout


def test_every_problem_is_reported_in_one_run():
    text = problems({
        "API_ID": "abc",
        "API_HASH": "",
        "BOT_TOKEN": "",
        "BIN_CHANNEL": "not-an-id",
        "DATABASE_URL": "",
    })
    for expected in ("API_ID", "API_HASH", "BOT_TOKEN", "BIN_CHANNEL", "DATABASE_URL"):
        assert expected in text, f"{expected} was not reported: {text}"
    assert text.count(";") >= 4, text


@pytest.mark.parametrize("case", [
    # Reject non-numeric input with the key.
    ({"API_ID": "abc"}, "API_ID must be a whole number"),
    ({"PORT": "8080 # port"}, "PORT must be a whole number"),

    # Reject before Client construction.
    ({"WORKERS": "0"}, "WORKERS must be at least 1"),

    # Keep FloodWait handling reachable.
    ({"SLEEP_THRESHOLD": "-1"}, "SLEEP_THRESHOLD must be at least 0"),

    # Avoid a busy loop.
    ({"PING_INTERVAL": "0"}, "PING_INTERVAL must be at least 1"),

    ({"PORT": "70000"}, "PORT must be at most 65535"),

    # Positive IDs resolve to users.
    ({"BIN_CHANNEL": "123456"}, "BIN_CHANNEL must be a channel id"),

    ({"MAX_BATCH_FILES": "0"}, "MAX_BATCH_FILES must be at least 1"),
    ({"TOKEN_TTL_HOURS": "0"}, "TOKEN_TTL_HOURS must be at least 1"),
])
def test_malformed_values_name_their_variable(case):
    overrides, expected = case
    text = problems(overrides)
    assert expected in text, f"expected {expected!r}, got {text!r}"


@pytest.mark.parametrize("uri", [
    "http://host",
    "mongodb://localhost:bad",
    "mongodb://[::1",
    "mongodb://user:pass@",
    "mongodb://",
])
def test_constructor_time_database_uri_errors_use_the_config_report(uri):
    result = run({"DATABASE_URL": uri}, "import Thunder.utils.database")
    assert result.returncode != 0
    assert "ConfigError: DATABASE_URL is malformed" in result.stderr, result.stderr[-800:]


def test_database_uri_errors_are_aggregated_with_other_parse_errors():
    text = problems({"FQDN": "example.com/base", "DATABASE_URL": "http://host"})
    assert "FQDN" in text
    assert "DATABASE_URL is malformed" in text


def test_an_unusable_force_channel_only_turns_the_feature_off():
    probe = (
        "import Thunder.vars as v;"
        "print('PROBLEM', bool(v.Var.FORCE_CHANNEL_PROBLEM));"
        "print('OFF', v.Var.FORCE_CHANNEL_ID is None)"
    )
    result = run({"FORCE_CHANNEL_ID": "@mychannel"}, probe)
    assert result.returncode == 0, "startup must not abort: " + result.stderr[-600:]
    assert "PROBLEM True" in result.stdout
    assert "OFF True" in result.stdout


@pytest.mark.parametrize("value", ["0", "5", "-0"])
def test_a_positive_or_zero_force_channel_id_is_flagged(value):
    probe = (
        "import Thunder.vars as v;"
        "print('PROBLEM', bool(v.Var.FORCE_CHANNEL_PROBLEM));"
        "print('OFF', v.Var.FORCE_CHANNEL_ID is None)"
    )
    result = run({"FORCE_CHANNEL_ID": value}, probe)
    assert result.returncode == 0, "the feature must stay optional: " + result.stderr[-600:]
    assert "PROBLEM True" in result.stdout, value
    assert "OFF True" in result.stdout


def test_a_valid_negative_force_channel_id_is_kept():
    probe = (
        "import Thunder.vars as v;"
        "print('PROBLEM', bool(v.Var.FORCE_CHANNEL_PROBLEM));"
        "print('ID', v.Var.FORCE_CHANNEL_ID)"
    )
    result = run({"FORCE_CHANNEL_ID": "-100123456789"}, probe)
    assert result.returncode == 0, result.stderr[-600:]
    assert "PROBLEM False" in result.stdout
    assert "ID -100123456789" in result.stdout


def test_a_bool_that_is_not_recognised_is_an_error_not_a_silent_false():
    text = problems({"HAS_SSL": "sometimes"})
    assert "HAS_SSL must be True or False" in text


def test_platform_hosts_are_normalised_like_any_other_value():
    from Thunder.utils.public_host import resolve_host

    cases = {
        "https://my-service.onrender.com": "my-service.onrender.com",
        "my-app.herokuapp.com": "my-app.herokuapp.com",
        "my-app.fly.dev": "my-app.fly.dev",
    }
    for supplied, expected in cases.items():
        variable = ("RENDER_EXTERNAL_URL" if "onrender" in supplied
                    else "KOYEB_PUBLIC_DOMAIN")
        resolved = resolve_host("", "0.0.0.0", {variable: supplied})
        assert resolved == expected, f"{supplied} -> {resolved}"
        assert "://" not in resolved, "a scheme must never reach the URL builder"


def test_heroku_app_name_is_expanded_to_a_host():
    from Thunder.utils.public_host import resolve_host

    assert resolve_host("", "0.0.0.0", {"HEROKU_APP_NAME": "thunder"}) == "thunder.herokuapp.com"


def test_a_configured_fqdn_beats_the_platform_value():
    from Thunder.utils.public_host import resolve_host

    resolved = resolve_host("bot.example.com", "0.0.0.0",
                            {"KOYEB_PUBLIC_DOMAIN": "other.koyeb.app"})
    assert resolved == "bot.example.com"


def test_a_port_in_fqdn_is_used_not_dropped():
    probe = "from Thunder.vars import Var; print('URL', Var.URL)"
    for overrides, expected in [
        ({"FQDN": "example.com:8443", "NO_PORT": "True"}, "https://example.com:8443/"),
        ({"FQDN": "example.com:8443", "NO_PORT": "False", "PORT": "8080"},
         "https://example.com:8443/"),
        ({"FQDN": "example.com", "NO_PORT": "False", "PORT": "8080"},
         "https://example.com:8080/"),
        ({"FQDN": "example.com", "NO_PORT": "True"}, "https://example.com/"),
    ]:
        result = run(overrides, probe)
        assert result.returncode == 0, result.stderr[-400:]
        assert f"URL {expected}" in result.stdout, f"{overrides} -> {result.stdout!r}"


@pytest.mark.parametrize("value", [
    "example.com:abc",
    "example.com:",
    "a:b:c",
    "example.com:8080:90",
    "::1",
    "example.com/base",
    "https://example.com/base",
    "user@example.com",
    "/",
    "https://",
    "[::1",
    "[::1]abc",
    "[::1]:abc",
    "[not-ipv6]",
    "example.com:²",
    "example.com:" + "9" * 5000,
    "example%zz.com",
    'exam"ple.com',
    "bad..example.com",
    "-bad.example.com",
    "bad-.example.com",
])
def test_an_fqdn_with_a_non_numeric_port_is_a_problem(value):
    text = problems({"FQDN": value})
    assert text, f"FQDN {value!r} must not parse"
    assert "FQDN" in text


def test_a_bind_address_fqdn_yields_to_the_platform_host():
    probe = "from Thunder.vars import Var; print('URL', Var.URL)"
    result = run({"FQDN": "::1", "RENDER_EXTERNAL_URL": "https://real.onrender.com",
                  "NO_PORT": "True"}, probe)
    assert result.returncode == 0, result.stderr[-400:]
    assert "URL https://real.onrender.com/" in result.stdout


def test_an_fqdn_with_a_numeric_port_or_brackets_still_parses():
    assert problems({"FQDN": "example.com:8080"}) == ""
    assert problems({"FQDN": "[::1]"}) == ""
    assert problems({"FQDN": "[::1]:8080"}) == ""
    assert problems({"FQDN": "example.com."}) == ""
    assert problems({"FQDN": "bücher.de"}) == ""


def test_bracketed_ipv6_port_is_not_joined_to_the_bind_port():
    probe = "from Thunder.vars import Var; print('URL', Var.URL)"
    result = run({"FQDN": "[2001:db8::1]:8443", "NO_PORT": "False", "PORT": "8080"}, probe)
    assert result.returncode == 0, result.stderr[-400:]
    assert "URL https://[2001:db8::1]:8443/" in result.stdout


def test_a_discarded_bind_fqdn_does_not_leak_its_port_to_the_platform_host():
    probe = "from Thunder.vars import Var; print('URL', Var.URL)"
    result = run({
        "FQDN": "0.0.0.0:8080",
        "RENDER_EXTERNAL_URL": "https://real.onrender.com",
        "NO_PORT": "True",
    }, probe)
    assert result.returncode == 0, result.stderr[-400:]
    assert "URL https://real.onrender.com/" in result.stdout


def test_a_platform_url_keeps_its_explicit_public_port():
    probe = "from Thunder.vars import Var; print('URL', Var.URL)"
    result = run({
        "FQDN": "",
        "RENDER_EXTERNAL_URL": "https://real.onrender.com:8443",
        "NO_PORT": "True",
    }, probe)
    assert result.returncode == 0, result.stderr[-400:]
    assert "URL https://real.onrender.com:8443/" in result.stdout


@pytest.mark.parametrize("platform_url", [
    "https://real.onrender.com/base",
    "https://::1",
    "https://::",
])
def test_an_invalid_platform_url_is_rejected(platform_url):
    text = problems({
        "FQDN": "",
        "RENDER_EXTERNAL_URL": platform_url,
    })
    assert "RENDER_EXTERNAL_URL" in text
    assert "FQDN" not in text


def test_an_invalid_platform_url_names_the_platform_not_the_bind_fqdn():
    text = problems({
        "FQDN": "0.0.0.0",
        "RENDER_EXTERNAL_URL": "https://real.onrender.com/base",
    })
    assert "RENDER_EXTERNAL_URL" in text
    assert "FQDN '0.0.0.0'" not in text


def test_an_empty_platform_url_falls_back_to_the_bind_address():
    probe = "from Thunder.vars import Var; print('URL', Var.URL)"
    result = run({
        "FQDN": "",
        "RENDER_EXTERNAL_URL": "https://",
        "BIND_ADDRESS": "0.0.0.0",
        "NO_PORT": "True",
    }, probe)
    assert result.returncode == 0, result.stderr[-400:]
    assert "URL https://0.0.0.0/" in result.stdout


def test_the_placeholder_config_still_imports():
    """Load the sample with valid required overrides; check its remaining values."""
    sample = (REPO / "config_sample.env").read_text(encoding="utf-8")
    overrides = {}
    for line in sample.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        overrides[key.strip()] = value.split("#")[0].strip().strip('"')
    overrides.update({"API_ID": "123456", "API_HASH": "0" * 32,
                      "BOT_TOKEN": "1:AA" + "x" * 30,
                      "BIN_CHANNEL": "-1001234567890",
                      "DATABASE_URL": "mongodb://localhost:27017/t"})
    result = run(overrides)
    assert result.returncode == 0, result.stderr[-1200:]
