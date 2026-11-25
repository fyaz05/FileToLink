<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/fyaz05/Resources@main/FileToLink/Thunder.jpg" alt="Thunder Logo" width="120">
  <h1 align="center">⚡ Thunder</h1>
</p>

<p align="center">
  <b>High-Performance Telegram File-to-Link Bot for Direct Links & Streaming</b>
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.15-blue?style=for-the-badge&logo=python" alt="Python Version"></a>
  <a href="https://github.com/rjriajul/wzgram"><img src="https://img.shields.io/badge/wzgram-red?style=for-the-badge" alt="wzgram"></a>
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
    - [Deploy to Heroku](#deploy-to-heroku)
  - [Reverse Proxy Setup](#reverse-proxy-setup)
- [Support & Community](#support--community)
  - [Troubleshooting & FAQ](#troubleshooting--faq)
  - [Contributing](#contributing)
- [License](#license)
- [Acknowledgments](#acknowledgments)

<hr>

## About The Project

**Thunder** is a Telegram bot that turns files into high-speed direct links for streaming and downloading. Share files via HTTP(S) links without downloading them through Telegram first.

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
| `SLEEP_THRESHOLD` | Flood-wait threshold in seconds | `600` |
| `WORKERS` | Async workers | `8` |
| `NAME` | Bot name; also the MongoDB database name (unsafe chars become `_`) | `ThunderF2L` |
| `BIND_ADDRESS` | Bind address | `0.0.0.0` |
| `PING_INTERVAL` | Ping interval (seconds) | `840` |
| `CHANNEL_WATCH_INTERVAL` | Recheck storage-channel access this often, in seconds; `0` disables | `3600` |
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
| `GLOBAL_RATE_LIMIT` | Global limiting | `False` |
| `MAX_GLOBAL_REQUESTS_PER_MINUTE` | Global limit | `4` |
| `UPSTREAM_REPO` | Git repo to self-update from; ignored without `.git` | `https://github.com/fyaz05/FileToLink` |
| `UPSTREAM_BRANCH` | Branch `UPSTREAM_REPO` updates from | `main` |

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
| `/doctor` | Re-check the whole configuration without restarting. |
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
doctor - [Admin] Re-check the configuration without restarting
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

Thunder uses a multi-tier rate limiting system:

#### **Priority Queue Architecture**

- **Owner Priority**: Bypasses all rate limits.
- **Authorized Users**: Dedicated priority queue, faster processing.
- **Regular Users**: Standard queue with fair scheduling.

#### **Multi-Level Rate Limiting**

- **Per-User Limits**: Configurable files per time window.
- **Global Limits**: System-wide request throttling.
- **Sliding Window**: Time-based limiting with automatic cleanup.

#### **Smart Queue Management**

- **Automatic Re-queuing**: Rate-limited requests are re-queued.
- **Queue Size Limits**: Configurable max queue size.
- **Flood Protection**: Guards against Telegram flood waits.

### Network Speed Testing

Monitor server performance with built-in speed testing:

```bash
/speedtest
```

Shows download/upload speeds, latency, and shareable result images.

## Deployment Guide

Covers setup from prerequisites to production.

### Prerequisites

| Requirement | Description | Source |
| :--- | :--- | :--- |
| Python 3.15 | Programming language | [python.org](https://python.org) |
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

# 3. Build and run (the config is mounted — it is not baked into the image)
docker build -t thunder .
docker run -d --name thunder -p 8080:8080 \
  -v "$PWD/config.env:/app/config.env:ro" thunder
```

(PowerShell: `-v "${PWD}\config.env:/app/config.env:ro"`.)

The container runs as uid `10001`, so `config.env` must be readable by that UID
without exposing the bot token and database credentials to every local account.
On a Linux host, prefer an ACL:

```bash
chmod 600 config.env
sudo setfacl -m u:10001:r config.env
```

If ACLs are unavailable, set the file's group to the container's GID and use
mode `640`:

```bash
sudo chgrp 10001 config.env
chmod 640 config.env
```

Avoid `chmod 644` on shared hosts.

`--stop-timeout 60` is optional: it lets the web server and in-flight request
executor each drain for up to 25 seconds (`docker stop` defaults to 10s):

```bash
docker run -d --stop-timeout 60 --name thunder -p 8080:8080 \
  -v "$PWD/config.env:/app/config.env:ro" thunder
```

Runs as unprivileged uid `10001` with no build toolchain. `.dockerignore` excludes
env files, so `config.env` is not baked in and must be supplied at runtime.

<details>
<summary>Alternative: pass configuration as environment variables</summary>

`--env-file` passes values verbatim, so inline comments/quotes in
`config_sample.env` (`HAS_SSL="True" # ...`) corrupt variables. Generate a clean file
with `python-dotenv`:

```bash
(umask 077; python3 -c "from dotenv import dotenv_values as d; [print(f'{k}={v}') for k, v in d('config.env').items()]" > /tmp/thunder-docker.env)
docker run -d --name thunder -p 8080:8080 --env-file /tmp/thunder-docker.env thunder
rm -f /tmp/thunder-docker.env
```

Needs `python-dotenv` locally (`pip install python-dotenv`); mounting `config.env`
avoids this. Keep the generated file outside the repo so it cannot enter the build
context, and remove it after Docker has read it.

</details>

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

> **Tip:** Start with the essential config to get running, then add optional features.

</details>

## Quick Deploy

### Deploy to Koyeb

[![Deploy to Koyeb](https://www.koyeb.com/static/images/deploy/button.svg)](https://app.koyeb.com/deploy?type=docker&image=docker.io/fyaz05/thunder:latest&name=thunder&ports=8080;http;/&env[API_ID]=&env[API_HASH]=&env[BOT_TOKEN]=&env[BIN_CHANNEL]=&env[OWNER_ID]=&env[DATABASE_URL]=&env[FQDN]=)

To add more environment variables after deployment, use the Koyeb dashboard under **Settings** → **Environment Variables**.

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

### Deploy to Heroku

1. Install the [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli) and login.
2. Create an app in the EU region (required for GDPR compliance):
   ```bash
   heroku create your-app-name --region eu
   heroku stack:set container
   ```
3. Set your config vars:
   ```bash
   heroku config:set API_ID="your_id" API_HASH="your_hash" BOT_TOKEN="your_token" \
     BIN_CHANNEL="-100xxx" OWNER_ID="your_id" FQDN="your-app-name.herokuapp.com" \
     HAS_SSL="True" NO_PORT="True" PORT="8080"
   ```
4. Set `DATABASE_URL` via the Heroku Dashboard or API (ampersand in MongoDB URL causes shell issues).
5. Deploy via source upload (Heroku does not support direct git push with API key auth):
   ```bash
   # Create source tarball
   tar -czf source.tar.gz --exclude='.git' --exclude='__pycache__' .
   # Upload via Heroku Builds API — see devcenter.heroku.com/articles/build-and-release-using-the-api
   ```
6. Scale the dyno:
   ```bash
   heroku ps:scale web=1
   ```
7. Restarts update from `UPSTREAM_REPO` only when a git checkout exists. A Heroku slug is built from a tarball (step 5) with no `.git`, so updates are skipped and the dyno runs the slug's code. Durable changes need a new slug:
   ```bash
   # Only takes effect on a deployment that has a .git directory (e.g. a VM checkout).
   heroku config:set UPSTREAM_REPO="https://github.com/fyaz05/FileToLink" UPSTREAM_BRANCH="main"
   ```

> **Note:** Heroku provides HTTPS automatically. Set `FQDN` to `your-app-name.herokuapp.com` and `HAS_SSL` to `True`.

> **Note:** `heroku.yml` builds this repo's `Dockerfile`, so the dyno runs as uid `10001`
> and cannot read a root-only `config.env`. Config vars avoid the file, hence this path.
> The image's `Thunder/logs` is already writable, so no `chmod` is needed here.

> **Note:** See the [Configuration](#configuration) section for required environment variables.

## Reverse Proxy Setup

<details>
<summary>Reverse Proxy Guide</summary>

Sets up a secure **NGINX** reverse proxy for the streaming bot with **Cloudflare SSL**.

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

Create a folder, place `cert.pem` and `key.key` inside, and secure the private key.

```bash
sudo mkdir -p /etc/ssl/cloudflare/f2l.thunder.com
# Move/copy your cert.pem and key.key files into this directory
sudo chmod 600 /etc/ssl/cloudflare/f2l.thunder.com/key.key
sudo chmod 644 /etc/ssl/cloudflare/f2l.thunder.com/cert.pem
```

---

#### 🛠️ Step 3: Create NGINX Configuration

Create `/etc/nginx/sites-available/f2l.thunder.conf` with the following, replacing `f2l.thunder.com` and `8080`:

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

Your reverse proxy now streams files securely behind Cloudflare!

</details>

## Support & Community

### Troubleshooting & FAQ

#### **Initial Setup**

**Q: Why isn't my bot responding after setup?**
A: Usually a configuration issue. Check:

1. **Verify `config.env`**: All essential variables (`API_ID`, `API_HASH`, `BOT_TOKEN`, `BIN_CHANNEL`, `DATABASE_URL`) must be filled in.
2. **Use environment settings**: Do not edit `vars.py` or `config_sample.env`. The bot reads environment variables directly and can also load them from `config.env`; existing environment values take precedence.
3. **Check Logs**: Review console logs on your server or host (Koyeb, Render, Heroku) for startup errors.

**Q: The bot reports a configuration problem and does not start. What now?**
A: Fix all listed problems at once. On a running bot, `/doctor` reruns the checks.
Run `python -m Thunder --check` to validate without serving; it excludes the public-URL probe.
Normal starts show problems unless `--verbose` is set.

Startup checks Telegram credentials, channel rights, database writes, owner ID,
secondary tokens, shortener, and the public `/status` reply after the server binds.
Unambiguous values are corrected and reported.

**Q: What do I use for the `FQDN` variable?**
A: It's the public URL or IP address of your bot.

- **With a Domain**: Use your subdomain (e.g., `f2l.thunder.com`).
- **On Koyeb/Render/Heroku**: Use the public URL provided by the platform.
- **On a VPS**: Use your server's public IP address.

**Q: Why are my links not working on a VPS?**
A: The URL must include the port (e.g., `http://YOUR_VPS_IP:8080`). Set `NO_PORT=False` in `config.env` and allow traffic through that port on your server.

#### **Common Errors**

**Q: Why are my links showing a "Resource Not Found" error or not working?**
A: The bot can't access the file. Check:

1. **Invalid Token**: Your `BOT_TOKEN` or one of the `MULTI_TOKEN`s might be wrong; verify with @BotFather.
2. **Missing Admin Rights**: The bot and **all** your client accounts must be **administrators** in the `BIN_CHANNEL`.
3. **File Deleted**: The link breaks if the file was deleted from `BIN_CHANNEL`.

**Q: Why isn't video or audio playing correctly in my browser?**
A: Your browser likely doesn't support the file's audio or video format (codec). This is a browser limitation, not a bot issue.

- **Solution**: Copy the link and play it in a dedicated media player: **VLC Media Player**, **MX Player**, **PotPlayer**, **IINA**, or **MPV**.

**Q: Why does the bot sometimes become unresponsive?**
A: Likely a **Telegram Flood Wait**: Telegram temporarily limits accounts making too many requests. The bot pauses automatically and resumes once the limit lifts.

**Q: On shutdown I see `Task was destroyed but it is pending!` — is something broken?**
A: Not if `✓ Database connection closed` printed first. Each session has a
`recv_worker` and a `ping_worker`, which the library closes on stop. While
running, expect one of each per connected session — twelve with eight clients
where six had connected.

#### **Performance**

**Q: How can I fix slow download and streaming speeds?**
A: Try:

- **Add More Clients**: Best fix. Add `MULTI_TOKEN`s to distribute load and raise throughput.
- **Use DC4 Accounts**: Accounts from **Data Center 4 (DC4)** often have the fastest connection; check with `/dc`.
- **Upgrade Your Server**: A slow network bottlenecks speeds; consider a better VPS plan.

#### **Bot Usage**

**Q: How do I generate links for multiple files at once?**
A: `/link` processes files sent in sequence. **Reply to the first file** with the command and total count.

- **Example**: For 5 files, reply to the first with `/link 5`.

**Q: Can I mix tokens from different accounts and data centers?**
A: Yes. Mixing clients across accounts and data centers (DC1, DC4, DC5) improves performance and reliability.

### Contributing

Contributions welcome. To contribute:

1. Fork the repository.
2. Create a new feature branch (`git checkout -b feature/amazing-feature`).
3. Make your change and run the suite and linter:
   ```bash
   PYYAML_FORCE_LIBYAML=0 pip install -r requirements-dev.txt   # POSIX
   python -m pytest -q
   python -m ruff check .
   ```
   Needed on Python 3.15 (no `cp315` wheel). On Windows, set it first:
   ```powershell
   $env:PYYAML_FORCE_LIBYAML = "0"; pip install -r requirements-dev.txt
   ```
4. Commit your changes (`git commit -m 'Add some amazing feature'`).
5. Push to the branch (`git push origin feature/amazing-feature`).
6. Open a Pull Request.

CI runs the suite on Linux and Windows, then builds the image once both pass.

## License

Licensed under [Apache License 2.0](LICENSE).

## Acknowledgments

- [wzgram](https://github.com/rjriajul/wzgram) - Telegram MTProto API Framework
- [aiohttp](https://github.com/aio-libs/aiohttp) - Asynchronous HTTP Client/Server
- [PyMongo](https://github.com/mongodb/mongo-python-driver) - Asynchronous MongoDB Driver
- [WarpCrypto](https://github.com/rjriajul/warpcrypto) - High-performance cryptography library (Rust)

## ⚠️ Disclaimer

Not affiliated with Telegram. Use responsibly and in compliance with Telegram's Terms of Service and applicable local regulations.

---

<p align="center">
  <b>⭐ Star this project if you find it useful!</b><br>
  <a href="https://github.com/fyaz05/FileToLink/issues/new">Report Bug</a> •
  <a href="https://github.com/fyaz05/FileToLink/issues/new">Request Feature</a>
</p>
