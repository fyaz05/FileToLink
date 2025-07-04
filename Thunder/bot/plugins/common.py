# Thunder/bot/plugins/common.py

import time
from datetime import datetime, timedelta

from pyrogram import Client, filters
from pyrogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                            LinkPreviewOptions, Message, User)

from Thunder.bot import StreamBot
from Thunder.utils.bot_utils import (gen_dc_txt, get_user, log_newusr, reply,
                                     reply_user_err)
from Thunder.utils.database import db
from Thunder.utils.decorators import check_banned
from Thunder.utils.file_properties import get_fname, get_fsize, parse_fid
from Thunder.utils.force_channel import force_channel_check, get_force_info
from Thunder.utils.handler import handle_flood_wait
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.logger import logger
from Thunder.utils.messages import *
from Thunder.vars import Var

@StreamBot.on_message(filters.command("start") & filters.private)
async def start_command(bot: Client, msg: Message):
    if not await check_banned(bot, msg):
        return
    user = msg.from_user
    if user:
        await log_newusr(bot, user.id, user.first_name)
    
    if len(msg.command) == 2:
        payload = msg.command[1]
        
        if payload == "start":
            pass
        else:
            token = await db.token_col.find_one({"token": payload})
            if token:
                if token["user_id"] != user.id:
                    return await handle_flood_wait(msg.reply_text, text=MSG_TOKEN_FAILED.format(
                        reason="This activation link is not for your account.",
                        error_id=str(int(time.time()))[-8:]
                    ))
                
                if token.get("activated"):
                    return await handle_flood_wait(msg.reply_text, text=MSG_TOKEN_FAILED.format(
                        reason="Token has already been activated.",
                        error_id=str(int(time.time()))[-8:]
                    ))
                
                now = datetime.utcnow()
                exp = now + timedelta(hours=Var.TOKEN_TTL_HOURS)
                
                await db.token_col.update_one(
                    {"token": payload, "user_id": user.id},
                    {"$set": {"activated": True, "created_at": now, "expires_at": exp}}
                )
                
                hrs = round((exp - now).total_seconds() / 3600, 1)
                return await handle_flood_wait(msg.reply_text, text=MSG_TOKEN_ACTIVATED.format(duration_hours=hrs))
            else:
                return await handle_flood_wait(msg.reply_text, text=MSG_TOKEN_INVALID)
            
    txt = MSG_WELCOME.format(user_name=user.first_name if user else "Unknown")
    link, title = await get_force_info(bot)
    if link:
        txt += f"\n\n{MSG_COMMUNITY_CHANNEL.format(channel_title=title)}"
    
    btns = [
        [InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command"),
         InlineKeyboardButton(MSG_BUTTON_ABOUT, callback_data="about_command")],
        [InlineKeyboardButton(MSG_BUTTON_GITHUB, url="https://github.com/fyaz05/FileToLink/"),
         InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]
    ]
    
    if link:
        btns.append([InlineKeyboardButton(MSG_BUTTON_JOIN_CHANNEL.format(channel_title=title), url=link)])
    
    await handle_flood_wait(msg.reply_text, text=txt, reply_markup=InlineKeyboardMarkup(btns))

