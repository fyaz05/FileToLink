# Thunder/utils/messages.py

# =====================================================================================
# ====== ERROR MESSAGES ======
# =====================================================================================

# ------ General Errors ------
MSG_ERROR_GENERIC = (
    "⚠️ **Oops!** Something went wrong. Please try again. If the issue persists, contact support."
)
MSG_ERROR_USER_INFO = "❗ **User Not Found:** Couldn't find user. Please check the ID or Username."

# ------ User Input & Validation Errors ------
MSG_INVALID_USER_ID = "❌ **Invalid User ID:** Please provide a numeric user ID."
MSG_ERROR_START_BOT = "⚠️ You need to start the bot in private first to use this command.\n👉 [Click here]({invite_link}) to start a private chat."
MSG_ERROR_REPLY_FILE = "⚠️ Please use the /link command in reply to a file."
MSG_ERROR_NO_FILE = "⚠️ The message you're replying to does not contain any file."
MSG_ERROR_INVALID_NUMBER = "⚠️ **Invalid number specified.**"
MSG_ERROR_NUMBER_RANGE = "⚠️ **Please specify a number between 1 and {max_files}.**"
MSG_ERROR_DM_FAILED = "⚠️ I couldn't send you a Direct Message. Please start the bot first."

# H7/M12: fail-closed + private-mode surfaces
MSG_ERROR_TEMP = (
    "⚠️ **Temporary service error.** Access checks are unavailable right now, "
    "so your request was rejected. Please try again in a few minutes."
)
MSG_PRIVATE_MODE_DENIED = (
    "🔒 **Private bot.** This instance is restricted to authorized users. "
    "If you believe you should have access, contact the owner."
)

# ------ File & Media Errors ------
MSG_ERROR_PROCESSING_MEDIA = "⚠️ **Oops!** Something went wrong while processing your media. Please try again. If the issue persists, contact support."

# ------ Admin Action Errors (Ban, Auth, etc.) ------
MSG_AUTHORIZE_FAILED = "❌ **Authorization Failed:** Could not authorize user `{user_id}`."
MSG_DEAUTHORIZE_FAILED = (
    "❌ **Deauthorization Failed:** User `{user_id}` was not authorized or an error occurred."
)
MSG_TOKEN_FAILED = (
    "⚠️ **Token Activation Failed!**\n\n"
    "> ❗ Reason: {reason}\n\n"
    "🔑 Please check your token or contact support."
)
MSG_SHELL_ERROR = """**❌ Shell Command Error ❌**
<pre>{error}</pre>"""

# ------ System & Bot Errors ------
MSG_ERROR_NOT_ADMIN = "⚠️ **Admin Required:** I need admin privileges to work here."
MSG_DC_INVALID_USAGE = (
    "🤔 **Invalid Usage:** Please reply to a user's message or a media file to get DC info."
)
MSG_DC_ANON_ERROR = "😥 **Cannot Get Your DC Info:** Unable to identify you. This command might not work for anonymous users."
MSG_DC_FILE_ERROR = (
    "⚙️ **Error Getting File DC Info:** Could not fetch details. File might be inaccessible."
)
MSG_STATUS_ERROR = "❌ **Status Error:** Could not retrieve system status."
MSG_DB_ERROR = "❌ **Database Error:** Could not retrieve user count."
MSG_CRITICAL_ERROR = (
    "🚨 **Critical Media Processing Error** 🚨\n\n"
    "> ⚠️ Details:\n```\n{error}\n```\n\n"
    "Please investigate immediately! (ID: {error_id})"
)

# =====================================================================================
# ====== ADMIN MESSAGES ======
# =====================================================================================

# ------ Ban/Unban (HTML for reason surfaces: reason is user-controlled, M7) ------
MSG_DECORATOR_BANNED = (
    "You are currently banned and cannot use this bot.\nReason: {reason}\nBanned on: {ban_time}"
)
MSG_BAN_USAGE = "⚠️ **Usage:** /ban [user_id] [reason]"
MSG_CANNOT_BAN_OWNER = "❌ **Cannot ban an owner.**"
MSG_ADMIN_USER_BANNED = "✅ <b>User {user_id} has been banned.</b>"
MSG_BAN_REASON_SUFFIX = "\n📝 <b>Reason:</b> {reason}"
MSG_ADMIN_NO_BAN_REASON = "No reason provided"
MSG_USER_BANNED_NOTIFICATION = "🚫 **You have been banned from using this bot.**"
MSG_UNBAN_USAGE = "⚠️ **Usage:** /unban <user_id>"
MSG_ADMIN_USER_UNBANNED = "✅ <b>User {user_id} has been unbanned.</b>"
MSG_USER_UNBANNED_NOTIFICATION = "🎉 **You have been unbanned from using this bot.**"
MSG_USER_NOT_IN_BAN_LIST = "ℹ️ **User {user_id} was not found in the ban list."
MSG_CHANNEL_BANNED = "✅ <b>Channel {channel_id} has been banned.</b>"
MSG_CHANNEL_BANNED_REASON_SUFFIX = "\n📝 <b>Reason:</b> {reason}"
MSG_CHANNEL_UNBANNED = "✅ **Channel {channel_id} has been unbanned.**"
MSG_CHANNEL_NOT_BANNED = "ℹ️ **Channel {channel_id} was not found in the ban list.**"

