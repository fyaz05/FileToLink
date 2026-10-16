"""Plugin discovery must load handler modules only.

import_plugins loads every file the glob matches as a plugin. The glob
deliberately excludes __init__.py: the package marker exists so coverage.py
measures these modules, and picking it up would register the package itself as
a fifth plugin (executing it a second time under a second module identity).
"""

import glob
import os

import pytest

from Thunder import __main__ as main_mod

EXPECTED = ["admin", "callbacks", "common", "stream"]


@pytest.mark.unit
def test_glob_matches_exactly_the_handler_modules():
    found = sorted(os.path.basename(p)[:-3] for p in glob.glob(main_mod.PLUGIN_PATH))
    assert found == EXPECTED


@pytest.mark.unit
def test_package_marker_is_never_loaded_as_a_plugin():
    stems = [os.path.basename(p) for p in glob.glob(main_mod.PLUGIN_PATH)]
    assert "__init__.py" not in stems


@pytest.mark.unit
def test_every_plugin_imports_cleanly():
    """A handler that fails to import is reported and skipped at boot, not
    silently dropped -- so each one must import on its own."""
    for name in EXPECTED:
        module = __import__(f"Thunder.bot.plugins.{name}", fromlist=["_"])
        assert module is not None
