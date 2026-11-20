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
    # The repo-root config.env sets HAS_SSL=False on some machines; the URL
    # expectations in this file assume the default (True).
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
    """The ConfigError message, or '' when the configuration parsed."""
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
    """The whole point: one restart, not one per bad value."""
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
    # A value that is not a number used to raise a bare ValueError naming no key.
    ({"API_ID": "abc"}, "API_ID must be a whole number"),
    ({"PORT": "8080 # port"}, "PORT must be a whole number"),

    # 0 workers raises inside Client.__init__, before startup could report it.
    ({"WORKERS": "0"}, "WORKERS must be at least 1"),

    # A negative threshold makes the FloodWait guard unreachable.
    ({"SLEEP_THRESHOLD": "-1"}, "SLEEP_THRESHOLD must be at least 0"),

    # <= 0 busy-loops the keepalive against the public URL.
    ({"PING_INTERVAL": "0"}, "PING_INTERVAL must be at least 1"),

    ({"PORT": "70000"}, "PORT must be at most 65535"),

    # A positive channel id resolves to a user chat; every upload then fails.
    ({"BIN_CHANNEL": "123456"}, "BIN_CHANNEL must be a channel id"),

    ({"MAX_BATCH_FILES": "0"}, "MAX_BATCH_FILES must be at least 1"),
    ({"TOKEN_TTL_HOURS": "0"}, "TOKEN_TTL_HOURS must be at least 1"),
])
def test_malformed_values_name_their_variable(case):
    overrides, expected = case
    text = problems(overrides)
    assert expected in text, f"expected {expected!r}, got {text!r}"


def test_an_unusable_force_channel_only_turns_the_feature_off():
    """It is optional: refusing to start over it would break a working bot."""
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
    """0 or positive parses as an int but is not a channel: force-subscribe would silently do the wrong thing."""
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
    """An unrecognised value is a problem, not a silent False."""
    text = problems({"HAS_SSL": "sometimes"})
    assert "HAS_SSL must be True or False" in text


def test_platform_hosts_are_normalised_like_any_other_value():
    """Render supplies a full URL; using it raw gave https://https://host/."""
    from Thunder.utils.public_host import resolve_host

    cases = {
        # Render's RENDER_EXTERNAL_URL is a complete URL.
        "https://my-service.onrender.com": "my-service.onrender.com",
        # Heroku and Fly give a bare name, which is joined to their suffix.
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
    """An explicit public port must win: behind a proxy it is not the bind port."""
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
])
def test_an_fqdn_with_a_non_numeric_port_is_a_problem(value):
    """normalise_host keeps the whole string as host here, so FQDN becomes ``example.com:abc`` and every link 404s."""
    text = problems({"FQDN": value})
    assert text, f"FQDN {value!r} must not parse"
    assert "FQDN" in text


def test_an_fqdn_with_a_numeric_port_or_brackets_still_parses():
    assert problems({"FQDN": "example.com:8080"}) == ""
    assert problems({"FQDN": "[::1]"}) == ""


def test_the_placeholder_config_still_imports():
    """config_sample.env must import; it leaves required values empty, so this
    only checks that nothing else is rejected.
    """
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
