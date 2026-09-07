# Thunder/bot/registry.py

"""Single command registry (plan M1).

One table drives all three surfaces: the Telegram command menu (owner-only
commands hidden, descriptions auto-truncated to Telegram's 256-char limit),
the /help command section, and the command table in AGENTS.md (a unit test
fails if AGENTS.md drifts from this registry).
"""

from typing import NamedTuple

from pyrogram.types import BotCommand

from Thunder.utils.messages import MSG_HELP_COMMAND_ROW


class Command(NamedTuple):
    name: str
    description: str
    owner_only: bool = False


COMMANDS: list[Command] = [
    Command("start", "Start the bot and get a welcome message"),
    Command("help", "Show help and usage instructions"),
    Command("link", "(Group) Generate a direct link for a file or batch"),
    Command("dc", "Retrieve the data center (DC) information of a user or file"),
    Command("ping", "Check the bot's status and response time"),
    Command("about", "Get information about the bot"),
    Command("users", "Show the total number of users", owner_only=True),
    Command("status", "View bot details and current workload", owner_only=True),
    Command("stats", "View usage statistics and resource consumption", owner_only=True),
    Command("broadcast", "Send a message to all users", owner_only=True),
    Command("ban", "Ban a user", owner_only=True),
    Command("unban", "Unban a user", owner_only=True),
    Command("log", "Send bot logs", owner_only=True),
    Command("restart", "Update and restart the bot", owner_only=True),
    Command("shell", "Execute a shell command (requires ENABLE_SHELL)", owner_only=True),
    Command("authorize", "Grant permanent access to a user", owner_only=True),
    Command("deauthorize", "Remove permanent access from a user", owner_only=True),
    Command("listauth", "List all authorized users", owner_only=True),
]

# Telegram's set_bot_commands description limit
_MAX_DESC_LEN = 256


def bot_commands() -> list[BotCommand]:
    """Menu surface: owner-only commands are hidden (M1 fix)."""
    return [
        BotCommand(cmd.name, cmd.description[:_MAX_DESC_LEN])
        for cmd in COMMANDS
        if not cmd.owner_only
    ]


def help_command_rows() -> str:
    """/help surface: same public commands, same order."""
    rows = ""
    for cmd in COMMANDS:
        if cmd.owner_only:
            continue
        rows += MSG_HELP_COMMAND_ROW.format(name=cmd.name, description=cmd.description)
    return rows


__all__ = ["Command", "COMMANDS", "bot_commands", "help_command_rows"]
