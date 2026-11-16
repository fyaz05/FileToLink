"""Deployment entry points that only a hosting platform reads.

Nothing in Thunder/ imports these, so no test failure or grep would notice them
going away -- but deleting one breaks a documented deployment while the suite
stays green. Procfile in particular is required by Heroku's Python buildpack,
which has no default web process.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_procfile_declares_the_web_process():
    procfile = (REPO / "Procfile").read_text(encoding="utf-8")
    assert procfile.startswith("web:"), "Heroku's buildpack needs a web process type"
    assert "python -m Thunder" in procfile, "the app's only entry point"


def test_platform_files_consumed_at_deploy_time_still_exist():
    for name in ("Procfile", "heroku.yml", ".python-version", "requirements.txt"):
        target = REPO / name
        assert target.is_file(), f"{name} is read by the deploy platform, not by our code"

    # Local tooling and buildpack deploys read this; the Docker image does not.
    assert (REPO / ".python-version").read_text(encoding="utf-8").strip(), \
        "uv and buildpack-based deploys read this to pick the interpreter"
