# Thunder/bot/plugins/common.py

import html
import time

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from Thunder.bot import StreamBot
from Thunder.utils.bot_utils import gen_dc_txt, get_user, log_newusr, reply_user_err
from Thunder.utils.commands import build_help_text
from Thunder.utils.decorators import GATES_INFO, GATES_START, preflight
from Thunder.utils.file_properties import get_fname, get_fsize, parse_fid
from Thunder.utils.force_channel import get_force_info
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.logger import logger
from Thunder.utils.messages import (
    MSG_ABOUT,
    MSG_BUTTON_ABOUT,
    MSG_BUTTON_CLOSE,
    MSG_BUTTON_GET_HELP,
    MSG_BUTTON_GITHUB,
    MSG_BUTTON_JOIN_CHANNEL,
    MSG_BUTTON_VIEW_PROFILE,
    MSG_COMMUNITY_CHANNEL,
    MSG_DC_ANON_ERROR,
    MSG_DC_FILE_ERROR,
    MSG_DC_FILE_INFO,
    MSG_DC_INVALID_USAGE,
    MSG_DC_UNKNOWN,
    MSG_ERROR_USER_INFO,
    MSG_FILE_TYPE_ANIMATION,
    MSG_FILE_TYPE_AUDIO,
    MSG_FILE_TYPE_DOCUMENT,
    MSG_FILE_TYPE_PHOTO,
    MSG_FILE_TYPE_STICKER,
    MSG_FILE_TYPE_UNKNOWN,
    MSG_FILE_TYPE_VIDEO,
    MSG_FILE_TYPE_VIDEO_NOTE,
    MSG_FILE_TYPE_VOICE,
    MSG_PING_RESPONSE,
    MSG_PING_START,
    MSG_TOKEN_ACTIVATED,
    MSG_TOKEN_FAILED,
    MSG_TOKEN_INVALID,
    MSG_WELCOME,
)
from Thunder.utils.safe_call import edit_safe, reply_safe
from Thunder.utils.tokens import consume
from Thunder.vars import Var

# M7: surfaces that interpolate user-controlled values are HTML now;
# every interpolation is html.escape()d.


@StreamBot.on_message(filters.command("start") & filters.private)
async def start_command(bot: Client, msg: Message):
    # M12: /start runs banned + private-mode only so the activation flow
    # stays reachable for token-gated users.
    if await preflight(bot, msg, gates=GATES_START) is None:
        return
    user = msg.from_user
    if user:
        await log_newusr(bot, user.id, user.first_name)

    if len(msg.command) == 2:
        payload = msg.command[1]

        if payload == "start":
            pass
        else:
            # M8: atomic activation -- exactly one concurrent /start wins.
            status, hours = await consume(payload, user.id)
            if status == "wrong_user":
                return await reply_safe(
                    msg,
                    text=MSG_TOKEN_FAILED.format(
                        reason="This activation link is not for your account."
                    ),
                )
            if status == "already":
                return await reply_safe(
                    msg,
                    text=MSG_TOKEN_FAILED.format(reason="Token has already been activated."),
                )
            if status == "ok":
                return await reply_safe(msg, text=MSG_TOKEN_ACTIVATED.format(duration_hours=hours))
            return await reply_safe(msg, text=MSG_TOKEN_INVALID)

    txt = MSG_WELCOME.format(
        user_name=html.escape(user.first_name or "Unknown") if user else "Unknown"
    )
    link, title = await get_force_info(bot)
    if link:
        txt += "\n\n" + MSG_COMMUNITY_CHANNEL.format(channel_title=html.escape(title or "Channel"))

    btns = [
        [
            InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command"),
            InlineKeyboardButton(MSG_BUTTON_ABOUT, callback_data="about_command"),
        ],
        [
            InlineKeyboardButton(MSG_BUTTON_GITHUB, url="https://github.com/fyaz05/FileToLink/"),
            InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel"),
        ],
    ]

    if link:
        btns.append(
            [InlineKeyboardButton(MSG_BUTTON_JOIN_CHANNEL.format(channel_title=title), url=link)]
        )

    await _send_html(msg, txt, btns)


async def _send_html(msg: Message, txt: str, btns):
    from pyrogram import enums

    await reply_safe(
        msg, text=txt, parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(btns)
    )


