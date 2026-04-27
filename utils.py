import re
from config import MAX_FILENAME_LENGTH


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.strip(". ")
    if len(name) > MAX_FILENAME_LENGTH:
        name = name[:MAX_FILENAME_LENGTH]
    if not name:
        name = "unnamed"
    return name
