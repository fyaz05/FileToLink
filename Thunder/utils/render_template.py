import urllib.parse

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pytdbot import types

from Thunder.bot import StreamBot
from Thunder.server.exceptions import InvalidHash, RateLimited
from Thunder.utils.bot_utils import quote_media_name
from Thunder.utils.compat import _get_file_name, _get_file_unique_id
from Thunder.utils.logger import logger
from Thunder.vars import Var

template_env = Environment(
    loader=FileSystemLoader('Thunder/template'),
    autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=True),
    enable_async=True,
    cache_size=200,
    auto_reload=False,
    optimized=True
)


async def render_media_page(file_name: str, src: str, requested_action: str | None = None) -> str:
    if requested_action == 'stream':
        template = template_env.get_template('req.html')
        context = {
            'heading': f"View {file_name}",
            'file_name': file_name,
            'src': f"{src}?disposition=inline"
        }
    else:
        template = template_env.get_template('dl.html')
        context = {
            'file_name': file_name,
            'src': src
        }
    return await template.render_async(**context)


async def render_page(message_id: int, secure_hash: str, requested_action: str | None = None) -> str:
    try:
        result = await StreamBot.getMessage(
            chat_id=int(Var.BIN_CHANNEL), message_id=message_id
        )
        if isinstance(result, types.Error):
            if hasattr(result, "code") and result.code == 429:
                raise RateLimited(f"Rate limited: {result.message}")
            raise InvalidHash(f"Message not found: {result.message}")

        message = result
        if not message:
            raise InvalidHash("Message not found")

        file_unique_id = _get_file_unique_id(message)
        file_name = _get_file_name(message) or "file"

        if not file_unique_id or file_unique_id[:6] != secure_hash:
            raise InvalidHash("File unique ID or secure hash mismatch during rendering.")

        quoted_filename = quote_media_name(file_name)
        src = urllib.parse.urljoin(Var.URL, f'{secure_hash}{message_id}/{quoted_filename}')
        return await render_media_page(file_name, src, requested_action)
    except Exception as e:
        logger.error(
            f"Error in render_page for message_id {message_id} and hash {secure_hash}: {e}",
            exc_info=True
        )
        raise
