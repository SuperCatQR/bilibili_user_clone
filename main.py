import asyncio
import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from config import VALID_TYPES, VIDEO_MODES, DEFAULT_INTERVAL, DEFAULT_RETRY, BATCH_SIZE, BATCH_PAUSE_STEPS
from auth import ensure_credential
from store import DownloadStore
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


async def save_user_info(uid: int, credential, base_dir: Path):
    u = user.User(uid=uid, credential=credential)
    info = await u.get_user_info()
    base_dir.mkdir(parents=True, exist_ok=True)
    info_path = base_dir / "info.json"
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    name = info.get("name", "?")
    console.print(f"[green]用户: {name} (UID: {uid})[/green]")


async def run_clone(uid: int, output: str, types: str, video_mode: str, interval: int, retry: int, hours: int | None):
    selected_types = set(t.strip() for t in types.split(",") if t.strip() in VALID_TYPES)
    if not selected_types:
        selected_types = VALID_TYPES.copy()

    if "video" not in selected_types and video_mode != "full":
        console.print(f"[yellow]警告: --types 不含 video，--video-mode={video_mode} 无效[/yellow]")

    if video_mode not in VIDEO_MODES:
        console.print(f"[red]无效的 video-mode: {video_mode}[/red]")
        sys.exit(1)

    credential = await ensure_credential()
    base_dir = Path(output) / str(uid)

    await save_user_info(uid, credential, base_dir)

    store = DownloadStore(uid)
    await store.open()

    try:
        stats = {"total": 0, "done": 0, "skipped": 0, "failed": 0}

        async def _process_items(items, download_fn, content_type_label):
            count = 0
            for item in items:
                stats["total"] += 1
                console.print(f"\n[cyan][{content_type_label}][/] {item.content_id} - {item.title}")
                try:
                    ok = await download_fn(item)
                    if ok:
                        stats["done"] += 1
                        console.print(f"[green]  ✓ 完成[/green]")
                    else:
                        stats["failed"] += 1
                        console.print(f"[red]  ✗ 失败[/red]")
                except Exception as e:
                    stats["failed"] += 1
                    console.print(f"[red]  ✗ 异常: {e}[/red]")

                count += 1
                if count % BATCH_SIZE == 0:
                    tier = min((count // BATCH_SIZE - 1) % len(BATCH_PAUSE_STEPS), len(BATCH_PAUSE_STEPS) - 1)
                    pause = BATCH_PAUSE_STEPS[tier]
                    console.print(f"[dim]已处理 {count} 项，暂停 {pause}s...[/dim]")
                    await asyncio.sleep(pause)
                else:
                    await asyncio.sleep(interval)

        if "video" in selected_types:
            console.print("\n[bold]枚举视频...[/bold]")
            videos = await enumerate_videos(uid, credential, store, hours)
            console.print(f"  待下载: {len(videos)} 个视频")

            async def _dl_video(item):
                return await download_video(item, uid, credential, store, base_dir, video_mode)
            await _process_items(videos, _dl_video, "视频")

        if "audio" in selected_types:
            console.print("\n[bold]枚举音频...[/bold]")
            audios = await enumerate_audios(uid, credential, store, hours)
            console.print(f"  待下载: {len(audios)} 个音频")

            async def _dl_audio(item):
                return await download_audio(item, uid, credential, store, base_dir)
            await _process_items(audios, _dl_audio, "音频")

        if "article" in selected_types:
            console.print("\n[bold]枚举专栏...[/bold]")
            articles = await enumerate_articles(uid, credential, store, hours)
            console.print(f"  待下载: {len(articles)} 个专栏")

            async def _dl_article(item):
                return await download_article(item, uid, credential, store, base_dir)
            await _process_items(articles, _dl_article, "专栏")

        if "dynamic" in selected_types:
            console.print("\n[bold]枚举动态...[/bold]")
            dynamics = await enumerate_dynamics(uid, credential, store, hours)
            console.print(f"  待下载: {len(dynamics)} 条动态")

            async def _dl_dynamic(item):
                return await download_dynamic(item, uid, credential, store, base_dir, selected_types)
            await _process_items(dynamics, _dl_dynamic, "动态")

        console.print("\n[bold]===== 下载报告 =====[/bold]")
        table = Table()
        table.add_column("指标", style="cyan")
        table.add_column("数量", style="green")
        table.add_row("总计", str(stats["total"]))
        table.add_row("成功", str(stats["done"]))
        table.add_row("失败", str(stats["failed"]))
        console.print(table)

    finally:
        await store.close()


@click.group()
def cli():
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
    asyncio.run(run_clone(uid, output, types, video_mode, interval, retry, hours))


if __name__ == "__main__":
    cli()
