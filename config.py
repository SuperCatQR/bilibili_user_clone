import os
from pathlib import Path

DEFAULT_INTERVAL = 3
DEFAULT_RETRY = 3
BACKOFF_BASE = 5
BACKOFF_MAX = 300
BATCH_SIZE = 50
BATCH_PAUSE_STEPS = [5, 10, 15, 20]
CHUNK_SIZE = 8192
CREDENTIAL_TTL_DAYS = 7
MAX_FILENAME_LENGTH = 200

CREDENTIAL_DIR = Path.home() / ".bilibili-cli"
CREDENTIAL_FILE = CREDENTIAL_DIR / "credential.json"

DB_DIR = Path(os.environ.get("BILIBILI_CLONE_DB_DIR", str(Path.home() / ".bilibili-cli")))
DB_FILE = DB_DIR / "downloads.db"

VALID_TYPES = {"video", "audio", "article", "dynamic"}
VIDEO_MODES = {"full", "video-only", "audio-only", "subtitle-only", "none"}
