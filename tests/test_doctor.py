"""Doctor checks must run concurrently and fit Telegram's message limit."""
import asyncio
import html
from html import unescape

from pyrogram.errors import FloodWait

from Thunder.bot.plugins import admin
from Thunder.utils import preflight


def test_doctor_chunks_preserve_text_and_fit_after_html_escaping():
    rendered = "check & <tag> \"quoted\" 'value'\n" * 500
    chunks = admin._chunk_telegram_text(rendered)
    assert len(chunks) > 1
    assert "".join(chunks) == rendered
    assert all(len(f"<pre>{html.escape(chunk)}</pre>") <= 4096 for chunk in chunks)


def test_doctor_chunks_count_utf16_units_for_non_bmp_text():
    rendered = "😀" * 3000
    chunks = admin._chunk_telegram_text(rendered)
    assert len(chunks) > 1
    assert "".join(chunks) == rendered
    assert all(
        len(f"<pre>{html.escape(chunk)}</pre>".encode("utf-16-le")) // 2 <= 4096
        for chunk in chunks
    )


async def test_secondary_doctor_checks_run_concurrently_in_stable_order(monkeypatch):
    monkeypatch.setattr(admin, "multi_clients", {0: object(), 2: object(), 1: object()})
    monkeypatch.setattr(admin, "client_keys", {1: "MULTI_TOKEN1", 2: "MULTI_TOKEN2"})
    monkeypatch.setattr(admin, "client_bot_ids", {1: 101, 2: 102})
    started = []
    both_started = asyncio.Event()

    async def fake_check(client, key, channel_id, bot_id):
        started.append(key)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=1)
        return [(preflight.OK, key)]

    monkeypatch.setattr(preflight, "check_secondary_client", fake_check)
    report = preflight.Report()
    await admin._run_secondary_client_checks(report)
    assert [text for _status, text in report.results] == ["MULTI_TOKEN1", "MULTI_TOKEN2"]


async def test_doctor_check_groups_run_concurrently(monkeypatch):
    started = []
    release = asyncio.Event()

    async def check(name, result=None):
        started.append(name)
        await release.wait()
        return result

    async def verify(report):
        await check("configuration")

    async def public(*args, **kwargs):
        return await check("public", [])

    async def secondary(report):
        await check("secondary")

    async def reply(*args, **kwargs):
        return object()

    async def deliver(*args, **kwargs):
        pass

    monkeypatch.setattr(admin, "reply", reply)
    monkeypatch.setattr(preflight, "verify_configuration", verify)
    monkeypatch.setattr(preflight, "check_public_url", public)
    monkeypatch.setattr(admin, "_run_secondary_client_checks", secondary)
    monkeypatch.setattr(admin, "_deliver_doctor_report", deliver)
    task = asyncio.create_task(admin.doctor_command(object(), object()))

    async def wait_for_all():
        while len(started) < 3:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_all(), timeout=1)
    assert started == ["configuration", "public", "secondary"]
    release.set()
    await task


class FakeStatus:
    def __init__(self, errors=()):
        self.errors = list(errors)
        self.calls = []

    async def edit_text(self, text, parse_mode=None):
        self.calls.append(text)
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error


class FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, parse_mode=None):
        self.calls.append(text)


async def test_long_doctor_reports_are_delivered_in_bounded_chunks():
    status = FakeStatus()
    message = FakeMessage()
    rendered = "a & <check>\n" * 1000
    await admin._deliver_doctor_report(status, message, rendered)
    assert len(status.calls) == 1
    assert len(message.calls) > 1
    assert all(len(call) <= 4096 for call in status.calls + message.calls)
    assert "".join(
        unescape(call.removeprefix("<pre>").removesuffix("</pre>"))
        for call in status.calls + message.calls
    ) == rendered


async def test_doctor_report_falls_back_to_a_reply_after_a_failed_floodwait_retry(monkeypatch):
    status = FakeStatus([FloodWait(value=0), RuntimeError("edit failed")])
    message = FakeMessage()

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(admin.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(admin.logger, "warning", lambda *args, **kwargs: None)
    await admin._deliver_doctor_report(status, message, "configuration report")
    assert len(status.calls) == 2
    assert message.calls == ["<pre>configuration report</pre>"]


async def test_later_doctor_chunks_retry_repeated_floodwaits(monkeypatch):
    class FlakyMessage:
        def __init__(self):
            self.attempts = 0
            self.delivered = []

        async def reply_text(self, text, parse_mode=None):
            self.attempts += 1
            if self.attempts <= 2:
                raise FloodWait(value=0)
            self.delivered.append(text)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(admin.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(admin.logger, "warning", lambda *args, **kwargs: None)
    status = FakeStatus()
    message = FlakyMessage()
    rendered = "abcdefghij" * 1000
    await admin._deliver_doctor_report(status, message, rendered)
    assert len(status.calls) == 1
    assert message.attempts == 4
    assert len(message.delivered) == 2