@StreamBot.on_message(filters.command("help") & filters.private)
async def help_command(bot: Client, msg: Message):
    if not await check_banned(bot, msg):
        return
    if msg.from_user:
        await log_newusr(bot, msg.from_user.id, msg.from_user.first_name)
    
    txt = MSG_HELP.format(max_files=Var.MAX_BATCH_FILES)
    btns = [[InlineKeyboardButton(MSG_BUTTON_ABOUT, callback_data="about_command")]]
    
    link, title = await get_force_info(bot)
    if link:
        btns.append([InlineKeyboardButton(MSG_BUTTON_JOIN_CHANNEL.format(channel_title=title), url=link)])
    
    btns.append([InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")])
    await handle_flood_wait(msg.reply_text, text=txt, reply_markup=InlineKeyboardMarkup(btns))

@StreamBot.on_message(filters.command("about") & filters.private)
async def about_command(bot: Client, msg: Message):
    if not await check_banned(bot, msg):
        return
    if msg.from_user:
        await log_newusr(bot, msg.from_user.id, msg.from_user.first_name)
    
    btns = [
        [InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command")],
        [InlineKeyboardButton(MSG_BUTTON_GITHUB, url="https://github.com/fyaz05/FileToLink/"),
         InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]
    ]
    
    await handle_flood_wait(msg.reply_text, text=MSG_ABOUT, reply_markup=InlineKeyboardMarkup(btns))

async def send_user_dc(msg: Message, user: User):
    txt = await gen_dc_txt(user)
    url = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"
    btns = [
        [InlineKeyboardButton(MSG_BUTTON_VIEW_PROFILE, url=url)],
        [InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]
    ]
    await handle_flood_wait(msg.reply_text, text=txt, reply_markup=InlineKeyboardMarkup(btns))

async def send_file_dc(msg: Message, file_msg: Message):
    try:
        fname = get_fname(file_msg) or "Untitled File"
        fsize = humanbytes(get_fsize(file_msg))
        
        type_map = {
            "document": MSG_FILE_TYPE_DOCUMENT,
            "photo": MSG_FILE_TYPE_PHOTO,
            "video": MSG_FILE_TYPE_VIDEO,
            "audio": MSG_FILE_TYPE_AUDIO,
            "voice": MSG_FILE_TYPE_VOICE,
            "sticker": MSG_FILE_TYPE_STICKER,
            "animation": MSG_FILE_TYPE_ANIMATION,
            "video_note": MSG_FILE_TYPE_VIDEO_NOTE
        }
        
        file_type = next((attr for attr in type_map if getattr(file_msg, attr, None)), "unknown")
        type_display = type_map.get(file_type, MSG_FILE_TYPE_UNKNOWN)
        
        dc_id = MSG_DC_UNKNOWN
        fid = parse_fid(file_msg)
        if fid:
            dc_id = fid.dc_id
        
        txt = MSG_DC_FILE_INFO.format(
            file_name=fname,
            file_size=fsize,
            file_type=type_display,
            dc_id=dc_id
        )
        
        btns = [[InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]]
        await handle_flood_wait(msg.reply_text, text=txt, reply_markup=InlineKeyboardMarkup(btns))
        
    except Exception as e:
        logger.error(f"File DC error: {e}", exc_info=True)
        await reply_user_err(msg, MSG_DC_FILE_ERROR)

@StreamBot.on_message(filters.command("dc"))
async def dc_command(bot: Client, msg: Message):
    if not await check_banned(bot, msg):
        return
    if not await force_channel_check(bot, msg):
        return
    if not msg.from_user and not msg.reply_to_message:
        return await reply_user_err(msg, MSG_DC_ANON_ERROR)
    
    args = msg.text.strip().split(maxsplit=1)
    if len(args) > 1:
        user = await get_user(bot, args[1].strip())
        if user:
            await send_user_dc(msg, user)
        else:
            await reply_user_err(msg, MSG_ERROR_USER_INFO)
        return
    
    if msg.reply_to_message:
        ref = msg.reply_to_message
        if ref.media:
            await send_file_dc(msg, ref)
        elif ref.from_user:
            await send_user_dc(msg, ref.from_user)
        else:
            await reply_user_err(msg, MSG_DC_INVALID_USAGE)
        return
    
    if msg.from_user:
        await send_user_dc(msg, msg.from_user)
    else:
        await reply_user_err(msg, MSG_DC_ANON_ERROR)

@StreamBot.on_message(filters.command("ping") & filters.private)
async def ping_command(bot: Client, msg: Message):
    if not await check_banned(bot, msg):
        return
    if not await force_channel_check(bot, msg):
        return
    start = time.time()
    sent = await handle_flood_wait(msg.reply_text, text=MSG_PING_START)
    end = time.time()
    ms = (end - start) * 1000
    
    btns = [
        [InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command"),
         InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]
    ]
    
    await handle_flood_wait(sent.edit_text,
        MSG_PING_RESPONSE.format(time_taken_ms=ms),
        reply_markup=InlineKeyboardMarkup(btns),
        link_preview_options=LinkPreviewOptions(is_disabled=True)
    )
