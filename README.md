<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/fyaz05/Resources@main/FileToLink/Thunder.jpg" alt="Thunder Logo" width="120">
  <h1 align="center">⚡ Thunder</h1>
</p>

<p align="center">
  <b>High-performance Telegram File to Link Bot</b>
</p>

<p align="center">
  <a href="https://www.python.org/">
    <img src="https://img.shields.io/badge/python-3.13%2B-brightgreen" alt="Python">
  </a>
  <a href="https://github.com/KurimuzonAkuma/pyrogram/">
    <img src="https://img.shields.io/badge/kurigram-blue" alt="Kurigram">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/github/license/fyaz05/FileToLink.svg?color=brightgreen" alt="License">
  </a>
  <a href="https://t.me/Thunder_Updates">
    <img src="https://img.shields.io/badge/Telegram-Channel-blue?logo=telegram" alt="Telegram Channel">
  </a>
</p>

<hr>

<p align="center">
  <a href="#about-the-project">About</a> •
  <a href="#-features">✨ Features</a> •
  <a href="#-how-it-works">🔍 How It Works</a> •
  <a href="#-prerequisites">📋 Prerequisites</a> •
  <a href="#configuration">Configuration</a> •
  <a href="#-deployment">📦 Deployment</a> •
  <a href="#-usage">📱 Usage</a> •
  <a href="#commands">⌨️ Commands</a>
</p>

<hr>

## About The Project

> **Thunder** is a powerful, high-performance Telegram bot that transforms media files into streamable direct links. Share and access files via HTTP(S) links instead of downloading from Telegram, for a seamless media experience.

**Perfect for:**

- 🚀 Download Telegram media at high speed.
- ☁️ Leveraging free unlimited cloud storage with high-speed links.
- 🎬 Content creators sharing media files.
- 👥 Communities distributing resources.
- 🎓 Educational platforms sharing materials.
- 🌍 Anyone needing to share Telegram media.

---

## ✨ Features

### 🧠 Core Functionality

- **Generate Direct Links:** Convert Telegram media files into direct streaming links.
- **Permanent Links:** Links remain active as long as the file exists in the storage channel.
- **Multi-Client Support:** Distribute workload across multiple Telegram clients for high traffic.
- **Browser Streaming:** Stream media files directly in the browser without downloading.
- **Broadcast Messages:** Send messages to all users.
- **Channel and Group Support:** Works in private chats, groups, and channels.
- **MongoDB Integration:** Store user data and info with advanced database capabilities.
- **HTTP/HTTPS Streaming:** Stream media with custom player support for all devices and browsers.
- **Flood Wait Handling:** Centralized handling for Telegram flood waits.

### 🧩 Advanced Features

- **Token Authentication:** Secure access with optional token-based authentication.
- **URL Shortening:** URL shortening for links.
- **Batch Processing:** Generate links for multiple files in a group chat.
- **User Authentication:** Require users to join channels before generating links.
- **Admin Commands:** Manage users and control bot behavior.
- **Custom Domain Support:** Use your own domain for streaming links.
- **Customizable Templates:** Personalize HTML templates for download pages.
- **Data Center Info:** Get data center information for users and files.
- **Auto Set Commands:** Automatically set bot commands on startup.

### ⚙️ Technical Capabilities

- **Asynchronous Architecture:** Built with aiohttp and asyncio for high concurrency.
- **Media Info Display:** Show file size, duration, format, and more.
- **Multiple File Types:** Supports videos, audio, documents, images, stickers, and more.
- **Caching System:** Reduce Telegram API calls and improve responsiveness.
- **Customizable Messages:** Personalize messages sent to users.

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

## Configuration

Rename `config_sample.env` to `config.env` and edit the following variables:

### Required