# ------ Token & Authorization ------
MSG_AUTHORIZE_USAGE = "🔑 **Usage:** `/authorize <user_id>`"
MSG_DEAUTHORIZE_USAGE = "🔒 **Usage:** `/deauthorize <user_id>`"
MSG_AUTHORIZE_SUCCESS = (
    "✅ **User Authorized!**\n\n> 👤 User ID: `{user_id}`\n> 🔑 Access: Permanent"
)
MSG_DEAUTHORIZE_SUCCESS = (
    "✅ **User Deauthorized!**\n\n> 👤 User ID: `{user_id}`\n> 🔒 Access: Revoked"
)
MSG_TOKEN_ACTIVATED = (
    "✅ Token successfully activated!\n\n⏳ This token is valid for {duration_hours} hours."
)
MSG_TOKEN_INVALID = (
    "🚫 **Expired or Invalid Token.** Please click the button below to activate your access token."
)
MSG_NO_AUTH_USERS = "ℹ️ **No Authorized Users Found:** The list is currently empty."
MSG_AUTH_USER_INFO = """{i}. 👤: {display_name}
   • User ID: <code>{user_id}</code>
   • Authorized by: <code>{authorized_by}</code>
   • Date: <code>{auth_time}</code>\n\n"""
MSG_ADMIN_AUTH_LIST_HEADER = "🔐 <b>Authorized Users List</b>\n\n"

# ------ Shell Commands (guarded by ENABLE_SHELL, L10) ------
MSG_SHELL_USAGE = "<b>Usage:</b>\n/shell <command>\n\n<b>Example:</b>\n/shell ls -l"
MSG_SHELL_DISABLED = (
    "⛔ <b>Shell is disabled.</b>\n\n"
    "Set <code>ENABLE_SHELL=True</code> in the environment to enable this "
    "owner-only command."
)
MSG_SHELL_EXECUTING = "Executing Command... ⚙️\n<pre>{command}</pre>"
MSG_SHELL_OUTPUT = """**Shell Command Output:**
<pre>{output}</pre>"""
MSG_SHELL_OUTPUT_STDOUT = "<b>[stdout]:</b>\n<pre>{output}</pre>"
MSG_SHELL_OUTPUT_STDERR = "<b>[stderr]:</b>\n<pre>{error}</pre>"
MSG_SHELL_NO_OUTPUT = "✅ <b>Command Executed:</b> No output."

# ------ Admin View & Control ------

MSG_WORKLOAD_ITEM = "   {bot_name}: {load}\n"
MSG_ADMIN_RESTART_DONE = "✅ **Restart Successful!**"
MSG_RESTARTING = "♻️ **Updating and Restarting Bot...**\n\n> ⏳ Please wait a moment."
MSG_LOG_FILE_CAPTION = "📄 **System Logs**"

MSG_LOG_FILE_EMPTY = "ℹ️ **Log File Empty:** No data found in the log file."
MSG_LOG_FILE_MISSING = "⚠️ **Log File Missing:** Could not find the log file."

# =====================================================================================
# ====== BUTTON TEXTS (User-facing) ======
# =====================================================================================

MSG_BUTTON_STREAM_NOW = "🖥️ Stream"
MSG_BUTTON_DOWNLOAD = "🚀 Download"
MSG_BUTTON_GET_HELP = "📖 Get Help"
MSG_BUTTON_CANCEL_BROADCAST = "🛑 Cancel Broadcast"
MSG_BUTTON_VIEW_PROFILE = "👤 View User Profile"
MSG_BUTTON_ABOUT = "ℹ️ About Bot"
MSG_BUTTON_JOIN_CHANNEL = "📢 Join {channel_title}"
MSG_BUTTON_GITHUB = "🛠️ GitHub"
MSG_BUTTON_START_CHAT = "📩 Start Chat"
MSG_BUTTON_CLOSE = "✖ Close"


