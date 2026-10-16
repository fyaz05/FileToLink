"""The access-log middleware, not just its helpers.

request.path is percent-DECODED by aiohttp, so a request for
/f/x/%0A[INFO] ... arrives with a real newline and would forge log lines if the
middleware logged the path raw. The helpers were tested but the middleware was
never executed, so replacing the escaped path with the plain redacted one
passed the whole suite.
"""

import logging

import pytest

from Thunder.server import access_log_middleware


class _Request:
    def __init__(self, path, method="GET", remote="203.0.113.7"):
        self.path = path
        self.method = method
        self.remote = remote


class _Response:
    status = 200
    content_length = 1234


async def _handler(_request):
    return _Response()


@pytest.mark.unit
async def test_newline_in_path_cannot_forge_a_log_line(caplog):
    forged = "/f/" + "a" * 32 + "/x\n[INFO] admin logged in\n"
    with caplog.at_level(logging.INFO, logger="ThunderBot"):
        await access_log_middleware(_Request(forged), _handler)

    assert len(caplog.records) == 1
    line = caplog.records[0].getMessage()
    assert "\n" not in line, f"forged line written: {line!r}"
    assert "[INFO]" in line  # the payload is present but inert
    assert "%0A" in line  # control characters are escaped, not dropped


@pytest.mark.unit
async def test_middleware_logs_method_status_size_and_duration(caplog):
    with caplog.at_level(logging.INFO, logger="ThunderBot"):
        await access_log_middleware(_Request("/health", method="GET"), _handler)

    line = caplog.records[0].getMessage()
    assert '"GET /health"' in line
    assert "200 1234" in line
    assert line.rstrip().endswith("ms")


@pytest.mark.unit
async def test_middleware_still_logs_when_the_handler_raises(caplog):
    async def _boom(_request):
        raise RuntimeError("handler exploded")

    with caplog.at_level(logging.INFO, logger="ThunderBot"):
        with pytest.raises(RuntimeError):
            await access_log_middleware(_Request("/status"), _boom)

    line = caplog.records[0].getMessage()
    # a non-HTTP failure has no response object: must report 500, not crash
    assert "500" in line


@pytest.mark.unit
async def test_middleware_redacts_link_credentials(caplog):
    canonical_hash = "b" * 32
    with caplog.at_level(logging.INFO, logger="ThunderBot"):
        await access_log_middleware(_Request(f"/f/{canonical_hash}/movie.mkv"), _handler)

    line = caplog.records[0].getMessage()
    assert canonical_hash not in line, "the link credential leaked into the access log"
    assert "/f/" in line and "movie.mkv" in line
