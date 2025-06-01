# Thunder/utils/render_template.py

import urllib.parse
import html as html_module
from jinja2 import Environment, FileSystemLoader
from Thunder.vars import Var
from Thunder.bot import StreamBot
from Thunder.utils.file_properties import get_file_ids
from Thunder.server.exceptions import InvalidHash
from Thunder.utils.error_handling import log_errors

template_env = Environment(
    loader=FileSystemLoader('Thunder/template'),
    enable_async=True,
    cache_size=200,
    auto_reload=False,
    optimized=True
)

@log_errors
async def render_page(id: int, secure_hash: str, requested_action: str | None = None) -> str:
    file_data = await get_file_ids(StreamBot, int(Var.BIN_CHANNEL), id)
    if file_data.unique_id[:6] != secure_hash:
        raise InvalidHash
    quoted_filename = urllib.parse.quote(file_data.file_name.replace('/', '_'))
    src = urllib.parse.urljoin(Var.URL, f'{secure_hash}{id}/{quoted_filename}')
    safe_filename = html_module.escape(file_data.file_name)
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
