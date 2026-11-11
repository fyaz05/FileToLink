"""Import hygiene and configuration handling.

These cover the three defects found by running the bot: the file_properties
import cycle, the database name derived from NAME, and /status during startup.
"""
import ast
import pathlib
import subprocess
import sys

import pytest

from Thunder.vars import Var

REPO = pathlib.Path(__file__).resolve().parent.parent


def test_file_properties_importable_first():
    """Importing file_properties before anything else used to raise
    ImportError because server/__init__ eagerly imported stream_routes."""
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
        """Guards the regression where Database() was passed Var.NAME, which
        crashes for any NAME MongoDB rejects (for example one with a space)."""
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
    """/status must not 500 while StreamBot.username is still unset."""
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


def test_update_script_parses():
    result = subprocess.run(
        [sys.executable, "-c",
         "import ast,pathlib;ast.parse(pathlib.Path('update.py').read_text(encoding='utf-8'))"],
        cwd=REPO, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_thunder_script_resolves_python_from_its_own_directory():
    script = (REPO / "thunder.sh").read_text(encoding="utf-8")
    assert "BASH_SOURCE" in script
    assert ".venv/bin/python" in script
