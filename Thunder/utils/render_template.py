# Thunder/utils/render_template.py

import urllib.parse
import html as html_module
from jinja2 import Environment, FileSystemLoader
from Thunder.vars import Var
from Thunder.bot import StreamBot
from Thunder.utils.file_properties import get_file_ids
from Thunder.server.exceptions import InvalidHash
from Thunder.utils.logger import logger

# Initialize Jinja2 environment
template_env = Environment(loader=FileSystemLoader('Thunder/template'), enable_async=True)

async def render_page(id: int, secure_hash: str, requested_action: str | None = None) -> str: # Re-add requested_action
    file_data = await get_file_ids(StreamBot, int(Var.BIN_CHANNEL), id)
    if file_data.unique_id[:6] != secure_hash:
        logger.debug(f'Link hash: {secure_hash} - Expected: {file_data.unique_id[:6]}')
        logger.debug(f"Invalid hash for message ID {id}")
        raise InvalidHash

    quoted_filename = urllib.parse.quote(file_data.file_name.replace('/', '_'))
    src = urllib.parse.urljoin(Var.URL, f'{secure_hash}{str(id)}/{quoted_filename}')
    
    safe_filename = html_module.escape(file_data.file_name)
    
    template_name: str
    context: dict

    if requested_action == 'stream':
        template_name = 'req.html'
        context = {
            'tag': "video",
            'heading': f"View {safe_filename}",
            'file_name': safe_filename,
            'src': src
        }
    else:  # Default to download
        template_name = 'dl.html'
        context = {
            'file_name': safe_filename,
            'src': src
        }

    template = template_env.get_template(template_name)
    html_content = await template.render_async(**context)
    return html_content
