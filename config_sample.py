# =============================================================
# Rename this file to config.py before using
# =============================================================

from typing import List, Set, Optional

####################
## REQUIRED SETTINGS
####################

# Telegram API credentials (from https://my.telegram.org/apps)
API_ID: int = 0          # Example: 1234567
API_HASH: str = ""       # Example: "abc123def456"

# Bot token (from @BotFather)
BOT_TOKEN: str = ""      # Example: "123456789:ABCdef..."

# Storage channel ID (create a channel and add bot as admin)
BIN_CHANNEL: int = 0     # Example: -1001234567890

# Owner information (get ID from @userinfobot)
OWNER_ID: List[int] = [0]  # Your Telegram user ID(s)
OWNER_USERNAME: str = ""   # Example: "YourUsername" (without @)

# Deployment configuration
FQDN: str = ""           # Your domain name (leave empty to use IP)
HAS_SSL: bool = False    # Set to True if using HTTPS
PORT: int = 8080         # Web server port
NO_PORT: bool = True     # Hide port in URLs

# Database connection string
DATABASE_URL: str = ""   # Example: "mongodb+srv://user:pass@host/db"

####################
## OPTIONAL SETTINGS
####################

# Multiple bot tokens for load distribution
MULTI_BOT_TOKENS: List[str] = [
    # "123456789:ABCdef...",
    # Add more tokens if needed, up to 49, and make sure to add them to the bin channel.
]

# Force users to join a specific channel before using the bot
FORCE_CHANNEL_ID: Optional[int] = None  # Example: -1001234567890

# Banned channels (files from these channels will be rejected)
BANNED_CHANNELS: Set[int] = set()  # Example: {-1001234567890}

####################
## ADVANCED SETTINGS (modify with caution)
####################

# Application name
NAME: str = "ThunderF2L"  # Bot application name

# Performance settings
SLEEP_THRESHOLD: int = 60  # Sleep time in seconds
WORKERS: int = 100         # Number of worker processes

# Web server configuration
BIND_ADDRESS: str = "0.0.0.0"  # Listen on all network interfaces
PING_INTERVAL: int = 840       # Ping interval in seconds
CACHE_SIZE: int = 100          # Cache size in MB
