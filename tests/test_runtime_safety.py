"""Verify import hygiene, database name sanitization, and pre-startup /status handling."""
import pathlib
import subprocess
import sys

import pytest

from Thunder.vars import Var

REPO = pathlib.Path(__file__).resolve().parent.parent


def test_file_properties_importable_first():
    code = "from Thunder.utils.file_properties import get_media; print('ok')"
    result = subprocess.run([sys.executable, "-c", code], cwd=REPO,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-500:]


def test_server_package_exports_routes_lazily():
    from Thunder.server import routes
    assert len(list(routes)) > 0


def test_server_package_builds_an_app():
    import asyncio

    from Thunder.server import web_server

    app = asyncio.run(web_server())
    assert len(list(app.router.routes())) > 0


class TestDatabaseName:
    def test_derived_name_has_no_spaces(self):
        assert " " not in Var.DATABASE_NAME

    def test_pymongo_accepts_derived_name(self):
        from pymongo.database_shared import _check_name

        _check_name(Var.DATABASE_NAME)

    def test_database_is_constructed_with_the_derived_name(self):
        import inspect

        from Thunder.utils import database as database_module

        source = inspect.getsource(database_module)
        assert "Var.DATABASE_NAME" in source
        assert "Database(Var.DATABASE_URL, Var.NAME)" not in source

    @pytest.mark.parametrize("raw,expected_valid", [
        ("Thunder test", True),
        ("My Bot / v2", True),
        ("bot.final", True),
        ('weird"name', True),
        ("$dollar$", True),
        ("a*b<c>d:e|f?g", True),
        ("   ", True),
        ("", True),
    ])
    def test_sanitizer_output_is_always_legal(self, raw, expected_valid):
        from pymongo.database_shared import _check_name

        derived = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in raw.strip()
        ) or "Thunder"
        _check_name(derived)
        assert bool(derived) is expected_valid


def test_status_reports_null_username_before_startup():
    import asyncio

    import aiohttp
    from aiohttp import web

    from Thunder.bot import StreamBot
    from Thunder.server import stream_routes as sr

    async def run():
        had = hasattr(StreamBot, "username")
        previous = getattr(StreamBot, "username", None)
        if had:
            del StreamBot.username
        try:
            app = web.Application()
            app.add_routes(sr.routes)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"http://127.0.0.1:{port}/status") as r:
                        assert r.status == 200
                        payload = await r.json()
                        assert payload["telegram_bot"]["username"] is None
            finally:
                await runner.cleanup()
        finally:
            if had:
                StreamBot.username = previous

    asyncio.run(run())


def test_status_exposes_the_startup_check_counts():
    """/status exposes cached counts only: no live checks or config identifiers."""
    import asyncio

    import aiohttp
    from aiohttp import web

    from Thunder.server.stream_routes import (
        CONFIGURATION_STATUS_KEY,
        configuration_summary,
        routes,
    )

    class FakeReport:
        failures = []
        passed = [1, 2, 3]
        warnings = [1]
        skipped = []

    async def run():
        app = web.Application()
        app.add_routes(routes)
        app[CONFIGURATION_STATUS_KEY] = configuration_summary(FakeReport())
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{port}/status") as r:
                    assert r.status == 200, "a healthcheck must not see a failure"
                    payload = await r.json()
        finally:
            await runner.cleanup()

        configuration = payload["configuration"]
        assert configuration == {"startup": True, "passed": 3, "warnings": 1, "failures": 0}
        for leak in ("BIN_CHANNEL", "mongodb", "@", "-100"):
            assert leak not in str(configuration), f"/status leaks {leak}"

    asyncio.run(run())


def test_update_script_parses():
    result = subprocess.run(
        [sys.executable, "-c",
         "import ast,pathlib;ast.parse(pathlib.Path('update.py').read_text(encoding='utf-8'))"],
        cwd=REPO, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_install_requirements_does_not_assume_pip_exists():
    """uv venv has no pip; try pip, then uv pip."""
    source = (REPO / "update.py").read_text(encoding="utf-8")
    installer = source.split("def _install_requirements", 1)[1].split("\ndef ", 1)[0]

    assert "-m\", \"pip\"" in installer or "'-m', 'pip'" in installer, \
        "pip should still be tried first: it is present in the common case"
    assert '"uv"' in installer and "--python" in installer, \
        "a venv created by uv venv has no pip, so uv must be tried too"

    assert "--seed" in installer, \
        "the warning must tell the operator how to get a pip-bearing environment"


def _sandbox_skip(module_name="update_helpers"):
    import importlib.util

    path = REPO / "tests" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"_sandbox_skip_{module_name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SKIP


def test_sandbox_copies_exclude_generated_state_and_nothing_else():
    """Exercise SKIP itself; source checks can match non-excluding patterns."""
    names = ["Thunder", "tests", "update.py", "requirements.txt", "config.env",
             ".venv", "venv", ".pytest_cache", ".ruff_cache", "logs", "__pycache__"]
    ignored = set(_sandbox_skip()(str(REPO), list(names)))

    for generated in (".venv", "venv", ".pytest_cache", ".ruff_cache"):
        assert generated in ignored, f"{generated}/ is copied into every sandbox"
    for real in ("Thunder", "tests", "update.py", "requirements.txt"):
        assert real not in ignored, f"real content would be excluded: {real}"

    for name in ("test_update_safety", "test_update_recovery"):
        source = (REPO / "tests" / f"{name}.py").read_text(encoding="utf-8")
        assert "from update_helpers import" in source, \
            f"{name} no longer copies a sandbox using the shared patterns"


def test_thunder_script_resolves_python_from_its_own_directory():
    script = (REPO / "thunder.sh").read_text(encoding="utf-8")
    assert "BASH_SOURCE" in script
    assert ".venv/bin/python" in script


def test_restart_never_execs_a_path_that_does_not_exist():
    """Locate bash with shutil.which; fall back to sys.executable."""
    source = (REPO / "Thunder" / "bot" / "plugins" / "admin.py").read_text(encoding="utf-8")
    restart = source.split("async def restart_bot", 1)[1].split("\n@StreamBot", 1)[0]

    assert "os.execv" in restart, "the process is replaced, not retired"
    assert '"/bin/bash"' not in restart, \
        "a hardcoded absolute path is not portable"
    assert "shutil.which" in restart, "the shell must be located, not assumed"
    assert "sys.executable" in restart, \
        "there must be a fallback that works without bash"


def test_update_validation_imports_the_startup_chain_not_just_the_package():
    """Import the startup chain with placeholders; updates move config.env aside."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_update_under_test", REPO / "update.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    calls = []

    class FakeCompleted:
        returncode = 0
        stderr = b""
        stdout = b""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return FakeCompleted()

    original = module.srun
    try:
        module.srun = fake_run
        assert module._validate() is True
    finally:
        module.srun = original

    assert any("compileall" in " ".join(str(a) for a in cmd) for cmd, _ in calls), \
        "syntax checking is still worth doing"

    probe_calls = [(cmd, kw) for cmd, kw in calls if "-c" in cmd]
    assert probe_calls, "validation must run an import probe, not only compile"
    probe, kwargs = probe_calls[0]
    probe = " ".join(str(a) for a in probe)

    for needed in ("from pyrogram import", "Client", "warpcrypto", "Thunder.bot",
                   "Thunder.__main__"):
        assert needed in probe, f"the probe omits {needed}"

    env = kwargs.get("env") or {}
    for needed in ("API_ID", "API_HASH", "BOT_TOKEN", "BIN_CHANNEL", "DATABASE_URL"):
        assert env.get(needed), f"the probe runs without {needed}"
