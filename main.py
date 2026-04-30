"""
CLI 入口模块

编排完整的 枚举→下载 流水线，输出下载报告。

整体架构：
- 使用 Click 定义命令行接口（CLI框架）
- 使用 Rich 输出彩色日志和统计表格（终端美化）
- 使用 asyncio.run() 驱动异步主流程

流水线步骤：
1. 解析CLI参数
2. 认证（QR码登录或加载已保存凭据）
3. 保存用户资料快照
4. 逐类型枚举内容（分页+时间过滤+跳过已完成）
5. 逐项下载（带间隔和阶梯暂停）
6. 输出统计报告

限速策略：
- 每项下载间隔 DEFAULT_INTERVAL 秒
- 每 BATCH_SIZE 项触发阶梯暂停（5s→10s→15s→20s循环）
- 412限速时指数退避重试
"""

import asyncio
import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

from config import VALID_TYPES, VIDEO_MODES, DEFAULT_INTERVAL, DEFAULT_RETRY, BATCH_SIZE, BATCH_PAUSE_STEPS
from auth import ensure_credential
from store import DownloadStore
from downloader import close_shared_session
from fetcher.enumerator import (
    DownloadItem,
    enumerate_videos,
    enumerate_audios,
    enumerate_articles,
    enumerate_dynamics,
)
from fetcher.video import download_video
from fetcher.audio import download_audio
from fetcher.article import download_article
from fetcher.dynamic import download_dynamic
from bilibili_api import user

console = Console()

ENUM_FNS = {
    "video": ("视频", enumerate_videos),
    "audio": ("音频", enumerate_audios),
    "article": ("专栏", enumerate_articles),
    "dynamic": ("动态", enumerate_dynamics),
}


