import os
from pathlib import Path

# Load .env file if present (local development)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

API_ID    = int(os.environ.get("API_ID",    "22609670"))
API_HASH  = os.environ.get("API_HASH",      "")
BOT_TOKEN = os.environ.get("BOT_TOKEN",     "")

PASS_DB = int(os.environ.get("PASS_DB", "721"))

OWNER = int(os.environ.get("OWNER", "0"))

LOG = int(os.environ.get("LOG", "-1002070057679"))

try:
    ADMINS = []
    for x in os.environ.get("ADMINS", "").split():
        ADMINS.append(int(x))
except ValueError:
    raise Exception("ADMINS env var must be space-separated integers.")

if OWNER:
    ADMINS.append(OWNER)

# ── startup check ─────────────────────────────────────────────────────────────
if not BOT_TOKEN:
    raise RuntimeError(
        "\n\n  BOT_TOKEN not set!\n"
        "  Set it in your .env file or Heroku Config Vars.\n"
        "  Example: BOT_TOKEN=8977991558:AAF...\n"
    )
if not API_HASH:
    raise RuntimeError(
        "\n\n  API_HASH not set!\n"
        "  Get it from https://my.telegram.org/apps\n"
    )
