"""
断点续传存储模块

使用 SQLite 数据库记录每个内容的下载状态和枚举缓存，支持中断后重新运行时跳过已完成的项。

核心设计：
- 主键为 (uid, content_type, content_id)，确保同一用户同一类型的内容只有一条记录
- status 为 done/skipped 的项在枚举阶段被跳过
- status 为 failed 的项会被重新尝试（下次运行时）
- enum_cache 表缓存枚举结果，避免重复调用API翻页
- 缓存默认24小时过期，可通过环境变量 BILIBILI_CLONE_CACHE_TTL_HOURS 配置

批量提交优化：
- 每 batch_size 次操作自动提交一次，减少磁盘IO
- 适合大量小操作的场景（如逐项标记下载状态）

SQLite选择理由：
- 零配置，无需安装数据库服务器
- 单文件存储，便于备份和迁移
- 支持并发读（适合异步场景）
- 事务支持，保证数据一致性
"""

import json
import os
import time
import aiosqlite
from pathlib import Path
from config import DB_DIR, DB_FILE

# 缓存过期时间（小时），默认24小时
# 可通过环境变量 BILIBILI_CLONE_CACHE_TTL_HOURS 覆盖
# 24小时是经验值：太短会导致频繁重新枚举，太长会错过新内容
CACHE_TTL_HOURS = int(os.environ.get("BILIBILI_CLONE_CACHE_TTL_HOURS", "24"))


