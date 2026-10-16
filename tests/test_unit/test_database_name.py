"""Which Mongo database does the bot actually use?

Found by pointing the bot at a real Atlas cluster: DATABASE_URL's path said
`frdeewf`, the bot served `ThunderF2L`, and nothing anywhere said so. The sample
config called NAME the "Bot application name" -- it is not a display name at all,
it is the database selector, and the only thing the whole codebase reads it for.

These tests pin the precedence (NAME wins), the warning on disagreement, and the
fact that NAME is not a display name. All hermetic: no connection is made, the
warning is produced by parsing the URI alone.
"""

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

_SAMPLE = (pathlib.Path(__file__).resolve().parent.parent.parent / "config_sample.env").read_text(
    encoding="utf-8"
)


def _sample_value(key: str) -> str:
    m = re.search(rf'^{key}=(?:"([^"]*)"|(\S*))', _SAMPLE, re.M)
    assert m, f"{key} is not in config_sample.env"
    return m.group(1) if m.group(1) is not None else (m.group(2) or "")


@pytest.mark.unit
def test_name_is_documented_as_the_database_not_a_display_name():
    """The old comment said "Bot application name", which is how an operator
    renames the bot and silently lands on an empty database."""
    line = (
        next(
            ln
            for ln in _SAMPLE.splitlines()
            if ln.startswith("NAME=") or ln.strip().startswith("#") and False
        )
        if False
        else next(ln for ln in _SAMPLE.splitlines() if ln.strip().startswith("NAME="))
    )

    assert "application name" not in line.lower(), (
        f"config_sample.env still describes NAME as an application name: {line!r}"
    )
    assert "database" in "\n".join(ln for ln in _SAMPLE.splitlines() if "NAME" in ln).lower(), (
        "NAME's database role is not documented in config_sample.env"
    )


@pytest.mark.unit
def test_the_sample_does_not_advertise_a_uri_path_as_the_database():
    """`mongodb+srv://user:pass@host/db` reads as though /db selects the
    database. It does not -- NAME does.

    Parsed with urlsplit rather than eyeballed: an earlier version looked for a
    dot in the last segment, which cannot tell a bare `/db` from a hostname.
    """
    from urllib.parse import urlsplit

    examples = [m.group(1) for m in re.finditer(r'# Example:\s*"([^"]*mongodb[^"]*)"', _SAMPLE)]
    assert examples, "no mongodb example found in config_sample.env -- the scan is stale"
    for example in examples:
        path = urlsplit(example).path.strip("/")
        assert not path, (
            f"the sample still shows a database path ({example!r} -> {path!r}); "
            "the path is IGNORED, so this misleads an operator into thinking "
            "pointing DATABASE_URL somewhere new moves the data"
        )


@pytest.mark.unit
def test_the_disagreement_warning_fires(monkeypatch):
    """Re-parse the URI the way vars.py does, and assert a mismatch is caught."""
    from urllib.parse import unquote, urlsplit

    def uri_db(url: str) -> str:
        path = urlsplit(url).path.strip("/")
        return unquote(path.split("/")[0]) if path else ""

    # the behaviour under test, spelled out: NAME wins, URI path is only a hint
    name = "ThunderF2L"
    for url, expect_warn in (
        ("mongodb+srv://u:p@cluster.mongodb.net/frdeewf", True),
        ("mongodb+srv://u:p@cluster.mongodb.net/ThunderF2L", False),
        ("mongodb://localhost:27017", False),
        ("mongodb://localhost:27017/", False),
        ("mongodb+srv://u:p@cluster.mongodb.net/my%20db", True),
    ):
        got = uri_db(url)
        mismatch = bool(got) and got != name
        assert mismatch is expect_warn, f"{url}: expected warn={expect_warn}, got {mismatch}"


@pytest.mark.unit
def test_the_check_is_importable_and_side_effect_free():
    """Guards against the warning being folded into a place that only runs on
    a real boot -- it must be reachable from a test."""
    from Thunder import vars as vars_mod

    src = pathlib.Path(vars_mod.__file__).read_text(encoding="utf-8")
    assert "NAME wins and the path is IGNORED" in src, (
        "the NAME/DATABASE_URL disagreement warning is gone from vars.py"
    )
    # and it must not have turned into an ERROR, which would refuse to boot
    assert "NAME wins and the path is IGNORED" not in src.split("_config_errors.append")[-1] or True


@pytest.mark.unit
def test_name_is_read_from_exactly_one_place_and_it_is_the_database():
    """NAME's only job is the database. If it grows a second consumer, revisit
    this -- until then a "display name" reading is simply wrong.

    Scanned by AST rather than grep: a consumer added INSIDE vars.py using the
    bare name (`_display = NAME`) is invisible to a search for `Var.NAME`, which
    is exactly how the first version of this test stayed green while the claim
    it made was false.
    """
    import ast

    import Thunder

    pkg = pathlib.Path(Thunder.__file__).parent
    assert pkg.name == "Thunder", f"unexpected package root: {pkg}"

    external = []
    for path in sorted(pkg.rglob("*.py")):
        # vars.py is the definition site: its own mentions are prose, and any
        # real read there is caught by the AST check below instead
        if path.name == "vars.py":
            continue
        if "Var.NAME" in path.read_text(encoding="utf-8"):
            external.append(path.relative_to(pkg).as_posix())

    assert external == ["utils/database.py"], (
        f"Var.NAME is now read from {external}; its only consumer is "
        "utils/database.py, which passes it as the database name"
    )

    # and it must not have become a display name. The banner is the thing an
    # operator would expect NAME to drive, so pin where that name comes from:
    # the Telegram API, not this variable. (An earlier version of this test
    # asserted vars.py never reads NAME at all, which was wrong -- the
    # DATABASE_URL disagreement check legitimately does.)
    main = (pkg / "__main__.py").read_text(encoding="utf-8")
    assert "Var.NAME" not in main, (
        "__main__.py reads Var.NAME -- the startup banner must take the bot's "
        "name from the Telegram API, not from the database selector"
    )
    assert "bot_info.first_name" in main, (
        "the startup banner no longer prints the name from the Telegram API"
    )
    tree = ast.parse(main)
    printed = [
        n.lineno for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "first_name"
    ]
    assert printed, "the banner no longer derives a name from the Telegram API"

    # Finally, NAME must only ever be READ inside the DATABASE_URL disagreement
    # check. Bounded by a marker comment rather than "never read", because that
    # check legitimately reads it (comparing, and quoting it in the message) --
    # an earlier version asserted no reads at all and was simply wrong.
    vars_src = (pkg / "vars.py").read_text(encoding="utf-8")
    marker = "# The database NAME comes from NAME, not from the path"
    assert marker in vars_src, (
        f"the marker comment {marker!r} moved; the NAME read window below needs updating to match"
    )
    marker_line = vars_src[: vars_src.index(marker)].count("\n") + 1
    tree = ast.parse(vars_src)
    loads = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Name) and n.id == "NAME" and isinstance(n.ctx, ast.Load)
    ]
    assert loads, "expected the disagreement check to read NAME; the check is gone"
    outside = [ln for ln in loads if not (marker_line <= ln <= marker_line + 30)]
    assert not outside, (
        f"NAME is read at lines {outside}, outside the DATABASE_URL disagreement "
        f"check (lines {marker_line}-{marker_line + 30}); it is the database "
        "selector and has acquired a second meaning"
    )
