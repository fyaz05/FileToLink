# Thunder/utils/render_template.py

import urllib.parse
import aiofiles
import aiohttp
from Thunder.vars import Var
from Thunder.bot import StreamBot
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.file_properties import get_file_ids
from Thunder.server.exceptions import InvalidHash
from Thunder.utils.logger import logger


async def render_page(id: int, secure_hash: str) -> str:
    """
    Render the HTML page for streaming or downloading.

    Args:
        id (int): The message ID.
        secure_hash (str): The secure hash.

    Returns:
        str: The rendered HTML content.

    Raises:
        InvalidHash: If the hash does not match.
    """
    file_data = await get_file_ids(StreamBot, int(Var.BIN_CHANNEL), id)
    if file_data.unique_id[:6] != secure_hash:
        logger.debug(f'Link hash: {secure_hash} - Expected hash: {file_data.unique_id[:6]}')
        logger.debug(f"Invalid hash for message with ID {id}")
        raise InvalidHash

    src = urllib.parse.urljoin(Var.URL, f'{secure_hash}{str(id)}')
    mime_type = file_data.mime_type.split('/')[0].strip()

    if mime_type == 'video':
        async with aiofiles.open('Thunder/template/req.html', 'r') as f:
            template_content = await f.read()
        heading = 'Watch {}'.format(file_data.file_name)
        tag = mime_type
        # Use template_content and format using % operator
        html = template_content.replace('tag', tag) % (heading, file_data.file_name, src)
    elif mime_type == 'audio':
        async with aiofiles.open('Thunder/template/req.html', 'r') as f:
            template_content = await f.read()
        heading = 'Listen {}'.format(file_data.file_name)
        tag = mime_type
        html = template_content.replace('tag', tag) % (heading, file_data.file_name, src)
    else:
        async with aiofiles.open('Thunder/template/dl.html', 'r') as f:
            template_content = await f.read()
        # Re-added aiohttp usage to fetch file size
        async with aiohttp.ClientSession() as session:
            async with session.get(src) as response:
                file_size = humanbytes(int(response.headers.get('Content-Length', 0)))
        heading = 'Download {}'.format(file_data.file_name)
        html = template_content % (heading, file_data.file_name, src, file_size)

    return html
