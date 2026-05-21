from pytdbot import types

from Thunder.bot import StreamBot
from Thunder.utils.logger import logger
from Thunder.vars import Var


def get_commands():
    command_descriptions = {
        "start": "Start the bot and get a welcome message",
        "link": "(Group) Generate a direct link for a file or batch",
        "dc": "Retrieve the data center (DC) information of a user or file",
        "ping": "Check the bot's status and response time",
        "about": "Get information about the bot",
        "help": "Show help and usage instructions",
        "status": "(Admin) View bot details and current workload",
        "stats": "(Admin) View usage statistics and resource consumption",
        "broadcast": "(Admin) Send a message to all users",
        "ban": "(Admin) Ban a user",
        "unban": "(Admin) Unban a user",
        "log": "(Admin) Send bot logs",
        "restart": "(Admin) Update and restart the bot",
        "shell": "(Admin) Execute a shell command",
        "speedtest": "(Admin) Run network speed test",
        "users": "(Admin) Show the total number of users",
        "authorize": "(Admin) Grant permanent access to a user",
        "deauthorize": "(Admin) Remove permanent access from a user",
        "listauth": "(Admin) List all authorized users"
    }
    return [types.BotCommand(command=name, description=desc) for name, desc in command_descriptions.items()]


async def set_commands():
    if Var.SET_COMMANDS:
        try:
            commands = get_commands()
            if commands:
                result = await StreamBot.setCommands(
                    scope=types.BotCommandScopeDefault(),
                    language_code="",
                    commands=commands
                )
                if isinstance(result, types.Error):
                    logger.error(f"Failed to set bot commands: {result.message}")
        except Exception as e:
            logger.error(f"Failed to set bot commands: {e}", exc_info=True)
