<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/fyaz05/Resources@main/FileToLink/Thunder.jpg" alt="Thunder Logo" width="120">
  <h1 align="center">⚡ Thunder</h1>
</p>

<p align="center">
  <b>High-Performance Telegram File-to-Link Bot</b>
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.13%2B-blue?style=for-the-badge&logo=python" alt="Python Version"></a>
  <a href="https://github.com/KurimuzonAkuma/pyrogram/"><img src="https://img.shields.io/badge/Kurigram-red?style=for-the-badge" alt="Kurigram"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/fyaz05/FileToLink?style=for-the-badge&color=green" alt="License"></a>
  <a href="https://t.me/Thunder_Updates"><img src="https://img.shields.io/badge/Telegram-Channel-blue?style=for-the-badge&logo=telegram" alt="Telegram Channel"></a>
</p>

<hr>

## 📑 Table of Contents

- [About](#about-the-project)
- [Features](#features)
- [How It Works](#how-it-works)
- [Prerequisites](#prerequisites)
- [Configuration](#configuration)
- [Quick Start](#quick-start)
- [Usage](#usage)
- [Commands](#commands)
- [Token System](#token-system)
- [URL Shortening](#url-shortening)
- [Rate Limiting](#rate-limiting-system)
- [Reverse Proxy Setup](#reverse-proxy-setup)
- [Contributing](#contributing)
- [License](#license)

<hr>

## About The Project

**Thunder** is a powerful Telegram bot that transforms media files into streamable direct links. Share files via HTTP(S) links without downloading from Telegram.

### 💡 Perfect For

- 🚀 High-speed downloads from Telegram media
- ☁️ Unlimited cloud storage with fast streaming links
- 🎬 Content creators sharing media files
- 👥 Communities distributing resources
- 🎓 Educational platforms sharing materials

## Features

### Core Functionality
- ✅ **Direct Link Generation** - Convert Telegram media to streaming links
- ✅ **Permanent Links** - Active as long as file exists
- ✅ **Multi-Client Support** - Load balancing across clients
- ✅ **Browser Streaming** - No download required
- ✅ **MongoDB Integration** - Persistent data storage
- ✅ **Channel/Group Support** - Works everywhere

### Advanced Features
- 🔐 **Token Authentication** - Secure access control
- 🔗 **URL Shortening** - Clean, short links
- 📦 **Batch Processing** - Multiple files at once
- 👤 **User Authentication** - Channel join requirements
- 🛡️ **Admin Controls** - Full user management
- 🌍 **Custom Domain** - Your own streaming domain
- 📊 **Rate Limiting** - Smart request queuing

### Technical Capabilities
- ⚡ **Async Architecture** - Built with aiohttp + asyncio
- 📈 **Media Info Display** - Size, duration, format details
- 📁 **All File Types** - Video, audio, documents, images
- 💾 **Caching System** - Improved performance
- 🎨 **Custom Templates** - Personalized messages

## How It Works

1. **Upload** → User sends media file to bot
2. **Store** → Bot forwards to storage channel
3. **Generate** → Unique streaming link created
4. **Stream** → Direct browser streaming via link
5. **Balance** → Multi-client load distribution

## Prerequisites

| Requirement | Description | Source |
|------------|-------------|--------|
| Python 3.13+ | Programming language | [python.org](https://python.org) |
| MongoDB | Database | [mongodb.com](https://mongodb.com) |
| Telegram API | API credentials | [my.telegram.org](https://my.telegram.org/apps) |
| Bot Token | From @BotFather | [@BotFather](https://t.me/BotFather) |
| Public Server | VPS/Dedicated server | Any provider |
| Storage Channel | For file storage | Create in Telegram |

## Configuration

Copy `config_sample.env` to `config.env` and configure:

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `API_ID` | Telegram API ID | `12345678` |
| `API_HASH` | Telegram API Hash | `abc123def456` |
| `BOT_TOKEN` | Bot token from @BotFather | `123456:ABCdefGHI` |
| `BIN_CHANNEL` | Storage channel ID | `-1001234567890` |
| `OWNER_ID` | Owner user ID | `12345678` |
| `DATABASE_URL` | MongoDB connection | `mongodb+srv://...` |
| `OWNER_USERNAME` | Owner username | `yourusername` |
| `FQDN` | Domain/IP address | `files.example.com` |
| `HAS_SSL` | HTTPS enabled | `True` or `False` |
| `PORT` | Server port | `8080` |

### Optional Variables

<details>
<summary>Click to expand optional configuration</summary>

| Variable | Description | Default |
|----------|-------------|---------|
| `MULTI_BOT_TOKENS` | Additional bot tokens | *(empty)* |
| `FORCE_CHANNEL_ID` | Required channel join | *(empty)* |
| `CHANNEL` | Allow processing of channel messages | `False` |
| `BANNED_CHANNELS` | Blocked channel IDs | *(empty)* |
| `SLEEP_THRESHOLD` | Client switch threshold | `300` |
| `WORKERS` | Async workers | `8` |
| `NAME` | Bot name | `ThunderF2L` |
| `BIND_ADDRESS` | Bind address | `0.0.0.0` |
| `PING_INTERVAL` | Ping interval (seconds) | `840` |
| `TOKEN_ENABLED` | Enable tokens | `False` |
| `SHORTEN_ENABLED` | URL shortening for tokens | `False` |
| `SHORTEN_MEDIA_LINKS` | URL shortening for media | `False` |
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

## Quick Start

### Docker Installation (Recommended)

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

### Manual Installation

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

## Usage

### Basic Usage

1. **Start** → Send `/start` to bot
2. **Authenticate** → Join required channels (if configured)
3. **Upload** → Send any media file
4. **Receive** → Get direct streaming link
5. **Share** → Anyone can access via link

### Advanced Usage

- **Batch Processing**: `/link 5` in groups (process 5 files)
- **Data Center Info**: `/dc` for DC information
- **Performance Check**: `/ping` for response time
- **Admin Panel**: `/status` for bot statistics

## Commands

### User Commands

| Command   | Description |
|-----------|-------------|
| `/start`  | Start the bot and get a welcome message. Also used for token activation. |
| `/link`   | Generate a direct link for a file in a group. Supports batch files by replying to the first file in a group (e.g., `/link 5`). |
| `/dc`     | Get the data center (DC) of a user or file. Use `/dc id`, or reply to a file or user. Works in both groups and private chats. |
| `/ping`   | Check if the bot is online and measure response time. |
| `/about`  | Get information about the bot. |
| `/help`   | Show help and usage instructions. |

### Admin Commands

| Command        | Description                                                          |
|----------------|----------------------------------------------------------------------|
| `/status`      | Check bot status, uptime, and resource usage.                        |
| `/broadcast`   | Send a message to all users (supports text, media, buttons).         |
| `/stats`       | View usage statistics and analytics.                                 |
| `/ban`         | Ban a user or channel (reply to message or use user/channel ID).     |
| `/unban`       | Unban a user or channel.                                              |
| `/log`         | Send bot logs.                                                       |
| `/restart`     | Restart the bot.                                                     |
| `/shell`       | Execute a shell command (Use with extreme caution!).                 |
| `/users`       | Show total number of users.                                          |
| `/authorize`   | Permanently authorize a user to use the bot (bypasses token system). |
| `/deauthorize` | Remove permanent authorization from a user.                          |
| `/listauth`    | List all permanently authorized users.                              |

### BotFather Commands Setup

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
```

## Token System

Enable controlled access with tokens:

1. Set `TOKEN_ENABLED=True` in config
2. Users receive automatic tokens on first use
3. Admins can grant permanent authorization
4. Tokens include activation links

## URL Shortening

Configure URL shortening:

```env
SHORTEN_ENABLED=True
SHORTEN_MEDIA_LINKS=True
URL_SHORTENER_API_KEY=your_api_key
URL_SHORTENER_SITE=shortener.example.com
```

## Rate Limiting System

Prevent abuse with intelligent rate limiting:

- **Per-User Limits** - Configurable request quotas
- **Global Limits** - System-wide control
- **Smart Queuing** - Queue excess requests
- **Priority Access** - Owners bypass limits
- **User Feedback** - Queue notifications

## Reverse Proxy Setup

<details>
<summary><b>Reverse Proxy with Cloudflare SSL</b></summary>

## Reverse Proxy Setup for File Streaming Bot with Cloudflare SSL
 
This guide will help you set up a secure reverse proxy using **NGINX** for your file streaming bot with **Cloudflare SSL protection**.

---

## ✅ What You Need

- A **VPS or server** running Ubuntu/Debian with NGINX installed
- Your **file streaming bot** running on a local port (e.g., `5063`)
- A **subdomain** (e.g., `dl.yoursite.com`) set up in **Cloudflare**
- **Cloudflare Origin Certificate** files:
  - `cert.pem` (Certificate file)
  - `key.key` (Private key file)

---

## 🔐 Step 1: Configure Cloudflare

**Set up DNS:**
- Go to your domain in [Cloudflare Dashboard](https://dash.cloudflare.com)
- Navigate to **DNS** → Add an `A` record:
  - **Name:** `dl` (or your preferred subdomain)
  - **Content:** Your server's IP address
  - **Proxy Status:** **Proxied (orange cloud)**

**Configure SSL:**
- Go to **SSL/TLS** → **Overview**
- Set encryption mode to **Full (strict)**
- Create your **Origin Certificate** if you haven't already

---

## 🛡️ Step 2: Set Up SSL Certificates

Create a folder for your SSL certificates:

```bash
sudo mkdir -p /etc/ssl/cloudflare/dl.yoursite.com
```

**If you have the certificate files already:**
```bash
sudo mv cert.pem key.key /etc/ssl/cloudflare/dl.yoursite.com/
```

**If you need to create them:**
```bash
# Create certificate file
sudo nano /etc/ssl/cloudflare/dl.yoursite.com/cert.pem
# Paste your Origin Certificate here and save

# Create private key file  
sudo nano /etc/ssl/cloudflare/dl.yoursite.com/key.key
# Paste your Private Key here and save
```

**Make the files secure:**
```bash
sudo chmod 600 /etc/ssl/cloudflare/dl.yoursite.com/key.key
sudo chmod 644 /etc/ssl/cloudflare/dl.yoursite.com/cert.pem
```

---

## 🛠️ Step 3: Create NGINX Configuration

Create a new configuration file:
```bash
sudo nano /etc/nginx/sites-available/dl.yoursite.conf
```

**Paste this configuration** (replace `dl.yoursite.com` and `5063` with your values):

```nginx
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name dl.yoursite.com;

    # SSL Configuration
    ssl_certificate     /etc/ssl/cloudflare/dl.yoursite.com/cert.pem;
    ssl_certificate_key /etc/ssl/cloudflare/dl.yoursite.com/key.key;

    # Basic security
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;

    # Logging
    access_log /var/log/nginx/dl.yoursite.com.access.log;
    error_log /var/log/nginx/dl.yoursite.com.error.log;

    location / {
        # Forward requests to your bot
        proxy_pass http://localhost:5063;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Settings for file streaming
        proxy_buffering off;
        proxy_request_buffering off;
        
        # Allow large files
        client_max_body_size 0;
    }
}

# Redirect HTTP to HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name dl.yoursite.com;
    
    return 301 https://$host$request_uri;
}
```

**Enable the configuration:**
```bash
sudo ln -s /etc/nginx/sites-available/dl.yoursite.conf /etc/nginx/sites-enabled/
```

---

## 🔄 Step 4: Test and Apply Changes

**Check if configuration is correct:**
```bash
sudo nginx -t
```

**If no errors, restart NGINX:**
```bash
sudo systemctl reload nginx
```

---

## ✅ Step 5: Test Your Setup

**Test if your site is working:**
```bash
curl -I https://dl.yoursite.com
```

**Test a file download:**
```bash
curl -I https://dl.yoursite.com/dl/<your_file_id>
```

---

## 🎉 Done!

Your reverse proxy is now securely streaming files behind Cloudflare, powered by NGINX!

</details>

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing`)
3. Commit changes (`git commit -m 'Add feature'`)
4. Push branch (`git push origin feature/amazing`)
5. Open Pull Request

## License

Licensed under [Apache License 2.0](LICENSE). See LICENSE file for details.

## 🙏 Acknowledgments

- [Kurigram](https://github.com/KurimuzonAkuma/pyrogram/) - Telegram MTProto API Framework
- [aiohttp](https://github.com/aio-libs/aiohttp) - Async HTTP client/server
- [Motor](https://github.com/mongodb/motor) - Async MongoDB driver
- [TgCrypto](https://github.com/pyrogram/tgcrypto) - Fast encryption library

## ⚠️ Disclaimer

This project is not affiliated with Telegram. Use responsibly and comply with Telegram's Terms of Service and local regulations.

---

<p align="center">
  <b>⭐ Star this project if you find it useful!</b><br>
  <a href="https://github.com/fyaz05/FileToLink/issues/new">Report Bug</a> •
  <a href="https://github.com/fyaz05/FileToLink/issues/new">Request Feature</a>
</p>