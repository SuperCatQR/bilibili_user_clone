import aiosqlite
from pathlib import Path
from config import DB_DIR, DB_FILE


class DownloadStore:
    def __init__(self, uid: int):
        self.uid = uid
        self._db: aiosqlite.Connection | None = None

    async def open(self):
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
        await self._db.commit()

    async def close(self):
        if self._db:
            await self._db.close()

    async def is_done(self, content_type: str, content_id: str) -> bool:
        cursor = await self._db.execute(
            "SELECT status FROM downloads WHERE uid=? AND content_type=? AND content_id=? AND status IN ('done', 'skipped')",
            (self.uid, content_type, content_id),
        )
        row = await cursor.fetchone()
        return row is not None

    async def mark(self, content_type: str, content_id: str, status: str, output_dir: str | None = None):
        await self._db.execute(
            "INSERT OR REPLACE INTO downloads (uid, content_type, content_id, status, output_dir) VALUES (?, ?, ?, ?, ?)",
            (self.uid, content_type, content_id, status, output_dir),
        )
        await self._db.commit()
