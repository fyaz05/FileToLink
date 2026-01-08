<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/fyaz05/Resources@main/FileToLink/Thunder.jpg" alt="Thunder Logo" width="120">
  <h1 align="center">⚡ Thunder</h1>
</p>

<p align="center">
  <b>High-Performance Telegram File-to-Link Bot for Direct Links & Streaming</b>
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.13%2B-blue?style=for-the-badge&logo=python" alt="Python Version"></a>
  <a href="https://github.com/Mayuri-Chan/pyrofork"><img src="https://img.shields.io/badge/Pyrofork-red?style=for-the-badge" alt="Pyrofork"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/fyaz05/FileToLink?style=for-the-badge&color=green" alt="License"></a>
  <a href="https://t.me/Thunder_Updates"><img src="https://img.shields.io/badge/Telegram-Channel-blue?style=for-the-badge&logo=telegram" alt="Telegram Channel"></a>
</p>

<hr>

## 📑 Table of Contents

- [About The Project](#about-the-project)
- [How It Works](#how-it-works)
- [Features](#features)
- [Configuration](#configuration)
  - [Essential Configuration](#essential-configuration)
  - [Optional Configuration](#optional-configuration)
- [Usage and Commands](#usage-and-commands)
  - [Basic Usage](#basic-usage)
  - [Commands Reference](#commands-reference)
- [Advanced Feature Setup](#advanced-feature-setup)
  - [Token System](#token-system)
  - [URL Shortening](#url-shortening)
  - [Rate Limiting System](#rate-limiting-system)
  - [Network Speed Testing](#network-speed-testing)
- [Deployment Guide](#deployment-guide)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Quick Deploy](#quick-deploy)
    - [Deploy to Koyeb](#deploy-to-koyeb)
    - [Deploy to Render](#deploy-to-render)
    - [Deploy to Railway](#deploy-to-railway)
  - [Reverse Proxy Setup](#reverse-proxy-setup)
- [Support & Community](#support--community)
  - [Troubleshooting & FAQ](#troubleshooting--faq)
  - [Contributing](#contributing)
- [License](#license)
- [Acknowledgments](#acknowledgments)

<hr>

## About The Project

**Thunder** is a powerful Telegram bot that transforms Telegram files into high-speed direct links, perfect for both streaming and rapid downloading. Share files via HTTP(S) links without needing to download them from the Telegram client first.

### 💡 Perfect For

- 🚀 Bypassing Telegram's built-in download speed limits
- ☁️ Unlimited cloud storage with fast streaming and download links
- 🎬 Content creators sharing media files
- 👥 Communities distributing resources
- 🎓 Educational platforms sharing materials

## How It Works

```
User Uploads File → Telegram Bot → Forwards to Channel → Generates Direct Link → Direct Download / Streaming
```

1. **Upload** → User sends any file to the bot.
2. **Store** → The bot forwards the file to your private storage channel (`BIN_CHANNEL`), where it is permanently saved to generate the link.
3. **Generate** → A unique, permanent link is created.
4. **Stream/Download** → Anyone with the link can stream or download the file directly in their browser.
5. **Balance** → Multi-client support distributes the load for high availability.

## Features

#### Core Functionality

- ✅ **Direct Link Generation** - Convert any Telegram file into a direct HTTP(S) link.
- ✅ **Permanent Links** - Links remain active as long as the file exists in the storage channel.
- ✅ **Browser Streaming & Downloading** - Stream media directly or download files at high speed without a Telegram client.
- ✅ **All File Types** - Supports video, audio, documents, images, and any other file format.
- ✅ **Batch Processing** - Generate links for multiple files at once with a single command.

#### Performance & Scalability

- ✅ **Multi-Client Support** - Distributes traffic across multiple Telegram bots to avoid limits and increase throughput.
- ✅ **Async Architecture** - Built with `aiohttp` and `asyncio` for non-blocking, high-performance operations.
- ✅ **MongoDB Integration** - Ensures persistent and reliable data storage.

#### Security & Control

- 🔐 **Token Authentication** - Secure user access with a time-limited token system.
- 🛡️ **Admin Controls** - Full suite of commands for user and bot management.
- 👤 **User Authentication** - Require users to join a specific channel before they can use the bot.
- ✅ **Channel/Group Support** - Fully functional in private chats, groups, and channels.

#### Customization

- 🌍 **Custom Domain** - Serve files from your own domain for a professional look.
- 🔗 **URL Shortening** - Integrate with URL shortener services for clean, shareable links.
- 🎨 **Custom Templates** - Personalize messages sent by the bot to match your brand.
- 📈 **Media Info Display** - Shows file size, duration, and format details in the response message.

## Configuration

Copy `config_sample.env` to `config.env` and fill in your values.

### Essential Configuration

| Variable | Description | Example |
| :--- | :--- | :--- |
| `API_ID` | Telegram API ID | `12345678` |
| `API_HASH` | Telegram API Hash | `abc123def456` |
| `BOT_TOKEN` | Bot token from @BotFather | `123456:ABCdefGHI` |
| `BIN_CHANNEL` | Storage channel ID | `-1001234567890` |
| `OWNER_ID` | Owner user ID | `12345678` |
| `DATABASE_URL` | MongoDB connection | `mongodb+srv://...` |
| `FQDN` | Domain/IP address | `f2l.thunder.com` |
| `HAS_SSL` | HTTPS enabled | `True` or `False` |
| `PORT` | Server port | `8080` |
| `NO_PORT` | Hide port in URLs | `True` or `False` |

### Optional Configuration

<details>
<summary>Optional Configuration Details</summary>

| Variable | Description | Default |
| :--- | :--- | :--- |
| `MULTI_TOKEN1` | Additional bot token 1 (use MULTI_TOKEN1, MULTI_TOKEN2, etc.) | *(empty)* |
| `FORCE_CHANNEL_ID` | Required channel join | *(empty)* |
| `MAX_BATCH_FILES` | Maximum files in batch processing | `50` |
| `CHANNEL` | Allow processing messages from channels | `False` |
| `BANNED_CHANNELS` | Blocked channel IDs | *(empty)* |
| `SLEEP_THRESHOLD` | Client switch threshold | `300` |
| `WORKERS` | Async workers | `8` |
| `NAME` | Bot name | `ThunderF2L` |
| `BIND_ADDRESS` | Bind address | `0.0.0.0` |
| `PING_INTERVAL` | Ping interval (seconds) | `840` |
| `TOKEN_ENABLED` | Enable tokens | `False` |
| `SHORTEN_ENABLED` | URL shortening for tokens | `False` |
| `SHORTEN_MEDIA_LINKS` | URL shortening for media | `False` |
| `TOKEN_TTL_HOURS` | Token validity duration in hours | `24` |
| `URL_SHORTENER_API_KEY` | Shortener API key | *(empty)* |
| `URL_SHORTENER_SITE` | Shortener service | *(empty)* |
| `SET_COMMANDS` | Auto-set bot commands | `True` |
| `RATE_LIMIT_ENABLED` | Enable rate limiting | `False` |
| `MAX_FILES_PER_PERIOD` | Files per window | `2` |
| `RATE_LIMIT_PERIOD_MINUTES` | Time window | `1` |
| `MAX_QUEUE_SIZE` | Queue size | `100` |
| `GLOBAL_RATE_LIMIT` | Global limiting | `True` |
| `MAX_GLOBAL_REQUESTS_PER_MINUTE` | Global limit | `4` |

</details>

## Usage and Commands

### Basic Usage

1. **Start** → Send `/start` to the bot.
2. **Authenticate** → Join required channels (if configured).
3. **Upload** → Send any media file.
4. **Receive** → Get a direct streaming and download link.
5. **Share** → Anyone can access the file via the link.

### Commands Reference

#### User Commands

| Command | Description |
| :--- | :--- |
| `/start` | Start the bot and get a welcome message. Also used for token activation. |
| `/link` | Generates a link. For batches, **reply to the first file** of a group and specify the count. **Example:** `/link 5` will process that file and the next four. |
| `/dc` | Get the data center (DC) of a user or file. Use `/dc id`, or reply to a file or user. |
| `/ping` | Check if the bot is online and measure response time. |
| `/about` | Get information about the bot. |
| `/help` | Show help and usage instructions. |

#### Admin Commands

| Command | Description |
| :--- | :--- |
| `/status` | Check bot status, uptime, and resource usage. |
| `/broadcast` | Send a message to all users (supports text, media, buttons). |
| `/stats` | View usage statistics and analytics. |
| `/ban` | Ban a user or channel (reply to message or use user/channel ID). |
| `/unban` | Unban a user or channel. |
| `/log` | Send bot logs. |
| `/restart` | Restart the bot. |
| `/shell` | Execute a shell command. |
| `/speedtest` | Run network speed test and display comprehensive results. |
| `/users` | Show total number of users. |
| `/authorize` | Permanently authorize a user to use the bot (bypasses token system). |
| `/deauthorize` | Remove permanent authorization from a user. |
| `/listauth` | List all permanently authorized users. |

<details>
<summary><h4>BotFather Commands Setup</h4></summary>

```text
start - Initialize bot
link - Generate direct link
dc - Get data center info
ping - Check bot status
about - Bot information
help - Show help guide
status - [Admin] System status
stats - [Admin] Usage statistics
broadcast - [Admin] Message all users
ban - [Admin] Ban user
unban - [Admin] Unban user
users - [Admin] User count
authorize - [Admin] Grant access
deauthorize - [Admin] Revoke access
listauth - [Admin] List authorized
log - [Admin] Send bot logs
restart - [Admin] Restart the bot
shell - [Admin] Execute shell command
speedtest - [Admin] Run network speed test
```

</details>

## Advanced Feature Setup

### Token System

Enable controlled access with tokens:

1. Set `TOKEN_ENABLED=True` in your `config.env`.
2. Users receive automatic tokens on first use.
3. Admins can grant permanent authorization with `/authorize` to bypass tokens.
4. Tokens include activation links for secure access.

### URL Shortening

Configure URL shortening for cleaner links:

```env
SHORTEN_ENABLED=True
SHORTEN_MEDIA_LINKS=True
URL_SHORTENER_API_KEY=your_api_key
URL_SHORTENER_SITE=shortener.example.com
```

### Rate Limiting System

Thunder implements a sophisticated multi-tier rate limiting system designed for high-performance file sharing:

#### **Priority Queue Architecture**

- **Owner Priority**: Complete bypass of all rate limits.
- **Authorized Users**: Dedicated priority queue with faster processing.
- **Regular Users**: Standard queue with fair scheduling.

#### **Multi-Level Rate Limiting**

- **Per-User Limits**: Configurable files per time window.
- **Global Limits**: System-wide request throttling.
- **Sliding Window**: Time-based rate limiting with automatic cleanup.

#### **Smart Queue Management**

- **Automatic Re-queuing**: Failed requests due to rate limits are intelligently re-queued.
- **Queue Size Limits**: Configurable maximum queue size.
- **Flood Protection**: Built-in protection against Telegram flood waits.

### Network Speed Testing

Monitor server performance with built-in speed testing:

```bash
/speedtest
```

Features include download/upload speeds, latency measurements, and shareable result images for performance monitoring.

## Deployment Guide

This section covers the complete setup process for deploying Thunder, from prerequisites to production deployment.

### Prerequisites

| Requirement | Description | Source |
| :--- | :--- | :--- |
| Python 3.13 | Programming language | [python.org](https://python.org) |
| MongoDB | Database | [mongodb.com](https://mongodb.com) |
| Telegram API | API credentials | [my.telegram.org](https://my.telegram.org/apps) |
| Bot Token | From @BotFather | [@BotFather](https://t.me/BotFather) |
| Public Server | VPS/Dedicated server | Any provider |
| Storage Channel | For file storage | Create in Telegram |

### Installation

#### Docker Installation (Recommended)

```bash
# 1. Clone repository
git clone https://github.com/fyaz05/FileToLink.git
cd FileToLink

# 2. Configure
cp config_sample.env config.env
nano config.env  # Edit your settings

# 3. Build and run
docker build -t thunder .
docker run -d --name thunder -p 8080:8080 thunder
```

<details>
<summary>Manual Installation</summary>

```bash
# 1. Clone repository
git clone https://github.com/fyaz05/FileToLink.git
cd FileToLink

# 2. Setup virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure
cp config_sample.env config.env
nano config.env

# 5. Run bot
python -m Thunder
```

> **Tip:** Start with the essential configuration to get Thunder running, then add optional features as needed.

</details>

## Quick Deploy

### Deploy to Koyeb

[![Deploy to Koyeb](https://www.koyeb.com/static/images/deploy/button.svg)](https://app.koyeb.com/deploy?type=docker&image=docker.io/fyaz05/thunder:latest&name=thunder&ports=8080;http;/&env[API_ID]=&env[API_HASH]=&env[BOT_TOKEN]=&env[BIN_CHANNEL]=&env[OWNER_ID]=&env[DATABASE_URL]=&env[FQDN]=)

After deployment, to add any additional environment variables, use the Koyeb dashboard under **Settings** → **Environment Variables**.

### Deploy to Render

1. Open [Render Dashboard](https://dashboard.render.com) → **New** → **Web Service**
2. Choose **Existing Image**: `fyaz05/thunder:latest`
3. Add your environment variables
4. Click **Deploy**

### Deploy to Railway

1. Open [Railway](https://railway.app) → **New Project** → **Deploy Service**
2. Choose **Docker Image**: `fyaz05/thunder:latest`
3. Add your environment variables
4. Click **Deploy**

> **Note:** See the [Configuration](#configuration) section for required environment variables.

## Reverse Proxy Setup

<details>
<summary>Reverse Proxy Guide</summary>

This guide will help you set up a secure reverse proxy using **NGINX** for your file streaming bot with **Cloudflare SSL protection**.

---

#### ✅ What You Need

- A **VPS or server** running Ubuntu/Debian with NGINX installed.
- Your **file streaming bot** running on a local port (e.g., `8080`).
- A **subdomain** (e.g., `f2l.thunder.com`) set up in **Cloudflare**.
- **Cloudflare Origin Certificate** files: `cert.pem` and `key.key`.

---

#### 🔐 Step 1: Configure Cloudflare

- **DNS**: Add an `A` record for your subdomain pointing to your server's IP. Ensure **Proxy Status** is **Proxied (orange cloud)**.
- **SSL**: In the **SSL/TLS** tab, set the encryption mode to **Full (strict)**.

---

#### 🛡️ Step 2: Set Up SSL Certificates on Server

Create a folder for your certificates and place `cert.pem` and `key.key` inside. Secure the private key.

```bash
sudo mkdir -p /etc/ssl/cloudflare/f2l.thunder.com
# Move/copy your cert.pem and key.key files into this directory
sudo chmod 600 /etc/ssl/cloudflare/f2l.thunder.com/key.key
sudo chmod 644 /etc/ssl/cloudflare/f2l.thunder.com/cert.pem
```

---

#### 🛠️ Step 3: Create NGINX Configuration

Create a new file at `/etc/nginx/sites-available/f2l.thunder.conf` and paste the following, replacing `f2l.thunder.com` and `8080` with your values.

```nginx
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name f2l.thunder.com;

    # SSL Configuration
    ssl_certificate     /etc/ssl/cloudflare/f2l.thunder.com/cert.pem;
    ssl_certificate_key /etc/ssl/cloudflare/f2l.thunder.com/key.key;

    # Basic security
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;

    location / {
        # Forward requests to your bot
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Settings for file streaming
        proxy_buffering off;
        proxy_request_buffering off;
        client_max_body_size 0;
    }
}

# Redirect HTTP to HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name f2l.thunder.com;
    return 301 https://$host$request_uri;
}
```

---

#### 🔄 Step 4: Test and Apply Changes

Enable the configuration, test it, and reload NGINX.

```bash
sudo ln -s /etc/nginx/sites-available/f2l.thunder.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

Your reverse proxy is now securely streaming files behind Cloudflare!

</details>

## Support & Community

### Troubleshooting & FAQ

#### **Initial Setup**

**Q: Why isn't my bot responding after setup?**
A: This is usually a configuration issue. Please check the following:

1. **Verify `config.env`**: Make sure all essential variables (`API_ID`, `API_HASH`, `BOT_TOKEN`, `BIN_CHANNEL`, `DATABASE_URL`) are filled in correctly.
2. **Use `config.env` Only**: Do not edit `vars.py` or `config_sample.env`. The bot is designed to only read your settings from `config.env`.
3. **Check Logs**: Review the console logs on your server or hosting platform (Koyeb, Render, Heroku) for any startup errors.

**Q: What do I use for the `FQDN` variable?**
A: It's the public URL or IP address of your bot.

- **With a Domain**: Use your subdomain (e.g., `f2l.thunder.com`).
- **On Koyeb/Render/Heroku**: Use the public URL provided by the platform.
- **On a VPS**: Use your server's public IP address.

**Q: Why are my links not working on a VPS?**
A: For links to work on a VPS, the URL must include the port number (e.g., `http://YOUR_VPS_IP:8080`). Ensure that `NO_PORT` is set to `False` in `config.env` and that your server is configured to allow traffic through that port.

#### **Common Errors**

**Q: Why are my links showing a "Resource Not Found" error or not working?**
A: This error means the bot can't access the file. Check these three things:

1. **Invalid Token**: Your `BOT_TOKEN` or one of the `MULTI_TOKEN`s might be wrong. Double-check them with @BotFather.
2. **Missing Admin Rights**: The bot and **all** your client accounts must be **administrators** in the `BIN_CHANNEL`.
3. **File Deleted**: The link will break if the file was deleted from your `BIN_CHANNEL`.

**Q: Why isn't video or audio playing correctly in my browser?**
A: Your browser likely doesn't support the file's audio or video format (codec). This is a browser limitation, not a bot issue.

- **Solution**: For perfect playback, copy the link and play it in a dedicated media player. Recommended players include **VLC Media Player**, **MX Player**, **PotPlayer**, **IINA**, and **MPV**.

**Q: Why does the bot sometimes become unresponsive?**
A: This is likely a **Telegram Flood Wait**. To prevent spam, Telegram temporarily limits accounts that make too many requests. The bot is designed to handle this automatically by pausing and will resume on its own once the limit is lifted.

#### **Performance**

**Q: How can I fix slow download and streaming speeds?**
A: If your speeds are slow, here’s how to fix it:

- **Add More Clients**: This is the best solution. Add `MULTI_TOKEN`s to your `config.env` to distribute the workload and increase throughput.
- **Use DC4 Accounts**: For top performance, use Telegram accounts from **Data Center 4 (DC4)**, as they often have the fastest connection. Use `/dc` to check an account's data center.
- **Upgrade Your Server**: A server with a slow network will bottleneck your speeds. Consider upgrading your VPS plan.

#### **Bot Usage**

**Q: How do I generate links for multiple files at once?**
A: The `/link` command can process multiple files sent in sequence. To use it, **reply to the first file** of the series with the command and the total count.

- **Example**: For a series of 5 files, reply to the very first file with `/link 5`.

**Q: Can I mix tokens from different accounts and data centers?**
A: Yes. Mixing clients from different accounts and data centers (like DC1, DC4, and DC5) is a great way to improve bot performance and reliability.

### Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository.
2. Create a new feature branch (`git checkout -b feature/amazing-feature`).
3. Commit your changes (`git commit -m 'Add some amazing feature'`).
4. Push to the branch (`git push origin feature/amazing-feature`).
5. Open a Pull Request.

## License

Licensed under the [Apache License 2.0](LICENSE). See the `LICENSE` file for details.

## Acknowledgments

- [Pyrofork](https://github.com/Mayuri-Chan/pyrofork) - Telegram MTProto API Framework
- [aiohttp](https://github.com/aio-libs/aiohttp) - Asynchronous HTTP Client/Server
- [PyMongo](https://github.com/mongodb/mongo-python-driver) - Asynchronous MongoDB Driver
- [TgCrypto](https://github.com/pyrogram/tgcrypto) - High-performance cryptography library

## ⚠️ Disclaimer

This project is not affiliated with Telegram. Use it responsibly and in compliance with Telegram's Terms of Service and all applicable local regulations.

---

<p align="center">
  <b>⭐ Star this project if you find it useful!</b><br>
  <a href="https://github.com/fyaz05/FileToLink/issues/new">Report Bug</a> •
  <a href="https://github.com/fyaz05/FileToLink/issues/new">Request Feature</a>
</p>