@StreamBot.on_message(filters.command("help") & filters.private)
async def help_command(bot: Client, msg: Message):
    if await preflight(bot, msg, gates=GATES_INFO) is None:
        return
    if msg.from_user:
        await log_newusr(bot, msg.from_user.id, msg.from_user.first_name)

    txt = build_help_text(Var.MAX_BATCH_FILES)
    btns = [[InlineKeyboardButton(MSG_BUTTON_ABOUT, callback_data="about_command")]]

    link, title = await get_force_info(bot)
    if link:
        btns.append(
            [InlineKeyboardButton(MSG_BUTTON_JOIN_CHANNEL.format(channel_title=title), url=link)]
        )

    btns.append([InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")])
    await _send_html(msg, txt, btns)


@StreamBot.on_message(filters.command("about") & filters.private)
async def about_command(bot: Client, msg: Message):
    if await preflight(bot, msg, gates=GATES_INFO) is None:
        return
    if msg.from_user:
        await log_newusr(bot, msg.from_user.id, msg.from_user.first_name)

    btns = [
        [InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command")],
        [
            InlineKeyboardButton(MSG_BUTTON_GITHUB, url="https://github.com/fyaz05/FileToLink/"),
            InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel"),
        ],
    ]

    await _send_html(msg, MSG_ABOUT, btns)


async def send_user_dc(msg: Message, user: User):
    txt = await gen_dc_txt(user)
    url = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"
    btns = [
        [InlineKeyboardButton(MSG_BUTTON_VIEW_PROFILE, url=url)],
        [InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")],
    ]
    await reply_safe(
        msg,
        text=txt,
        # DC templates are HTML (M7): pin the parse mode so pyrofork's
        # DEFAULT markdown pre-pass cannot reinterpret user data
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(btns),  # type: ignore[arg-type]
    )


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
            "video_note": MSG_FILE_TYPE_VIDEO_NOTE,
        }

        file_type = next((attr for attr in type_map if getattr(file_msg, attr, None)), "unknown")
        type_display = type_map.get(file_type, MSG_FILE_TYPE_UNKNOWN)

        dc_id: int | str = MSG_DC_UNKNOWN
        fid = parse_fid(file_msg)
        if fid:
            dc_id = fid.dc_id

        txt = MSG_DC_FILE_INFO.format(
            # file_name is attacker-controlled; template is HTML (M7)
            file_name=html.escape(fname, quote=False),
            file_size=fsize,
            file_type=type_display,
            dc_id=dc_id,
        )

        btns = [[InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]]
        await reply_safe(
            msg,
            text=txt,
            parse_mode=ParseMode.HTML,  # template is HTML (M7); skip md pre-pass
            reply_markup=InlineKeyboardMarkup(btns),  # type: ignore[arg-type]
        )

    except Exception as e:
        logger.error(f"File DC error: {e}", exc_info=True)
        await reply_user_err(msg, MSG_DC_FILE_ERROR)


@StreamBot.on_message(filters.command("dc"))
async def dc_command(bot: Client, msg: Message):
    # Gate chain for /dc (banned -> private-mode, then force-sub).  The token
    # gate is intentionally NOT applied: /dc is informational, and applying
    # it here would lock token-gated users out of diagnostics.
    if await preflight(bot, msg, gates=GATES_START) is None:
        return
    from Thunder.utils.decorators import force_sub_gate

    if not await force_sub_gate(bot, msg):
        return
    if not msg.from_user and not msg.reply_to_message:
        return await reply_user_err(msg, MSG_DC_ANON_ERROR)

    args = (msg.text or msg.caption or "").strip().split(maxsplit=1)
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
    if await preflight(bot, msg, gates=GATES_START) is None:
        return
    from Thunder.utils.decorators import force_sub_gate

    if not await force_sub_gate(bot, msg):
        return
    start = time.time()
    try:
        sent = await reply_safe(msg, text=MSG_PING_START)
    except Exception:
        return
    end = time.time()
    ms = (end - start) * 1000

    btns = [
        [
            InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command"),
            InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel"),
        ]
    ]

    try:
        await edit_safe(
            sent,
            MSG_PING_RESPONSE.format(time_taken_ms=ms),
            reply_markup=InlineKeyboardMarkup(btns),  # type: ignore[arg-type]
            disable_web_page_preview=True,
        )
    except MessageNotModified:
        pass
    except Exception as e:
        logger.debug(f"Could not edit ping message: {e}")
