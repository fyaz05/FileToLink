from Thunder.bot import StreamBot
from Thunder.bot.registry import bot_commands, help_command_rows
from Thunder.utils.logger import logger
from Thunder.utils.messages import MSG_HELP_COMMANDS_HEADER, MSG_HELP_TIPS
from Thunder.vars import Var


def build_help_text(max_files: int) -> str:
    """Assemble /help from its three parts (M1: commands come from the registry)."""
    from Thunder.utils.messages import MSG_HELP_INTRO

    return (
        MSG_HELP_INTRO.format(max_files=max_files)
        + MSG_HELP_COMMANDS_HEADER
        + help_command_rows()
        + MSG_HELP_TIPS
    )


async def set_commands():
    if Var.SET_COMMANDS:
        try:
            commands = bot_commands()
            if commands:
                await StreamBot.set_bot_commands(commands)
        except Exception as e:
            logger.error(f"Failed to set bot commands: {e}", exc_info=True)