# =====================================================================================
# ====== COMMAND RESPONSES (User-facing) ======
# =====================================================================================

# M7: welcome/help/about are HTML and interpolate html.escape()d values.
MSG_WELCOME = (
    "🌟 <b>Welcome, {user_name}!</b> 🌟\n\n"
    "I'm <b>Thunder File to Link Bot</b> ⚡\n"
    "I generate direct download and streaming links for your files.\n\n"
    "<b>How to use:</b>\n"
    "1. Send any file to me for private links.\n"
    "2. In groups, reply to a file with /link.\n\n"
    "» Use /help for all commands and detailed information.\n\n"
    "🚀 Send a file to begin!"
)

MSG_HELP_INTRO = (
    "📘 <b>Thunder Bot - Help Guide</b> 📖\n\n"
    "How to get direct download &amp; streaming links:\n\n"
    "<b>🚀 Private Chat (with me):</b>\n"
    "> 1. Send me <b>any file</b> (document, video, audio, photo, etc.).\n"
    "> 2. I'll instantly reply with your links! ⚡\n\n"
    "<b>👥 Using in Groups:</b>\n"
    "> • Reply to any file with /link.\n"
    "> • <b>Batch Mode:</b> Reply to the <b>first</b> file with <code>/link &lt;number&gt;</code> "
    "(e.g., <code>/link 5</code> for 5 files, up to {max_files}).\n"
    "> • Bot needs administrator rights in the group to function.\n"
    "> • Links are posted in the group &amp; sent to you privately.\n\n"
    "<b>📢 Using in Channels:</b>\n"
    "> • Add me as an administrator with necessary permissions.\n"
    "> • I can be configured to auto-detect new media files.\n"
    "> • Inline stream/download buttons can be added to files automatically.\n"
    "> • Files from banned channels (owner configuration) are rejected.\n"
    "> • Auto-posting links if the bot has admin privileges with delete rights.\n"
)

# M1: the commands section is generated from bot/registry.py.
MSG_HELP_COMMANDS_HEADER = "\n<b>⚙️ Available Commands:</b>\n"
MSG_HELP_COMMAND_ROW = "> /{name} - {description}\n"

MSG_HELP_TIPS = (
    "\n<b>💡 Pro Tips:</b>\n"
    "> • You can forward files from other chats directly to me.\n"
    "> • If you encounter a rate limit message, please wait the specified time. ⏳\n"
    "> • For /link in groups to work reliably (and for private link delivery), "
    "ensure you've started a private chat with me first.\n"
    "> • Processing batch files might take a bit longer. Please be patient. 🐌\n\n"
    "❓ Questions? Please ask in our support group!"
)

MSG_ABOUT = (
    "🌟 <b>About Thunder File to Link Bot</b> ℹ️\n\n"
    "I'm your go-to bot for <b>instant download &amp; streaming!</b> ⚡\n\n"
    "<b>🚀 Key Features:</b>\n"
    "> <b>Instant Links:</b> Get your links within seconds.\n"
    "> <b>Online Streaming:</b> Watch videos or listen to audio directly (for supported formats).\n"
    "> <b>Universal File Support:</b> Handles documents, videos, audio, photos, and more.\n"
    "> <b>High-Speed Access:</b> Optimized for fast link generation and file access.\n"
    "> <b>Secure &amp; Reliable:</b> Your files are handled with care during processing.\n"
    "> <b>User-Friendly Interface:</b> Designed for ease of use on any device.\n"
    "> <b>Efficient Processing:</b> Built for speed and reliability.\n"
    "> <b>Batch Mode:</b> Process multiple files at once in groups using <code>/link &lt;number&gt;</code>.\n"
    "> <b>Versatile Usage:</b> Works in private chats, groups, and channels (with admin setup).\n\n"
    "💖 If you find me useful, please consider sharing me with your friends!"
)

# ------ Ping ------
MSG_PING_START = "🛰️ **Pinging...** Please wait."
MSG_PING_RESPONSE = (
    "☁️ **PONG! Bot is Online!** ⚡\n\n"
    "> ⏱️ **Ping:** {time_taken_ms:.2f} ms\n"
    "> 🤖 **Bot Status:** `Active`"
)

# ------ DC Info ------
MSG_DC_USER_INFO = (
    "📍 **Information**\n"
    "> 👤 **User:** [{user_name}](tg://user?id={user_id})\n"
    "> 🆔 **User ID:** `{user_id}`\n"
    "> 🌍 **DC ID:** `{dc_id}`"
)

