<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/fyaz05/Resources@main/FileToLink/Thunder.jpg?text=Thunder" alt="Thunder Logo" width="120">
  <h1 align="center">⚡ Thunder</h1>
</p>

<p align="center">
  <b>High-performance Telegram File to Link Bot</b>
</p>

<p align="center">
  <a href="https://github.com/fyaz05/FileToLink">
    <img src="https://img.shields.io/badge/version-1.8.0-blue" alt="Version">
  </a>
  <a href="https://www.python.org/">
    <img src="https://img.shields.io/badge/python-3.13%2B-brightgreen" alt="Python">
  </a>
  <a href="https://github.com/KurimuzonAkuma/pyrogram/">
    <img src="https://img.shields.io/badge/kurigram-blue" alt="Kurigram">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/github/license/fyaz05/FileToLink.svg?color=brightgreen" alt="License">
  </a>
</p>

<hr>

<p align="center">
  <a href="#about-the-project">ℹ️ About</a> •
  <a href="#-features">✨ Features</a> •
  <a href="#-how-it-works">🔍 How It Works</a> •
  <a href="#-prerequisites">📋 Prerequisites</a> •
  <a href="#-configuration">⚙️ Configuration</a> •
  <a href="#-deployment">📦 Deployment</a> •
  <a href="#-usage">📱 Usage</a> •
  <a href="#-commands">⌨️ Commands</a>
</p>

<hr>

## ℹ️ About The Project

> **Thunder** is a powerful, high-performance Telegram bot that transforms media files into streamable direct links. Share and access files via HTTP(S) links instead of downloading from Telegram, for a seamless media experience.

**Perfect for:**

- 📤 Download Telegram media at high speed.
- 🎬 Content creators sharing media files.
- 👥 Communities distributing resources.
- 🎓 Educational platforms sharing materials.
- 🌍 Anyone needing to share Telegram media.

---

## ✨ Features

### 🚀 Core Functionality

- **Generate Direct Links:** Convert Telegram media files into direct streaming links.
- **Permanent Links:** Links remain active as long as the file exists in the storage channel.
- **Multi-Client Support:** Distribute workload across multiple Telegram accounts.
- **HTTP/HTTPS Streaming:** Stream media with custom player support for all devices and browsers.

### 🧩 Advanced Features

- **MongoDB Integration:** Store user data and file info with advanced database capabilities.
- **User Authentication:** Require users to join channels before generating links.
- **Admin Commands:** Manage users, view stats, and control bot behavior.
- **Custom Domain Support:** Use your own domain for streaming links.
- **Customizable Templates:** Personalize HTML templates for download pages.

### ⚙️ Technical Capabilities

- **Asynchronous Architecture:** Built with aiohttp and asyncio for high concurrency.
- **Rate Limiting:** Prevent abuse with advanced rate-limiting.
- **Media Info Display:** Show file size, duration, format, and more.
- **Multiple File Types:** Supports videos, audio, documents, images, stickers, and more.
- **Forwarding Control:** Restrict or allow file forwarding.
- **Caching System:** Reduce Telegram API calls and improve responsiveness.

---

## 🔍 How It Works

1. **Upload:** User sends a media file to the bot. The bot forwards it to a storage channel and stores metadata.
2. **Link Generation:** A unique, secure link is generated and sent to the user.
3. **Streaming:** When the link is opened, the server authenticates, retrieves the file from Telegram, and streams it directly to the browser.
4. **Load Balancing:** Multiple Telegram clients distribute workload, with automatic switching and smart queuing for high traffic.

---

## 📋 Prerequisites

