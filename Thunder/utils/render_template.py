# Thunder/utils/render_template.py

import urllib.parse
import html as html_module

from jinja2 import Environment, FileSystemLoader
from Thunder.bot import StreamBot
from Thunder.server.exceptions import InvalidHash
from Thunder.utils.file_properties import get_fids, get_uniqid, get_fname
from Thunder.utils.logger import logger
from Thunder.vars import Var

template_env = Environment(
    loader=FileSystemLoader('Thunder/template'),
    enable_async=True,
    cache_size=200,
    auto_reload=False,
    optimized=True
)

async def render_page(id: int, secure_hash: str, requested_action: str | None = None) -> str:
    try:
        message = await StreamBot.get_messages(chat_id=int(Var.BIN_CHANNEL), message_ids=id)
        if not message:
            raise InvalidHash("Message not found")
        
        file_unique_id = get_uniqid(message)
        file_name = get_fname(message)
        
        if not file_unique_id or file_unique_id[:6] != secure_hash:
            raise InvalidHash
        
        quoted_filename = urllib.parse.quote(file_name.replace('/', '_'))
        src = urllib.parse.urljoin(Var.URL, f'{secure_hash}{id}/{quoted_filename}')
        safe_filename = html_module.escape(file_name)
        if requested_action == 'stream':
            template = template_env.get_template('req.html')
            context = {
                'tag': "video",
                'heading': f"View {safe_filename}",
                'file_name': safe_filename,
                'src': src
            }
        else:
            template = template_env.get_template('dl.html')
            context = {
                'file_name': safe_filename,
                'src': src
            }
        return await template.render_async(**context)
    except Exception as e:
        logger.error(f"Error in render_page for ID {id} and hash {secure_hash}: {e}", exc_info=True)
        raise
