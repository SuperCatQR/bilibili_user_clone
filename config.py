"""
全局配置常量模块

定义项目所有运行参数、路径和合法值集合。
"""

import os
from pathlib import Path

# 请求间隔（秒），每次API调用或下载之间的等待时间，避免触发B站限速
DEFAULT_INTERVAL = 3

# API请求失败时的最大重试次数
DEFAULT_RETRY = 3

# 412限速指数退避的基础等待秒数，实际等待 = BACKOFF_BASE * 2^attempt
BACKOFF_BASE = 5

# 412退避等待上限（秒）
BACKOFF_MAX = 300

# 每处理多少项后触发一次阶梯暂停
BATCH_SIZE = 50

# 阶梯暂停时长循环序列（秒），每BATCH_SIZE项后按此序列循环暂停
BATCH_PAUSE_STEPS = [5, 10, 15, 20]

# 文件下载分块大小（字节）
CHUNK_SIZE = 8192

# 凭据有效期（天），超过后需重新扫码登录
CREDENTIAL_TTL_DAYS = 7

# 文件名最大长度（字符），超出部分截断
MAX_FILENAME_LENGTH = 200

# 凭据文件目录和路径
CREDENTIAL_DIR = Path.home() / ".bilibili-cli"
CREDENTIAL_FILE = CREDENTIAL_DIR / "credential.json"

# SQLite断点续传数据库目录和路径，可通过环境变量 BILIBILI_CLONE_DB_DIR 覆盖
DB_DIR = Path(os.environ.get("BILIBILI_CLONE_DB_DIR", ".bilibili-clone"))
DB_FILE = DB_DIR / "downloads.db"

# 合法的内容类型集合
VALID_TYPES = {"video", "audio", "article", "dynamic"}

# 合法的视频下载模式集合
VIDEO_MODES = {"full", "video-only", "audio-only", "subtitle-only", "none"}