| Variable         | Description                                 | Example                                 |
|------------------|---------------------------------------------|-----------------------------------------|
| `API_ID`         | Telegram API ID from my.telegram.org         | `12345`                                 |
| `API_HASH`       | Telegram API Hash from my.telegram.org       | `abc123def456`                          |
| `BOT_TOKEN`      | Bot token from @BotFather                   | `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`  |
| `BIN_CHANNEL`    | Channel ID for storing files (add bot as admin) | `-1001234567890`                    |
| `OWNER_ID`       | Your Telegram user ID(s) (space-separated)   | `12345678 87654321`                     |
| `DATABASE_URL`       | MongoDB connection string                | `mongodb+srv://user:pass@host/db` |
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
| `SLEEP_THRESHOLD`    | Threshold for client switching           | `300`      | `600`                          |
| `WORKERS`            | Number of async workers                  | `8`     | `200`                         |
| `NAME`               | Bot application name                     | `ThunderF2L` | `MyFileBot`                |
| `BIND_ADDRESS`       | Address to bind web server               | `0.0.0.0` | `127.0.0.1`                   |
| `PING_INTERVAL`      | Ping interval in seconds                 | `840`     | `1200`                        |
| `CACHE_SIZE`         | Cache size in MB                         | `100`     | `200`                         |
| `TOKEN_ENABLED`      | Enable token authentication system      | `False`   | `True`                         |
| `SHORTEN_ENABLED`    | Enable URL shortening for tokens        | `False`   | `True`                         |
| `SHORTEN_MEDIA_LINKS`| Enable URL shortening for media links   | `False`   | `True`                         |
| `URL_SHORTENER_API_KEY` | API key for URL shortening service    | `""`      | `"abc123def456"`               |
| `URL_SHORTENER_SITE` | URL shortening service to use           | `""`      | `"example.com"`                  |
| `SET_COMMANDS`       | Automatically set bot commands on startup | `True`   | `False`                           |

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

### ⚡ Scaling for High Traffic or Speed

- Use multiple bot instances.
- Increase `WORKERS` in `config.env` based on your server's capabilities.

---

## 📱 Usage

### Basic

1. **Start:** Send `/start` to your bot.
2. **Authenticate:** Join required channels if configured by the admin.
3. **Token Authentication:** If token system is enabled, you'll need a valid token to use the bot. When you try to use a feature requiring authorization, the bot will automatically generate a token for you with an activation link.
4. **Upload:** Send any media file to the bot.
5. **Get Link:** Receive a direct streaming link.
6. **Share:** Anyone with the link can stream or download the file.

### Advanced

- **Batch Processing:** Use /link with a number to generate links for multiple files in a group chat (e.g., `/link 5`).
- **Data Center Info:** Use `/dc` to get the data center of a user or file.
- **Ping Bot:** Use `/ping` to check if the bot is online and measure response time.
- **Admin Commands:** If you are the bot owner, use admin commands like `/status`, `/broadcast`, `/stats`, etc., to manage the bot and users.

---

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
| `/authorize`   | Permanently authorize a user to use the bot (bypasses token system). |
| `/deauthorize` | Remove permanent authorization from a user.                          |
| `/listauth`    | List all permanently authorized users.                              |

### Commands for @BotFather

> If `SET_COMMANDS` is set to `True` in your configuration, the bot will automatically configure these commands.

Paste the following into the BotFather "Edit Commands" section for your bot.

```text
start - Start the bot and get a welcome message
link - (Group) Generate a direct link for a file or batch
dc - Retrieve the data center (DC) information of a user or file
ping - Check the bot's status and response time
about - Get information about the bot
help - Show help and usage instructions
status - (Admin) View bot details and current workload
stats - (Admin) View usage statistics and resource consumption
broadcast - (Admin) Send a message to all users
ban - (Admin) Ban a user
unban - (Admin) Unban a user
log - (Admin) Send bot logs
restart - (Admin) Restart the bot
shell - (Admin) Execute a shell command
users - (Admin) Show the total number of users
authorize - (Admin) Grant permanent access to a user
deauthorize - (Admin) Remove permanent access from a user
listauth - (Admin) List all authorized users
```

---

## 🔑 Token System

Thunder Bot includes an optional token-based access control system that allows admins to control who can use the bot.

### How It Works

1. Enable the token system by setting `TOKEN_ENABLED=True` in your config.
2. Users without a valid token will receive an "Access Denied" message when trying to use the bot
3. Admins can authorize users permanently, or users receive automatically generated tokens
 When a user sends a media to the bot, the bot will automatically shorten the link.

## 🔗 Url Shortening

Thunder Bot supports URL shortening for both media links and token activation links.

### How It Works

1. Provide a valid URL shortener API key and site in `URL_SHORTENER_API_KEY` and `URL_SHORTENER_SITE`.
2. Enable URL shortening in token activation links by setting `SHORTEN_ENABLED=True` in your config.
3. Enable URL shortening in media links links by setting `SHORTEN_MEDIA_LINKS=True` in your config.

---

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

This repository is unlicensed and provided as-is without any warranty. No permission is granted to use, copy, modify, or distribute this software for any purpose.

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
