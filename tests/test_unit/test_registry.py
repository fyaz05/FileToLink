# tests/test_unit/test_registry.py
"""M1: registry drives the menu (owner-only hidden) and AGENTS.md drift."""

import re
from pathlib import Path

import pytest

from Thunder.bot.registry import COMMANDS, bot_commands, help_command_rows

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.unit
def test_owner_only_commands_hidden_from_menu():
    menu_names = {c.command for c in bot_commands()}
    owner_names = {c.name for c in COMMANDS if c.owner_only}
    assert owner_names.isdisjoint(menu_names)
    # public commands are present
    assert {"start", "help", "link", "ping", "dc", "about"} <= menu_names


@pytest.mark.unit
def test_descriptions_within_telegram_limit():
    for cmd in bot_commands():
        assert len(cmd.description) <= 256


@pytest.mark.unit
def test_help_rows_match_public_commands():
    rows = help_command_rows()
    for cmd in COMMANDS:
        if cmd.owner_only:
            assert f"/{cmd.name} " not in rows
        else:
            assert f"/{cmd.name} " in rows


@pytest.mark.unit
def test_agents_md_documents_every_command():
    """M1 code-gen drift check: AGENTS.md must mention each command name."""
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for cmd in COMMANDS:
        assert re.search(rf"`/{cmd.name}`", agents), (
            f"AGENTS.md is missing `/{cmd.name}` -- update the commands table "
            "when editing Thunder/bot/registry.py"
        )