- 🐍 Python 3.13 or higher
- 🍃 MongoDB
- 🔑 Telegram API ID and Hash ([my.telegram.org/apps](https://my.telegram.org/apps))
- 🤖 Bot Token from [@BotFather](https://t.me/BotFather)
- 🌐 Server with public IP or domain
- 📦 Telegram storage channel

---

## ⚙️ Configuration

Rename `config_sample.env` to `config.env` and edit the following variables:

### Required

| Variable         | Description                                 | Example                                 |
|------------------|---------------------------------------------|-----------------------------------------|
| `API_ID`         | Telegram API ID from my.telegram.org         | `12345`                                 |
| `API_HASH`       | Telegram API Hash from my.telegram.org       | `abc123def456`                          |
| `BOT_TOKEN`      | Bot token from @BotFather                   | `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`  |
| `BIN_CHANNEL`    | Channel ID for storing files (add bot as admin) | `-1001234567890`                    |
| `OWNER_ID`       | Your Telegram user ID(s) (space-separated)   | `12345678 87654321`                     |
| `OWNER_USERNAME` | Your Telegram username (without @)           | `yourusername`                          |
| `FQDN`           | Your domain name or server IP                | `files.yourdomain.com`                  |
| `HAS_SSL`        | Set to "True" if using HTTPS                | `True` or `False`                       |
| `PORT`           | Web server port                              | `8080`                                  |
| `NO_PORT`        | Hide port in URLs                            | `True` or `False`                       |

### Optional

| Variable             | Description                              | Default   | Example                       |
|----------------------|------------------------------------------|-----------|-------------------------------|
| `MULTI_BOT_TOKENS`   | Additional bot tokens for load balancing | *(empty)* | `MULTI_TOKEN1=`              |
| `FORCE_CHANNEL_ID`   | Channel ID users must join               | *(empty)* | `-1001234567890`              |
| `BANNED_CHANNELS`    | Space-separated banned channel IDs       | *(empty)* | `-1001234567890 -100987654321`|
| `SLEEP_THRESHOLD`    | Threshold for client switching           | `60`      | `30`                          |
| `WORKERS`            | Number of async workers                  | `100`     | `200`                         |
| `DATABASE_URL`       | MongoDB connection string                | *(empty)* | `mongodb+srv://user:pass@host/db` |
| `NAME`               | Bot application name                     | `ThunderF2L` | `MyFileBot`                |
| `BIND_ADDRESS`       | Address to bind web server               | `0.0.0.0` | `127.0.0.1`                   |
| `PING_INTERVAL`      | Ping interval in seconds                 | `840`     | `1200`                        |
| `CACHE_SIZE`         | Cache size in MB                         | `100`     | `200`                         |

> ℹ️ For all options, see `config_sample.env`.

---

## 📦 Deployment

### Using Docker (Recommended)

```bash
# Ensure you have configured config.env as per the Configuration section
# Build and run with Docker
docker build -t Thunder .
docker run -d --name Thunder -p 8080:8080 Thunder
```

### Manual Installation

```bash
# Ensure you have completed the "Getting Started" and "Configuration" sections
# Run the bot
python -m Thunder
```

### ⚡ Scaling for High Traffic

- Use multiple bot instances.
- Increase `WORKERS` in `config.env` based on your server's capabilities.

---

## 📱 Usage

### Basic

1. **Start:** Send `/start` to your bot.
2. **Authenticate:** Join required channels if configured by the admin.
3. **Upload:** Send any media file to the bot.
4. **Get Link:** Receive a direct streaming link.
5. **Share:** Anyone with the link can stream or download the file.

### Advanced

- **Batch Processing:** Forward multiple files to the bot for batch link generation.
- **Custom Thumbnails:** Send a photo with `/set_thumbnail` as its caption to set a custom thumbnail for subsequent files.
- **Remove Thumbnail:** Use `/del_thumbnail` to remove a previously set custom thumbnail.
- **User Settings:** Users might have access to a settings menu (if implemented) to configure preferences.

---

## ⌨️ Commands

### User Commands

| Command   | Description |
|-----------|-------------|
| `/start`  | Start the bot and get a welcome message. |
| `/link`   | Generate a direct link for a file in a group. Supports batch files by replying to the first file in a group (e.g., `/link 5`). |
| `/dc`     | Get the data center (DC) of a user or file. Use `/dc id`, or reply to a file or user. Works in both groups and private chats. |
| `/ping`   | Check if the bot is online and measure response time. |
| `/about`  | Get information about the bot. |
| `/help`   | Show help and usage instructions. |

### Admin Commands (for Bot Owner)

| Command        | Description                                                          |
|----------------|----------------------------------------------------------------------|
| `/status`      | Check bot status, uptime, and resource usage.                        |
| `/broadcast`   | Send a message to all users (supports text, media, buttons).         |
| `/stats`       | View usage statistics and analytics.                                 |
| `/ban`         | Ban a user (reply to message or use user ID).                        |
| `/unban`       | Unban a user.                                                        |
| `/log`         | Send bot logs.                                                       |
| `/restart`     | Restart the bot.                                                     |
| `/shell`       | Execute a shell command (Use with extreme caution!).                 |
| `/users`       | Show total number of users.                                          |

### Commands for @BotFather

Paste the following into the BotFather "Edit Commands" section for your bot:

```text
start - Start the bot and get a welcome message
link - Generate a direct link for a file (supports batch in groups)
dc - Get the data center (DC) of a user or file
ping - Check if the bot is online
about - Get information about the bot
help - Show help and usage instructions
status - (Admin) Check bot status, uptime, and resource usage
broadcast - (Admin) Send a message to all users
stats - (Admin) View usage statistics and analytics
ban - (Admin) Ban a user
unban - (Admin) Unban a user
log - (Admin) Send bot logs
restart - (Admin) Restart the bot
shell - (Admin) Execute a shell command
users - (Admin) Show total number of users
```

---

## 🤝 Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository.
2. Create your feature branch (`git checkout -b feature/AmazingFeature`).
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`).
4. Push to the branch (`git push origin feature/AmazingFeature`).
5. Open a Pull Request.

---

## 📄 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

---

## 👏 Acknowledgements

- [Kurigram](https://github.com/KurimuzonAkuma/pyrogram/) – Telegram MTProto API Client Library
- [AIOHTTP](https://github.com/aio-libs/aiohttp) – Async HTTP client/server framework
- [Motor](https://github.com/mongodb/motor) – Async MongoDB driver
- [TgCrypto](https://github.com/pyrogram/tgcrypto) – Fast cryptography library for Telegram
- All contributors who have helped improve the project.

---

## 📢 Disclaimer

> This project is not affiliated with Telegram. Use at your own risk and responsibility.
> Comply with Telegram's Terms of Service and your local regulations regarding content distribution.

---

<p align="center">
  ⭐ <b>Like this project? Give it a star!</b> ⭐<br>
  🐛 <b>Found a bug or have a feature request?</b> <a href="https://github.com/fyaz05/FileToLink/issues/new">Open an issue</a>
</p>
