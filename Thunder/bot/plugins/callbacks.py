"""
Thunder/bot/plugins/callbacks.py - Callback query handlers for Thunder bot.
"""

import uuid
from pyrogram import Client, filters, StopPropagation
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions
from pyrogram.errors import MessageNotModified
from Thunder.bot import StreamBot
from Thunder.vars import Var
from Thunder.utils.logger import logger
from Thunder.utils.messages import *
from Thunder.utils.decorators import owner_only

# Helper Functions
async def handle_callback_error(callback_query, error, operation="callback"):
    """Standardized error handling for callback queries."""
    error_id = uuid.uuid4().hex[:8]
    logger.error(f"Error in {operation}: {error}")
    await callback_query.answer(
        MSG_ERROR_GENERIC_CALLBACK.format(error_id=error_id), 
        show_alert=True
    )

async def get_force_channel_button(client):
    """Get force channel button if configured."""
    if not Var.FORCE_CHANNEL_ID:
        return None
    
    try:
        chat = await client.get_chat(Var.FORCE_CHANNEL_ID)
        invite_link = chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else None)
        
        if invite_link:
            return [InlineKeyboardButton(
                MSG_BUTTON_JOIN_CHANNEL.format(channel_title=chat.title), 
                url=invite_link
            )]
        else:
            logger.warning(f"Could not construct invite link for FORCE_CHANNEL_ID {Var.FORCE_CHANNEL_ID}")
            return None
    except Exception as e:
        logger.error(f"Error creating force channel button: {e}")
        return None

# Callback Handlers
@StreamBot.on_callback_query(filters.regex(r"^help_command$"))
async def help_callback(client: Client, callback_query: CallbackQuery):
    """Handler for Help button."""
    try:
        await callback_query.answer()
        
        # Build buttons list - About button first
        buttons = [[InlineKeyboardButton(MSG_BUTTON_ABOUT, callback_data="about_command")]]
        
        # Add force channel button if configured
        force_button = await get_force_channel_button(client)
        if force_button:
            buttons.append(force_button)
        
        # Add close button at the end
        buttons.append([InlineKeyboardButton("🗙 Close", callback_data="close_panel")])
        
        await callback_query.message.edit_text(
            text=MSG_HELP,
            reply_markup=InlineKeyboardMarkup(buttons),
            link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
        
        logger.debug(f"User {callback_query.from_user.id} accessed help panel.")
        
    except MessageNotModified:
        logger.debug(f"Help panel already displayed for user {callback_query.from_user.id}")
    except Exception as e:
        await handle_callback_error(callback_query, e, "help_callback")
    finally:
        raise StopPropagation

@StreamBot.on_callback_query(filters.regex(r"^about_command$"))
async def about_callback(client: Client, callback_query: CallbackQuery):
    """Handler for About button."""
    try:
        await callback_query.answer()

        buttons = [
            [InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command")],
            [InlineKeyboardButton(MSG_BUTTON_GITHUB, url="https://github.com/fyaz05/FileToLink"),
             InlineKeyboardButton("🗙 Close", callback_data="close_panel")]
        ]
        
        await callback_query.message.edit_text(
            text=MSG_ABOUT,
            reply_markup=InlineKeyboardMarkup(buttons),
            link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
        
        logger.debug(f"User {callback_query.from_user.id} accessed about panel.")
        
    except MessageNotModified:
        logger.debug(f"About panel already displayed for user {callback_query.from_user.id}")
    except Exception as e:
        await handle_callback_error(callback_query, e, "about_callback")
    finally:
        raise StopPropagation

@StreamBot.on_callback_query(filters.regex(r"^restart_broadcast$"))
@owner_only
async def restart_broadcast_callback(client: Client, callback_query: CallbackQuery):
    """Handler for Restart Broadcast button."""
    try:
        await callback_query.answer(MSG_ERROR_BROADCAST_RESTART, show_alert=True)
        
        # Create back and close buttons
        buttons = [
            [InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command"),
             InlineKeyboardButton("🗙 Close", callback_data="close_panel")]
        ]
        
        await callback_query.message.edit_text(
            MSG_ERROR_BROADCAST_INSTRUCTION,
            reply_markup=InlineKeyboardMarkup(buttons),
            link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
        
        logger.debug(f"User {callback_query.from_user.id} viewed broadcast restart instruction.")
        
    except Exception as e:
        await handle_callback_error(callback_query, e, "restart_broadcast_callback")
    finally:
        raise StopPropagation

@StreamBot.on_callback_query(filters.regex(r"^close_panel$"))
async def close_panel_callback(client: Client, callback_query: CallbackQuery):
    """Handler for Close button - deletes current panel and command message."""
    try:
        await callback_query.answer()
        
        # Delete the current panel message
        await callback_query.message.delete()
        logger.debug(f"User {callback_query.from_user.id} closed panel")
        
        # Clean up original command message and replies
        if callback_query.message.reply_to_message:
            try:
                ctx = callback_query.message.reply_to_message
                await ctx.delete()
                if ctx.reply_to_message:
                    await ctx.reply_to_message.delete()
            except Exception as e:
                logger.warning(f"Error deleting command messages: {e}")
                
    except Exception as e:
        await handle_callback_error(callback_query, e, "close_panel_callback")
    finally:
        raise StopPropagation

# Fallback handler for unmatched callback queries
@StreamBot.on_callback_query(group=999)
async def fallback_callback(client: Client, callback_query: CallbackQuery):
    """Fallback handler for any unmatched callback query."""
    try:
        logger.debug(f"Unhandled callback query: {callback_query.data} from user {callback_query.from_user.id}")
        await callback_query.answer(MSG_ERROR_CALLBACK_UNSUPPORTED, show_alert=True)
    except Exception as e:
        logger.error(f"Error in fallback_callback: {e}")
    raise StopPropagation
