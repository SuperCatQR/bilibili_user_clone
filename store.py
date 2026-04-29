"""
断点续传存储模块

使用 SQLite 数据库记录每个内容的下载状态和枚举缓存，支持中断后重新运行时跳过已完成的项。
主键为 (uid, content_type, content_id)，status 为 done/skipped 的项在枚举阶段被跳过，
status 为 failed 的项会被重新尝试。
enum_cache 表缓存枚举结果，重新运行时无需再次翻页调API。
"""

import json
import aiosqlite
from pathlib import Path
from config import DB_DIR, DB_FILE


class DownloadStore:
    """异步 SQLite 下载状态存储，按 uid 隔离。"""

    def __init__(self, uid: int):
        self.uid = uid
        self._db: aiosqlite.Connection | None = None

    async def open(self):
        """打开数据库连接并确保表结构存在。"""
        DB_DIR.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(DB_FILE))
        await self._db.execute(
            """CREATE TABLE IF NOT EXISTS downloads (
                uid INTEGER NOT NULL,
                content_type TEXT NOT NULL,
                content_id TEXT NOT NULL,
                status TEXT NOT NULL,
                output_dir TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (uid, content_type, content_id)
            )"""
        )
        await self._db.execute(
            """CREATE TABLE IF NOT EXISTS enum_cache (
                uid INTEGER NOT NULL,
                content_type TEXT NOT NULL,
                items_json TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (uid, content_type)
            )"""
        )
        await self._db.commit()

    async def close(self):
        """关闭数据库连接。"""
        if self._db:
            await self._db.close()

    async def is_done(self, content_type: str, content_id: str) -> bool:
        """
        检查指定内容是否已完成（done 或 skipped）。
        
        skipped 视为已完成，因为 none 模式和无字幕的视频被标记为 skipped，
        重新运行时不应再次尝试。
        """
        cursor = await self._db.execute(
            "SELECT status FROM downloads WHERE uid=? AND content_type=? AND content_id=? AND status IN ('done', 'skipped')",
            (self.uid, content_type, content_id),
        )
        row = await cursor.fetchone()
        return row is not None

    async def mark(self, content_type: str, content_id: str, status: str, output_dir: str | None = None):
        """
        记录或更新下载状态。INSERT OR REPLACE 保证同一主键只保留最新状态。
        
        Args:
            status: "done"(成功) / "failed"(失败，下次重试) / "skipped"(跳过，不再重试)
        """
        await self._db.execute(
            "INSERT OR REPLACE INTO downloads (uid, content_type, content_id, status, output_dir) VALUES (?, ?, ?, ?, ?)",
            (self.uid, content_type, content_id, status, output_dir),
        )
        await self._db.commit()

    async def save_enum_cache(self, content_type: str, items: list):
        """保存枚举结果缓存。items 为 DownloadItem 列表，序列化为 JSON 存储。"""
        data = [
            {"content_type": it.content_type, "content_id": it.content_id,
             "title": it.title, "extra": it.extra}
            for it in items
        ]
        await self._db.execute(
            "INSERT OR REPLACE INTO enum_cache (uid, content_type, items_json) VALUES (?, ?, ?)",
            (self.uid, content_type, json.dumps(data, ensure_ascii=False)),
        )
        await self._db.commit()

    async def load_enum_cache(self, content_type: str) -> list[dict] | None:
        """加载枚举缓存，返回 dict 列表或 None（无缓存时）。"""
        cursor = await self._db.execute(
            "SELECT items_json FROM enum_cache WHERE uid=? AND content_type=?",
            (self.uid, content_type),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return json.loads(row[0])