class DownloadStore:
    """异步 SQLite 下载状态存储，按 uid 隔离。支持批量提交优化性能。"""

    def __init__(self, uid: int, batch_size: int = 100):
        """
        初始化存储对象。

        Args:
            uid: 用户UID，用于隔离不同用户的下载记录
            batch_size: 批量提交的大小，每batch_size次操作自动提交一次
                        100是一个经验值：太小会增加commit频率，太大会增加内存占用
        """
        self.uid = uid
        self._db: aiosqlite.Connection | None = None  # 数据库连接
        self._batch_size = batch_size  # 批量提交阈值
        self._pending_operations = 0  # 待提交操作计数器

    async def open(self):
        """
        打开数据库连接并确保表结构存在。

        创建两个表：
        - downloads: 下载状态记录（主键: uid + content_type + content_id）
        - enum_cache: 枚举结果缓存（主键: uid + content_type）
        """
        # 确保数据库目录存在
        DB_DIR.mkdir(parents=True, exist_ok=True)

        # 打开SQLite连接
        # aiosqlite是SQLite的异步包装器，底层使用线程池执行同步操作
        self._db = await aiosqlite.connect(str(DB_FILE))

        # 创建downloads表（如果不存在）
        # 该表记录每个内容的下载状态
        await self._db.execute(
            """CREATE TABLE IF NOT EXISTS downloads (
                uid INTEGER NOT NULL,           -- 用户UID
                content_type TEXT NOT NULL,      -- 内容类型：video/audio/article/dynamic
                content_id TEXT NOT NULL,        -- 内容ID：BV号/AU号/cv号/动态ID
                status TEXT NOT NULL,            -- 下载状态：done/failed/skipped
                output_dir TEXT,                 -- 输出目录路径
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- 记录创建时间
                PRIMARY KEY (uid, content_type, content_id)      -- 复合主键
            )"""
        )

        # 创建enum_cache表（如果不存在）
        # 该表缓存枚举结果，避免重复调用API翻页
        await self._db.execute(
            """CREATE TABLE IF NOT EXISTS enum_cache (
                uid INTEGER NOT NULL,           -- 用户UID
                content_type TEXT NOT NULL,      -- 内容类型
                items_json TEXT NOT NULL,        -- 枚举结果的JSON序列化
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- 更新时间
                created_at REAL NOT NULL DEFAULT 0,              -- 创建时间戳（Unix时间）
                PRIMARY KEY (uid, content_type)                  -- 复合主键
            )"""
        )

        # 添加created_at列（如果不存在）
        # 这是数据库迁移逻辑，兼容旧版本数据库
        try:
            await self._db.execute("ALTER TABLE enum_cache ADD COLUMN created_at REAL NOT NULL DEFAULT 0")
        except Exception:
            pass  # 列已存在，忽略错误

        # 提交DDL语句
        await self._db.commit()

    async def close(self):
        """
        关闭数据库连接，提交所有未提交的操作。

        在关闭前确保所有pending操作都已提交到磁盘，
        防止数据丢失。
        """
        if self._db:
            # 提交所有未提交的操作
            if self._pending_operations > 0:
                await self._db.commit()
                self._pending_operations = 0

            # 关闭数据库连接
            await self._db.close()

    async def flush(self):
        """
        手动提交所有未提交的操作。

        在批量操作中间调用，确保数据持久化。
        例如在处理完一批下载后调用。
        """
        if self._db and self._pending_operations > 0:
            await self._db.commit()
            self._pending_operations = 0

    async def is_done(self, content_type: str, content_id: str) -> bool:
        """
        检查指定内容是否已完成（done 或 skipped）。

        skipped 视为已完成，因为：
        - none 模式下的视频被标记为 skipped（用户选择不下载）
        - 无字幕的视频被标记为 skipped（没有可用内容）
        - 重新运行时不应再次尝试这些项

        Args:
            content_type: 内容类型（video/audio/article/dynamic）
            content_id: 内容ID（BV号/AU号/cv号/动态ID）

        Returns:
            True表示已完成（done或skipped），False表示未完成或失败
        """
        # 查询数据库，检查是否存在done或skipped状态的记录
        cursor = await self._db.execute(
            "SELECT status FROM downloads WHERE uid=? AND content_type=? AND content_id=? AND status IN ('done', 'skipped')",
            (self.uid, content_type, content_id),
        )
        row = await cursor.fetchone()
        return row is not None

    async def mark(self, content_type: str, content_id: str, status: str, output_dir: str | None = None):
        """
        记录或更新下载状态。INSERT OR REPLACE 保证同一主键只保留最新状态。

        支持批量提交：每batch_size次操作自动提交一次，提高性能。

        Args:
            content_type: 内容类型
            content_id: 内容ID
            status: "done"(成功) / "failed"(失败，下次重试) / "skipped"(跳过，不再重试)
            output_dir: 输出目录路径（可选，用于记录文件位置）
        """
        # INSERT OR REPLACE：如果记录已存在则更新，不存在则插入
        # 这是SQLite的upsert语法，保证同一主键只有一条记录
        await self._db.execute(
            "INSERT OR REPLACE INTO downloads (uid, content_type, content_id, status, output_dir) VALUES (?, ?, ?, ?, ?)",
            (self.uid, content_type, content_id, status, output_dir),
        )

        # 增加待提交操作计数
        self._pending_operations += 1

        # 达到批量大小时自动提交
        # 这样可以将多次小操作合并为一次磁盘IO
        if self._pending_operations >= self._batch_size:
            await self._db.commit()
            self._pending_operations = 0

    async def save_enum_cache(self, content_type: str, items: list):
        """
        保存枚举结果缓存。items 为 DownloadItem 列表，序列化为 JSON 存储。

        缓存目的：
        - 避免重复调用API翻页（节省API配额）
        - 加速重新运行（直接从缓存加载）

        Args:
            content_type: 内容类型
            items: DownloadItem列表
        """
        # 将DownloadItem列表序列化为JSON-compatible的字典列表
        data = [
            {"content_type": it.content_type, "content_id": it.content_id,
             "title": it.title, "extra": it.extra}
            for it in items
        ]

        # INSERT OR REPLACE：如果缓存已存在则更新
        # created_at使用Unix时间戳，用于判断缓存是否过期
        await self._db.execute(
            "INSERT OR REPLACE INTO enum_cache (uid, content_type, items_json, created_at) VALUES (?, ?, ?, ?)",
            (self.uid, content_type, json.dumps(data, ensure_ascii=False), time.time()),
        )
        await self._db.commit()

    async def load_enum_cache(self, content_type: str) -> tuple[list[dict] | None, bool, int]:
        """
        加载枚举缓存。

        Args:
            content_type: 内容类型

        Returns:
            (缓存的字典列表, 是否过期, 缓存年龄小时数)
            无缓存时返回 (None, False, 0)
        """
        cursor = await self._db.execute(
            "SELECT items_json, created_at FROM enum_cache WHERE uid=? AND content_type=?",
            (self.uid, content_type),
        )
        row = await cursor.fetchone()

        if row is None:
            return None, False, 0

        items_json, created_at = row
        age = time.time() - created_at if created_at else 0
        is_expired = age > CACHE_TTL_HOURS * 3600
        age_hours = int(age / 3600)

        return json.loads(items_json), is_expired, age_hours

    async def clear_enum_cache(self, content_type: str | None = None):
        """
        清除枚举缓存。

        Args:
            content_type: 内容类型，None 表示清除该用户的所有缓存
        """
        if content_type:
            await self._db.execute(
                "DELETE FROM enum_cache WHERE uid=? AND content_type=?",
                (self.uid, content_type),
            )
        else:
            await self._db.execute(
                "DELETE FROM enum_cache WHERE uid=?",
                (self.uid,),
            )
        await self._db.commit()
