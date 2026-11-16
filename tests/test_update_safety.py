"""Verify update.py preconditions and rollback in isolated project copies."""
import importlib.util
import sqlite3
import subprocess
import sys

from update_helpers import (BASE_ENV, force_rmtree, make_sandbox, make_upstream,
                            run_update)


def _load_update():
    """Import update.py so its helpers can be called directly."""
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("_update_under_test", repo / "update.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(repo))
    spec.loader.exec_module(module)
    return module


SESSIONS_COLS = ("dc_id INTEGER PRIMARY KEY, api_id INTEGER, test_mode INTEGER, "
                 "auth_key BLOB, date INTEGER, user_id INTEGER, is_bot INTEGER")
SESSIONS_COLS_V7 = SESSIONS_COLS + ", server_address TEXT, port INTEGER"


def _write_session(path, version, columns=SESSIONS_COLS, update_state=True,
                   username_peer_id=False, extra_columns=0):
    """Write a session. `columns` shapes it, since the stored version does not
    identify the layout: a fixture has to match the format it claims."""
    connection = sqlite3.connect(str(path))
    connection.execute(f"CREATE TABLE sessions ({columns})")
    connection.execute(
        "CREATE TABLE peers (id INTEGER PRIMARY KEY, access_hash INTEGER, "
        "type INTEGER NOT NULL, phone_number TEXT, last_update_on INTEGER)")
    connection.execute(
        "CREATE TABLE usernames (id INTEGER, username TEXT"
        f"{', peer_id INTEGER' if username_peer_id else ''})")
    if update_state:
        connection.execute(
            "CREATE TABLE update_state (id INTEGER PRIMARY KEY, pts INTEGER, "
            "qts INTEGER, date INTEGER, seq INTEGER)")
    connection.execute("CREATE TABLE version (number INTEGER)")
    connection.execute("INSERT INTO version VALUES (?)", (version,))
    placeholders = ", ".join(["2", "123456", "0", "?", "0", "1", "1"] + ["NULL"] * extra_columns)
    connection.execute(f"INSERT INTO sessions VALUES ({placeholders})", (b"x" * 256,))
    connection.commit()
    connection.close()


def test_only_sessions_the_library_cannot_open_are_reported(tmp_path, monkeypatch):
    """Report the files the library refuses, not every schema difference.

    A stored version does not identify a session's format: wzgram migrates v6 and
    v7, while pyrofork's v4 layout fails to migrate. The library is the only
    authority, so update.py tries it on a snapshot.
    """
    module = _load_update()
    monkeypatch.chdir(tmp_path)

    # pyrofork's shape: its step 4 already created update_state, which wzgram's
    # step 4 re-creates without IF NOT EXISTS, so open() raises.
    _write_session(tmp_path / "pyrofork.session", 4)
    # Migratable: one step behind, shaped as that version leaves it.
    _write_session(tmp_path / "migratable.session", 7, SESSIONS_COLS_V7,
                   username_peer_id=True, extra_columns=2)
    _write_session(tmp_path / "current.session", 8, SESSIONS_COLS_V7,
                   username_peer_id=True, extra_columns=2)
    (tmp_path / "garbage.session").write_bytes(b"not a database at all")
    (tmp_path / "notes.txt").write_text("ignore me")

    found = module._stale_sessions()

    assert any("pyrofork.session" in entry for entry in found), \
        "a session the library cannot open must be reported"
    assert not any("migratable.session" in entry for entry in found), \
        "a session the library migrates must not be reported; deleting it logs the bot out"
    assert not any("current.session" in entry for entry in found), \
        "the current session must not be reported"
    assert not any("garbage" in entry for entry in found), \
        "a file that is not a session database must be skipped, not crash the update"
    assert not any("notes" in entry for entry in found), "only *.session files are sessions"


def test_an_uncheckpointed_session_is_not_reported_as_unreadable(tmp_path, monkeypatch):
    """A session whose pages are still in the -wal sidecar must survive the check.

    An unclean stop leaves committed schema there and the main file looks empty.
    Copying only that file gives the probe no `version` table, so a good session is
    reported as incompatible and the operator is told to delete their authorisation.
    """
    module = _load_update()
    monkeypatch.chdir(tmp_path)

    connection = sqlite3.connect(str(tmp_path / "unclean.session"))
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(f"CREATE TABLE sessions ({SESSIONS_COLS_V7})")
    connection.execute(
        "CREATE TABLE peers (id INTEGER PRIMARY KEY, access_hash INTEGER, "
        "type INTEGER NOT NULL, phone_number TEXT, last_update_on INTEGER)")
    connection.execute("CREATE TABLE usernames (id INTEGER, username TEXT, peer_id INTEGER)")
    connection.execute(
        "CREATE TABLE update_state (id INTEGER PRIMARY KEY, pts INTEGER, "
        "qts INTEGER, date INTEGER, seq INTEGER)")
    connection.execute("CREATE TABLE version (number INTEGER)")
    connection.execute("INSERT INTO version VALUES (8)")
    connection.execute(
        "INSERT INTO sessions VALUES (2, 123456, 0, ?, 0, 1, 1, NULL, NULL)",
        (b"x" * 256,))
    connection.commit()
    # Left open and uncheckpointed: the committed pages live in the -wal sidecar,
    # which is what an unclean stop leaves behind.
    try:
        assert (tmp_path / "unclean.session-wal").exists(), \
            "the fixture needs a WAL sidecar to be meaningful"

        found = module._stale_sessions()

        assert not any("unclean.session" in entry for entry in found), (
            "a session readable through its WAL was reported as unreadable; the "
            "snapshot must include committed WAL content, not just the main file"
        )
    finally:
        connection.close()


def test_refuses_when_upstream_unset():
    sandbox = make_sandbox()
    try:
        result = run_update(sandbox, upstream="")
        assert result.returncode == 0
        assert (sandbox / ".git" / "CANARY").exists()
    finally:
        force_rmtree(sandbox)


def test_refuses_when_git_is_missing():
    sandbox = make_sandbox()
    try:
        env = dict(BASE_ENV)
        env["UPSTREAM_REPO"] = "https://example.invalid/x.git"
        env["PATH"] = "/nonexistent"
        result = subprocess.run([sys.executable, "update.py"], cwd=sandbox,
                                env=env, capture_output=True, text=True,
                                timeout=180)
        assert result.returncode == 1
        assert (sandbox / ".git" / "CANARY").exists(), "history must survive"
    finally:
        force_rmtree(sandbox)


def test_no_git_and_no_checkout_is_not_an_error():
    """In container environments without git, update is skipped without error."""
    sandbox = make_sandbox()
    try:
        force_rmtree(sandbox / ".git")
        assert not (sandbox / ".git").exists()

        env = dict(BASE_ENV)
        env["UPSTREAM_REPO"] = "https://example.invalid/x.git"
        env["PATH"] = "/nonexistent"
        result = subprocess.run([sys.executable, "update.py"], cwd=sandbox,
                                env=env, capture_output=True, text=True,
                                timeout=180)
        assert result.returncode == 0, result.stderr[-500:]
        assert (sandbox / "config.env").exists(), "nothing may be touched"
        assert (sandbox / "update.py").exists(), "code must be left intact"
    finally:
        force_rmtree(sandbox)


def test_refuses_unreachable_remote():
    sandbox = make_sandbox()
    try:
        result = run_update(sandbox, upstream="https://example.invalid/nope.git")
        assert result.returncode == 1
        assert (sandbox / ".git" / "CANARY").exists(), "history must survive"
    finally:
        force_rmtree(sandbox)


def test_refuses_missing_branch():
    root, bare = make_upstream()
    sandbox = make_sandbox()
    try:
        result = run_update(sandbox, upstream=str(bare), branch="does-not-exist")
        assert result.returncode == 1
        assert (sandbox / ".git" / "CANARY").exists()
    finally:
        force_rmtree(sandbox)
        force_rmtree(root)


def test_successful_update_applies_upstream_and_keeps_config_and_history():
    root, bare = make_upstream()
    sandbox = make_sandbox()
    try:
        result = run_update(sandbox, upstream=str(bare))
        assert result.returncode == 0, result.stderr[-500:]
        assert (sandbox / "UPSTREAM_MARKER.txt").exists()
        assert (sandbox / "config.env").read_text().startswith("KEEP_ME")

        # The update applies new code without replacing the repository, so the
        # user's own remote and branches are still there afterwards.
        assert (sandbox / ".git" / "CANARY").exists(), \
            "a successful update must not replace the user's repository"
        remotes = subprocess.run(["git", "remote", "-v"], cwd=sandbox,
                                 capture_output=True, text=True, timeout=120)
        assert "example.invalid" in remotes.stdout or "origin" in remotes.stdout
    finally:
        force_rmtree(sandbox)
        force_rmtree(root)


def test_update_is_idempotent():
    root, bare = make_upstream()
    sandbox = make_sandbox()
    try:
        assert run_update(sandbox, upstream=str(bare)).returncode == 0
        assert run_update(sandbox, upstream=str(bare)).returncode == 0
        assert (sandbox / "config.env").exists()
    finally:
        force_rmtree(sandbox)
        force_rmtree(root)


def test_broken_upstream_rolls_back():
    """A broken upstream that fails bytecode compilation is rolled back."""
    root, bare = make_upstream(broken=True)
    sandbox = make_sandbox()
    try:
        result = run_update(sandbox, upstream=str(bare))
        assert result.returncode == 1
        assert not (sandbox / "Thunder" / "broken_syntax.py").exists(), \
            "the file the failed update added must be removed"
        assert (sandbox / "config.env").exists()
        assert (sandbox / "update.py").exists(), "original code restored"
    finally:
        force_rmtree(sandbox)
        force_rmtree(root)