async def _process_items(items: list[DownloadItem], download_fn, content_type_label: str, stats: dict, interval: int):
    """
    遍历并下载一组内容项。

    每项之间等待 interval 秒，每 BATCH_SIZE 项触发阶梯暂停
    （5s→10s→15s→20s 循环），避免触发B站412限速。
    """
    if not items:
        return

    count = 0
    with Progress(
        "[progress.description]{task.description}",
        "[progress.percentage]{task.percentage:>3.0f}%",
        "{task.completed}/{task.total}",
        console=console,
    ) as progress:
        task = progress.add_task(f"下载{content_type_label}", total=len(items))
        for item in items:
            stats["total"] += 1
            # 截断标题避免进度条过长
            short_title = item.title[:30] + ("..." if len(item.title) > 30 else "")
            progress.update(task, description=f"[cyan]{item.content_id}[/] {short_title}")

            try:
                ok = await download_fn(item)
                if ok:
                    stats["done"] += 1
                else:
                    stats["failed"] += 1
            except Exception as e:
                stats["failed"] += 1

            progress.advance(task)

            count += 1
            if count % BATCH_SIZE == 0:
                tier = min((count // BATCH_SIZE - 1) % len(BATCH_PAUSE_STEPS), len(BATCH_PAUSE_STEPS) - 1)
                pause = BATCH_PAUSE_STEPS[tier]
                progress.update(task, description=f"[dim]暂停 {pause}s...[/dim]")
                await asyncio.sleep(pause)
            else:
                await asyncio.sleep(interval)


async def save_user_info(uid: int, credential, base_dir: Path):
    """
    获取并保存用户资料快照到 info.json，同时打印用户名和UID。

    用户资料包含：昵称、头像、签名、等级、认证信息等。
    保存为JSON格式，便于后续数据分析。

    Args:
        uid: 用户UID
        credential: 认证凭据
        base_dir: 输出基目录（output/<UID>/）
    """
    # 创建User对象
    u = user.User(uid=uid, credential=credential)

    # 获取用户信息（调用B站API）
    info = await u.get_user_info()

    # 确保目录存在
    base_dir.mkdir(parents=True, exist_ok=True)

    # 写入info.json
    # indent=2: 格式化输出便于人工查看
    # ensure_ascii=False: 允许非ASCII字符（中文等）
    info_path = base_dir / "info.json"
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    # 打印用户信息
    name = info.get("name", "?")
    console.print(f"[green]用户: {name} (UID: {uid})[/green]")


async def run_clone(uid: int, output: str, types: str, video_mode: str, interval: int, retry: int, hours: int | None):
    """
    主下载流水线。

    流程：解析参数 → 认证 → 保存用户资料 → 逐类型枚举(分页+时间过滤+跳过已完成) →
    逐项下载 → 阶梯暂停(每50项) → 输出统计报告。

    Args:
        uid: 用户UID
        output: 输出目录
        types: 内容类型（逗号分隔）
        video_mode: 视频下载模式
        interval: 请求间隔（秒）
        retry: 重试次数
        hours: 只下载指定小时内发布的内容（None表示不限制）
    """
    # 解析内容类型
    # 将逗号分隔的字符串转换为集合，并过滤非法值
    selected_types = set(t.strip() for t in types.split(",") if t.strip() in VALID_TYPES)
    if not selected_types:
        # 如果没有指定有效类型，使用所有类型
        selected_types = VALID_TYPES.copy()

    # 检查video-mode参数的有效性
    if "video" not in selected_types and video_mode != "full":
        console.print(f"[yellow]警告: --types 不含 video，--video-mode={video_mode} 无效[/yellow]")

    if video_mode not in VIDEO_MODES:
        console.print(f"[red]无效的 video-mode: {video_mode}[/red]")
        sys.exit(1)

    # 获取认证凭据（QR码登录或加载已保存凭据）
    credential = await ensure_credential()

    # 构建输出目录：output/<UID>/
    base_dir = Path(output) / str(uid)

    # 保存用户资料快照
    await save_user_info(uid, credential, base_dir)

    # 初始化下载状态存储
    store = DownloadStore(uid)
    await store.open()

    try:
        stats = {"total": 0, "done": 0, "failed": 0}

        if "video" in selected_types:
            label, enum_fn = ENUM_FNS["video"]
            with console.status(f"[bold green]枚举{label}...[/bold green]"):
                items = await enum_fn(uid, credential, store, hours, retries=retry)
            console.print(f"  待下载: {len(items)} 个{label}")
            await _process_items(items, lambda item: download_video(item, uid, credential, store, base_dir, video_mode, retries=retry), label, stats, interval)

        if "audio" in selected_types:
            label, enum_fn = ENUM_FNS["audio"]
            with console.status(f"[bold green]枚举{label}...[/bold green]"):
                items = await enum_fn(uid, credential, store, hours, retries=retry)
            console.print(f"  待下载: {len(items)} 个{label}")
            await _process_items(items, lambda item: download_audio(item, uid, credential, store, base_dir, retries=retry), label, stats, interval)

        if "article" in selected_types:
            label, enum_fn = ENUM_FNS["article"]
            with console.status(f"[bold green]枚举{label}...[/bold green]"):
                items = await enum_fn(uid, credential, store, hours, retries=retry)
            console.print(f"  待下载: {len(items)} 个{label}")
            await _process_items(items, lambda item: download_article(item, uid, credential, store, base_dir, retries=retry), label, stats, interval)

        if "dynamic" in selected_types:
            label, enum_fn = ENUM_FNS["dynamic"]
            with console.status(f"[bold green]枚举{label}...[/bold green]"):
                items = await enum_fn(uid, credential, store, hours, retries=retry)
            console.print(f"  待下载: {len(items)} 个{label}")
            await _process_items(items, lambda item: download_dynamic(item, uid, credential, store, base_dir, retries=retry), label, stats, interval)

        # 输出下载报告
        console.print("\n[bold]===== 下载报告 =====[/bold]")
        table = Table()
        table.add_column("指标", style="cyan")
        table.add_column("数量", style="green")
        table.add_row("总计", str(stats["total"]))
        table.add_row("成功", str(stats["done"]))
        table.add_row("失败", str(stats["failed"]))
        console.print(table)

    finally:
        # 确保资源释放（无论是否发生异常）
        await store.close()
        await close_shared_session()


# ============================================================
# Click CLI 定义
# ============================================================

@click.group()
def cli():
    """B站用户内容克隆工具"""
    pass


@cli.command()
@click.argument("uid", type=int)
@click.option("--output", default="./output", help="输出目录")
@click.option("--types", default="video,audio,article,dynamic", help="内容类型，逗号分隔")
@click.option("--video-mode", default="full", type=click.Choice(list(VIDEO_MODES)), help="视频下载模式")
@click.option("--interval", default=DEFAULT_INTERVAL, type=int, help="请求间隔(秒)")
@click.option("--retry", default=DEFAULT_RETRY, type=int, help="412重试次数")
@click.option("--hours", default=None, type=int, help="只下载指定小时内发布的内容")
def clone(uid, output, types, video_mode, interval, retry, hours):
    """
    克隆B站用户的所有内容。

    UID: 目标用户的UID（数字）
    """
    # asyncio.run() 启动异步事件循环
    # 这是Python 3.7+的标准异步入口
    asyncio.run(run_clone(uid, output, types, video_mode, interval, retry, hours))


async def run_update_cache(uid: int, types: str, retry: int):
    """
    强制更新枚举缓存。

    流程：认证 → 逐类型清除旧缓存 → 强制重新枚举 → 保存新缓存。
    不执行下载，仅更新缓存数据。
    """
    selected_types = set(t.strip() for t in types.split(",") if t.strip() in VALID_TYPES)
    if not selected_types:
        selected_types = VALID_TYPES.copy()

    credential = await ensure_credential()

    store = DownloadStore(uid)
    await store.open()

    try:
        stats = {}

        for ctype in ("video", "audio", "article", "dynamic"):
            if ctype not in selected_types:
                continue
            label, enum_fn = ENUM_FNS[ctype]
            with console.status(f"[bold green]更新{label}缓存...[/bold green]"):
                await store.clear_enum_cache(ctype)
                items = await enum_fn(uid, credential, store, retries=retry, force=True)
            await store.save_enum_cache(ctype, items)
            stats[ctype] = len(items)
            console.print(f"  共 {len(items)} 个{label}")

        console.print("\n[bold]===== 缓存更新报告 =====[/bold]")
        table = Table()
        table.add_column("类型", style="cyan")
        table.add_column("数量", style="green")
        for ctype, count in stats.items():
            table.add_row(ctype, str(count))
        console.print(table)

    finally:
        await store.close()
        await close_shared_session()


@cli.command()
@click.argument("uid", type=int)
@click.option("--types", default="video,audio,article,dynamic", help="内容类型，逗号分隔")
@click.option("--retry", default=DEFAULT_RETRY, type=int, help="API重试次数")
def update_cache(uid, types, retry):
    """
    强制更新枚举缓存。

    重新调用API枚举用户的全部内容，并更新本地缓存。
    下次运行 clone 时会使用新缓存。
    """
    asyncio.run(run_update_cache(uid, types, retry))


if __name__ == "__main__":
    cli()
