# language: Python, file: config.py, target: Telegram bot + Kaggle 2×T4
# *All tunables live here so the same binary runs local or on Kaggle without edits.*

import os
from pathlib import Path

# ── Telegram ──────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")
ALLOWED_USER_IDS = set()  # empty = personal / open to whoever has the token
# example: ALLOWED_USER_IDS = {123456789}

# ── Paths ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
RESULTS_DIR = DATA_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)
CHECKPOINT_DIR = DATA_DIR / "checkpoints"
CHECKPOINT_DIR.mkdir(exist_ok=True)

# ── Parser performance (tuned for 2×T4 / high concurrency) ───────────────
MAX_CONCURRENT_REQUESTS = 120          # total in-flight HTTP
MAX_CONCURRENT_PER_PROXY = 8
REQUEST_TIMEOUT = 12.0                 # seconds
CONNECT_TIMEOUT = 6.0
LIVE_CHECK_TIMEOUT = 8.0
WAVE_SIZE = 250                        # dorks per wave before checkpoint
PROGRESS_EVERY = 25                    # Telegram status update frequency
USER_AGENT_ROTATE = True

# ── Yahoo Japan ───────────────────────────────────────────────────────────
YAHOO_JP_SEARCH = "https://search.yahoo.co.jp/search"
DEFAULT_PARAMS = {
    "ei": "UTF-8",
    "fr": "top_ga1_sa",
    "b": "1",                          # start offset, we paginate later if needed
}

# ── Filters ───────────────────────────────────────────────────────────────
SOFT_404_MARKERS = [
    "ページが見つかりません",
    "not found",
    "404",
    "page does not exist",
    "このページは存在しません",
]
MIN_CONTENT_LENGTH = 400               # bytes — below this = likely dead
REJECT_STATUS = {400, 401, 403, 404, 410, 429, 500, 502, 503, 504}

# ── Proxy ─────────────────────────────────────────────────────────────────
PROXY_FILE = DATA_DIR / "proxies.txt"  # one per line: http://user:pass@host:port
PROXY_HEALTH_INTERVAL = 60             # seconds
MAX_PROXY_FAILURES = 4

# ── Logging ───────────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"