MSG_DC_FILE_INFO = (
    "🗂️ **File Information**\n"
    ">`{file_name}`\n"
    "💾 **File Size:** `{file_size}`\n"
    "📁 **File Type:** `{file_type}`\n"
    "🌍 **DC ID:** `{dc_id}`"
)

MSG_DC_UNKNOWN = "Unknown"

# ------ File Link Generation (HTML: file names are user-controlled, M7) ------
MSG_DM_SINGLE_PREFIX = "📬 <b>From {chat_title}</b>\n"
MSG_LINKS = (
    "✨ <b>Your Links are Ready!</b> ✨\n\n"
    "> <code>{file_name}</code>\n\n"
    "📂 <b>File Size:</b> <code>{file_size}</code>\n\n"
    "🚀 <b>Download Link:</b>\n<code>{download_link}</code>\n\n"
    "🖥️ <b>Stream Link:</b>\n<code>{stream_link}</code>\n\n"
    "⌛️ <b>Note: Links remain active while the bot is running and the file is accessible.</b>"
)

# L2: appended to link messages only when FILE_TTL_DAYS > 0
MSG_FILE_EXPIRY_NOTE = "⏳ <b>Files expire after {days} of inactivity.</b>"
MSG_FILE_TTL_DAYS_LABEL = "{days} day(s)"

# =====================================================================================
# ====== USER NOTIFICATIONS ======
# =====================================================================================

MSG_NEW_USER = (
    "✨ <b>New User Alert!</b> ✨\n"
    '> 👤 <b>Name:</b> <a href="tg://user?id={user_id}">{first_name}</a>\n'
    "> 🆔 <b>User ID:</b> <code>{user_id}</code>\n\n"
)
MSG_COMMUNITY_CHANNEL = "📢 <b>{channel_title}:</b> 🔒 Join this channel to use the bot."

# =====================================================================================
# ====== PROCESSING MESSAGES ======
# =====================================================================================

# ------ General File Processing ------
MSG_PROCESSING_REQUEST = "⏳ **Processing your request...**"
MSG_PROCESSING_FILE = "⏳ **Processing your file...**"
MSG_NEW_FILE_REQUEST = (
    '> 👤 <b>Source:</b> <a href="tg://user?id={id_}">{source_info}</a>\n'
    "> 🆔 <b>ID:</b> <code>{id_}</code>\n\n"
    "🚀 <b>Download:</b> <code>{online_link}</code>\n\n"
    "🖥️ <b>Stream:</b> <code>{stream_link}</code>"
)

# ------ Batch Processing (M4b: skipped counts non-media files) ------
MSG_PROCESSING_BATCH = "♻️ **Processing Batch {batch_number}/{total_batches}** ({file_count} files)"
MSG_PROCESSING_STATUS = "📊 **Processing Files:** {processed}/{total} complete, {failed} failed"
MSG_BATCH_LINKS_READY = "🔗 Here are your {count} download links:"
MSG_DM_BATCH_PREFIX = "📬 <b>Batch Links from {chat_title}</b>\n"
MSG_PROCESSING_RESULT = (
    "✅ **Process Complete:** {processed}/{total} files processed successfully, {failed} failed"
)

# =====================================================================================
# ====== BROADCAST MESSAGES ======
# =====================================================================================

MSG_BROADCAST_START = "📣 **Starting Broadcast...**\n\n> ⏳ Please wait for completion."
MSG_BROADCAST_COMPLETE = (
    "📢 **Broadcast Completed Successfully!** 📢\n\n"
    "⏱️ **Duration:** `{elapsed_time}`\n"
    "👥 **Total Users:** `{total_users}`\n"
    "✅ **Successful Deliveries:** `{successes}`\n"
    "❌ **Failed Deliveries:** `{failures}`\n"
    "🗑️ **Accounts Removed (Blocked/Deactivated):** `{deleted_accounts}`\n"
)
MSG_BROADCAST_CANCEL = (
    "🛑 **Cancelling Broadcast:** `{broadcast_id}`\n\n> ⏳ Stopping operations..."
)
MSG_INVALID_BROADCAST_CMD = "Please reply to the message you want to broadcast."
MSG_BROADCAST_USAGE = (
    "📣 **Broadcast Command Usage:**\n\n"
    "`/broadcast` - Broadcast to all users\n"
    "`/broadcast authorized` - Broadcast to authorized users only\n"
    "`/broadcast regular` - Broadcast to regular (non-authorized) users only\n\n"
    "**Note:** Reply to the message you want to broadcast."
)

# =====================================================================================
# ====== PERMISSION MESSAGES ======
# =====================================================================================

