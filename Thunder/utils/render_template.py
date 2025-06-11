import urllib.parse
import html as html_module
from jinja2 import Environment, FileSystemLoader, TemplateNotFound, TemplateSyntaxError
from typing import Optional
from Thunder.vars import Var
from Thunder.bot import StreamBot
from Thunder.utils.file_properties import get_fids
from Thunder.server.exceptions import InvalidHash, FileNotFound # Added FileNotFound
from Thunder.utils.logger import logger # Added logger

template_env = Environment(
    loader=FileSystemLoader('Thunder/template'),
    enable_async=True,
    cache_size=Var.JINJA_CACHE_SIZE if hasattr(Var, 'JINJA_CACHE_SIZE') else 50, # Use Var if available
    auto_reload=Var.JINJA_AUTO_RELOAD if hasattr(Var, 'JINJA_AUTO_RELOAD') else False, # Use Var if available
    optimized=True
)

async def render_page(id: int, secure_hash: str, requested_action: Optional[str] = None) -> str:
    file_data = await get_fids(StreamBot, int(Var.BIN_CHANNEL), id)

    if not file_data:
        raise FileNotFound("File data could not be retrieved for rendering.")

    object_unique_id = getattr(file_data, 'unique_id', None)
    if not object_unique_id or object_unique_id[:6] != secure_hash:
        # Log this attempt for security audit?
        # logger.warning(f"Invalid hash attempt for id {id}, secure_hash {secure_hash}, obj_uid {object_unique_id}")
        raise InvalidHash # Keep InvalidHash for security failures

    actual_file_name = getattr(file_data, 'file_name', None)
    if not actual_file_name: # If file_name is empty or None after getattr
        # This case should ideally be handled by get_fids ensuring file_name is populated,
        # or get_fname could be called here as a fallback.
        # For now, if it's critical, raise an error. Otherwise, provide a default.
        logger.warning(f"File name is missing for file_id {id} after get_fids. Using default.")
        actual_file_name = "file" # Default filename if not found on file_data

    quoted_filename = urllib.parse.quote(actual_file_name.replace('/', '_'))
    # Construct src URL ensuring no double slashes if Var.URL ends with / and components start with /
    base_url = Var.URL.rstrip('/')
    src_path = f"{secure_hash}{id}/{quoted_filename}"
    src = f"{base_url}/{src_path.lstrip('/')}"

    safe_filename = html_module.escape(actual_file_name)

    try:
        if requested_action == 'stream':
            template_name = 'req.html'
            # Determine media tag dynamically based on mime_type
            media_tag = "video" # Default
            mime_type = getattr(file_data, 'mime_type', "").lower()
            if mime_type.startswith("audio/"):
                media_tag = "audio"
            elif mime_type.startswith("image/"): # Though streaming images this way is unusual
                media_tag = "img"
            # Add more types as needed: e.g. application/pdf for <embed> or <iframe>

            context = {
                'tag': media_tag,
                'heading': f"View {safe_filename}",
                'file_name': safe_filename,
                'src': src
            }
        else: # Default to download page
            template_name = 'dl.html'
            context = {
                'file_name': safe_filename,
                'src': src
            }

        template = template_env.get_template(template_name)
        return await template.render_async(**context)

    except (TemplateNotFound, TemplateSyntaxError) as e_jinja_load:
        logger.error(f"Jinja2 template loading/syntax error in render_page ('{template_name}'): {e_jinja_load}")
        # For security, don't pass e_jinja_load directly to user
        raise Exception("Server template configuration error.")
    except Exception as e_jinja_render: # Catch other potential rendering errors
        logger.error(f"Jinja2 template rendering error in render_page ('{template_name}'): {e_jinja_render}")
        raise Exception("Server error during page rendering.")
