import os

API_ID   = int(os.environ.get("API_ID",   "0"))
API_HASH = os.environ.get("API_HASH",     "")
BOT_TOKEN = os.environ.get("BOT_TOKEN",   "")

PASS_DB = int(os.environ.get("PASS_DB", "721"))

OWNER = int(os.environ.get("OWNER", "0"))

LOG = int(os.environ.get("LOG", "0"))

try:
    ADMINS = []
    for x in os.environ.get("ADMINS", "").split():
        ADMINS.append(int(x))
except ValueError:
    raise Exception("ADMINS env var must contain space-separated integers.")
if OWNER:
    ADMINS.append(OWNER)