MSG_ERROR_UNAUTHORIZED = "You are not authorized to view this information."
MSG_ERROR_BROADCAST_RESTART = "Please use the /broadcast command to start a new broadcast."
MSG_ERROR_BROADCAST_INSTRUCTION = "To start a new broadcast, use the /broadcast command and reply to the message you want to broadcast."
MSG_ERROR_CALLBACK_UNSUPPORTED = "This button is not active or no longer supported."
MSG_ERROR_CLOSE_NOT_ALLOWED = (
    "⚠️ Only the person who triggered this panel (or the owner) can close it."
)

# =====================================================================================
# ====== RATE LIMITING MESSAGES ======
# =====================================================================================

MSG_RATE_LIMIT_QUEUE_PRIORITY = (
    "⚡ You're in the **Priority Queue!**\n\n"
    "> ⏳ **Estimated Wait:** `~{wait_estimate} minute{s}`\n"
    "> 🚀 **Status:** In Queue"
)

MSG_RATE_LIMIT_QUEUE_REGULAR = (
    "⏳ **Rate Limit Reached!**\n\n"
    "> ⌛ **Estimated Wait:** `~{wait_estimate} minute{s1}`\n"
    "> 📊 **Limit:** `{max_requests} files per {time_window} minute{s2}`\n"
    "> 🔄 **Status:** In Queue"
)

MSG_RATE_LIMIT_QUEUE_FULL = (
    "⚠️ **Service Busy!** The processing queue is currently full.\n\n"
    "> 🕒 **Please try again in:** `~{wait_estimate} minute{s}`\n"
    "> 💡 **Tip:** Try again later when system load decreases"
)

MSG_RATE_LIMIT_DROPPED = (
    "⚠️ Service is busy and your request could not be completed. Please try again in a few minutes."
)

# H7/M12 decorator gate failures (user-facing, shared by all entry points)
MSG_ERROR_TOKEN_LINK_FAILED = (
    "Sorry, could not generate an access token link. Please try again later."
)
MSG_ERROR_UNEXPECTED = "Sorry, an unexpected error occurred. Please try again later."


# =====================================================================================
# ====== FILE TYPE DESCRIPTIONS ======
# =====================================================================================
MSG_FILE_TYPE_DOCUMENT = "📄 Document"
MSG_FILE_TYPE_PHOTO = "🖼️ Photo"
MSG_FILE_TYPE_VIDEO = "🎬 Video"
MSG_FILE_TYPE_AUDIO = "🎵 Audio"
MSG_FILE_TYPE_VOICE = "🎤 Voice Message"
MSG_FILE_TYPE_STICKER = "🎨 Sticker"
MSG_FILE_TYPE_ANIMATION = "🎞️ Animation (GIF)"
MSG_FILE_TYPE_VIDEO_NOTE = "📹 Video Note"
MSG_FILE_TYPE_UNKNOWN = "❓ Unknown File Type"

# =====================================================================================
# ====== SYSTEM & STATUS MESSAGES ======
# =====================================================================================

MSG_SYSTEM_STATUS = (
    "✅ **System Status:** Operational\n\n"
    "> 🕒 **Uptime:** `{uptime}`\n"
    "> 🤖 **Bot Instances:** `{active_bots}`\n"
    "> 📊 **Total Workload:** `{total_workload}`\n\n"
    "📜 **Workload Distribution:**\n\n"
    "{workload_items}\n"
    "> ♻️ **Version:** `{version}`"
)

MSG_SYSTEM_STATS = (
    "📊 **System Statistics**\n\n"
    "> System Uptime: {sys_uptime}\n"
    "> Bot Uptime: {bot_uptime}\n\n"
    "⚙️ **Performance:**\n"
    "> CPU: {cpu_percent}%\n"
    "> CPU Core: {cpu_cores}\n"
    "> Frequency: {cpu_freq} GHz\n\n"
    "💾 **RAM**\n"
    "> Total: {ram_total}\n"
    "> Used: {ram_used}\n"
    "> Free: {ram_free}\n\n"
    "💽 **Storage:**\n"
    "> Disk: `{disk_percent}%`\n"
    "> Total: `{total}`\n"
    "> Used: `{used}`\n"
    "> Free: `{free}`\n\n"
    "📶 **Network:**\n"
    "> 🔺 Upload: `{upload}`\n"
    "> 🔻 Download: `{download}`\n\n"
    "🚦 **Limiter:** {limiter}"
)

MSG_DB_STATS = "📊 **Database Statistics**\n\n> 👥 **Total Users:** `{total_users}`"
