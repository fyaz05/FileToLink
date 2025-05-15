# Thunder/utils/render_template.py

import urllib.parse
import aiofiles
import aiohttp
import html as html_module
from Thunder.vars import Var
from Thunder.bot import StreamBot
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.file_properties import get_file_ids
from Thunder.server.exceptions import InvalidHash
from Thunder.utils.logger import logger


async def render_page(id: int, secure_hash: str) -> str:
    # Render HTML page for streaming or downloading files
    file_data = await get_file_ids(StreamBot, int(Var.BIN_CHANNEL), id)
    # Verify hash matches to prevent unauthorized access
    if file_data.unique_id[:6] != secure_hash:
        logger.debug(f'Link hash: {secure_hash} - Expected: {file_data.unique_id[:6]}')
        logger.debug(f"Invalid hash for message ID {id}")
        raise InvalidHash

    # Generate source URL for the file
    src = urllib.parse.urljoin(Var.URL, f'{secure_hash}{str(id)}')
    
    # Choose template based on file type
    mime_type = file_data.mime_type.split('/')[0].strip()
    
    if mime_type in ('video', 'audio'):
        async with aiofiles.open('Thunder/template/req.html', 'r') as f:
            template_content = await f.read()
            
        safe_filename = html_module.escape(file_data.file_name)
        heading = f"{'Watch' if mime_type == 'video' else 'Listen'} {safe_filename}"
        html = template_content.replace('tag', mime_type) % (heading, safe_filename, src)
    else:
        # For documents and other file types
        async with aiofiles.open('Thunder/template/dl.html', 'r') as f:
            template_content = await f.read()
            
        # Get file size for download template
        async with aiohttp.ClientSession() as session:
            async with session.get(src) as response:
                file_size = humanbytes(int(response.headers.get('Content-Length', 0)))
                
        safe_filename = html_module.escape(file_data.file_name)
        heading = f"Download {safe_filename}"
        html = template_content % (heading, safe_filename, src, file_size)

    return html
