"""
枚举模块

分页遍历B站API，收集用户的所有视频/音频/专栏/动态列表。

架构：
- EnumMode 枚举：USE_CACHE（直接用缓存）、INCREMENTAL（增量更新）、FULL（完整枚举）
- _decide_enum_mode() 决策函数：根据参数+缓存+第一页数据判断枚举模式
- _incremental_enum_*() 增量枚举函数：只翻页到遇到缓存已有内容，合并新+旧
- _full_enum_*() 完整枚举函数：从头翻页遍历全部
- enumerate_*() 纯编排函数：加载缓存 → 获取第一页 → 决策模式 → 调度执行
"""

import asyncio
import time
import traceback
from dataclasses import dataclass
from enum import Enum, auto

from bilibili_api import user, Credential
from rich.console import Console

from config import DEFAULT_INTERVAL, DEFAULT_RETRY, BACKOFF_BASE
from store import DownloadStore

console = Console()


class EnumMode(Enum):
    USE_CACHE = auto()
    INCREMENTAL = auto()
    FULL = auto()


@dataclass
class DownloadItem:
    content_type: str
    content_id: str
    title: str
    extra: dict


async def _load_cached_items(content_type: str, store: DownloadStore) -> tuple[list[DownloadItem] | None, set[str], list[DownloadItem]]:
    """
    从缓存加载指定类型的下载项。

    返回三个值：
    1. 未完成的 DownloadItem 列表（用于下载）
    2. 所有缓存内容的 ID 集合（用于增量更新时判断是否在缓存中）
    3. 完整的缓存 DownloadItem 列表（用于增量更新时合并）

    Args:
        content_type: 内容类型（video/audio/article/dynamic）
        store: 存储对象

    Returns:
        (未完成的项列表, 所有缓存ID集合, 完整缓存列表)
        如果无缓存返回 (None, set(), [])
    """
    cached, is_expired, age_hours = await store.load_enum_cache(content_type)
    if cached is None:
        return None, set(), []

    if is_expired:
        console.print(
            f"  [yellow]缓存已过期 {age_hours} 小时，"
            f"建议运行: uv run main.py update-cache {store.uid} --types {content_type}[/yellow]"
        )

    all_ids = {d["content_id"] for d in cached}

    # 未完成的项（用于返回给下载器）
    pending = []
    for d in cached:
        if not await store.is_done(content_type, d["content_id"]):
            pending.append(DownloadItem(
                content_type=d["content_type"], content_id=d["content_id"],
                title=d["title"], extra=d["extra"],
            ))

    # 完整的缓存数据（用于增量更新时合并）
    full_cached = [
        DownloadItem(
            content_type=d["content_type"], content_id=d["content_id"],
            title=d["title"], extra=d["extra"],
        ) for d in cached
    ]

    console.print(f"  [dim](从缓存加载 {len(cached)} 项，{len(cached) - len(pending)} 已完成)[/dim]")
    return pending, all_ids, full_cached


def _cutoff(hours: int | None) -> float | None:
    """
    将小时数转换为Unix时间戳截止点。

    Args:
        hours: 小时数，None表示不过滤

    Returns:
        Unix时间戳，或None
    """
    if hours:
        # 当前时间 - hours * 3600秒 = 截止时间戳
        return time.time() - hours * 3600
    return None


async def _retry_api(fn, retries=DEFAULT_RETRY):
    for attempt in range(retries):
        try:
            return await fn()
        except Exception as e:
            if attempt < retries - 1:
                wait = min(BACKOFF_BASE * (2 ** attempt), 60)
                console.print(f"[yellow]API请求失败: {e}[/yellow]")
                console.print(f"[dim]{traceback.format_exc()}[/dim]")
                console.print(f"[yellow]{wait}s后重试...[/yellow]")
                await asyncio.sleep(wait)
            else:
                console.print(f"[red]API请求最终失败: {e}[/red]")
                console.print(f"[dim]{traceback.format_exc()}[/dim]")
                raise


def _decide_enum_mode(
    hours: int | None,
    force: bool,
    cached_items: list[DownloadItem] | None,
    first_page_raw: list[dict],
    id_fn,
    cached_ids: set[str],
) -> EnumMode:
    if hours is not None or force:
        return EnumMode.FULL
    if cached_items is None:
        return EnumMode.FULL
    for item in first_page_raw:
        item_id = id_fn(item)
        if item_id and item_id not in cached_ids:
            return EnumMode.INCREMENTAL
    return EnumMode.USE_CACHE


def _merge_items(new_items: list[DownloadItem], full_cached: list[DownloadItem]) -> list[DownloadItem]:
    all_items = new_items + full_cached
    seen = set()
    unique = []
    for it in all_items:
        if it.content_id not in seen:
            seen.add(it.content_id)
            unique.append(it)
    return unique


def _parse_audio_list(resp) -> list:
    data = resp.get("data", resp)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("list", [])
    return []


# ============================================================
# Video
# ============================================================

async def _incremental_enum_videos(u, first_vlist, cached_ids, full_cached, cached_items, store, retries):
    console.print("  [yellow]检测到新增内容，增量更新...[/yellow]")
    new_items = []
    pn = 1
    while True:
        if pn == 1:
            vlist = first_vlist
        else:
            resp = await _retry_api(lambda _pn=pn: u.get_videos(pn=_pn, ps=30), retries=retries)
            vlist = resp.get("list", {}).get("vlist", [])

        if not vlist:
            break

        page_all_cached = True
        for v in vlist:
            bvid = v.get("bvid", "")
            title = v.get("title", "")
            if not bvid:
                continue
            if bvid in cached_ids:
                continue
            page_all_cached = False
            if await store.is_done("video", bvid):
                continue
            new_items.append(DownloadItem(
                content_type="video",
                content_id=bvid,
                title=title,
                extra={"aid": v.get("aid"), "cid": v.get("cid")},
            ))

        if page_all_cached:
            break

        pn += 1
        await asyncio.sleep(DEFAULT_INTERVAL)

    unique_items = _merge_items(new_items, full_cached)
    await store.save_enum_cache("video", unique_items)
    console.print(f"  [dim]新增 {len(new_items)} 项，缓存共 {len(unique_items)} 项[/dim]")
    return new_items + cached_items


async def _full_enum_videos(u, first_vlist, cutoff, store, retries, hours):
    items = []
    pn = 1
    while True:
        if pn == 1:
            vlist = first_vlist
        else:
            resp = await _retry_api(lambda _pn=pn: u.get_videos(pn=_pn, ps=30), retries=retries)
            vlist = resp.get("list", {}).get("vlist", [])

        if not vlist:
            break

        page_all_old = True
        for v in vlist:
            bvid = v.get("bvid", "")
            title = v.get("title", "")
            if not bvid:
                continue

            created = v.get("created", 0)
            if cutoff and created < cutoff:
                continue

            page_all_old = False

            if await store.is_done("video", bvid):
                continue

            items.append(DownloadItem(
                content_type="video",
                content_id=bvid,
                title=title,
                extra={"aid": v.get("aid"), "cid": v.get("cid")},
            ))

        if cutoff and page_all_old:
            break

        pn += 1
        await asyncio.sleep(DEFAULT_INTERVAL)

    if hours is None:
        await store.save_enum_cache("video", items)

    return items


async def enumerate_videos(uid: int, credential: Credential, store: DownloadStore, hours: int | None = None, retries: int = DEFAULT_RETRY, force: bool = False) -> list[DownloadItem]:
    cached_items = None
    cached_ids = set()
    full_cached = []
    if hours is None and not force:
        cached_items, cached_ids, full_cached = await _load_cached_items("video", store)

    cutoff = _cutoff(hours)
    u = user.User(uid=uid, credential=credential)

    resp = await _retry_api(lambda: u.get_videos(pn=1, ps=30), retries=retries)
    first_vlist = resp.get("list", {}).get("vlist", [])

    mode = _decide_enum_mode(hours, force, cached_items, first_vlist, lambda v: v.get("bvid", ""), cached_ids)

    if mode == EnumMode.USE_CACHE:
        console.print("  [dim](缓存新鲜，无新增内容)[/dim]")
        return cached_items
    if mode == EnumMode.INCREMENTAL:
        return await _incremental_enum_videos(u, first_vlist, cached_ids, full_cached, cached_items, store, retries)
    return await _full_enum_videos(u, first_vlist, cutoff, store, retries, hours)


# ============================================================
# Audio
# ============================================================

async def _incremental_enum_audios(u, first_aulist, first_resp, cached_ids, full_cached, cached_items, store, retries):
    console.print("  [yellow]检测到新增内容，增量更新...[/yellow]")
    new_items = []
    pn = 1
    resp = first_resp
    while True:
        if pn == 1:
            aulist = first_aulist
        else:
            resp = await _retry_api(lambda _pn=pn: u.get_audios(pn=_pn, ps=30), retries=retries)
            aulist = _parse_audio_list(resp)

        if not aulist:
            break

        page_all_cached = True
        for a in aulist:
            auid = str(a.get("id", ""))
            title = a.get("title", "")
            if not auid:
                continue
            if auid in cached_ids:
                continue
            page_all_cached = False
            if await store.is_done("audio", auid):
                continue
            new_items.append(DownloadItem(
                content_type="audio",
                content_id=auid,
                title=title,
                extra={},
            ))

        if page_all_cached:
            break

        total_pages = resp.get("pageCount", 1)
        if pn >= total_pages:
            break

        pn += 1
        await asyncio.sleep(DEFAULT_INTERVAL)

    unique_items = _merge_items(new_items, full_cached)
    await store.save_enum_cache("audio", unique_items)
    console.print(f"  [dim]新增 {len(new_items)} 项，缓存共 {len(unique_items)} 项[/dim]")
    return new_items + cached_items


async def _full_enum_audios(u, first_aulist, first_resp, cutoff, store, retries, hours):
    items = []
    pn = 1
    resp = first_resp
    while True:
        if pn == 1:
            aulist = first_aulist
        else:
            resp = await _retry_api(lambda _pn=pn: u.get_audios(pn=_pn, ps=30), retries=retries)
            aulist = _parse_audio_list(resp)

        if not aulist:
            break

        page_all_old = True
        for a in aulist:
            auid = str(a.get("id", ""))
            title = a.get("title", "")
            if not auid:
                continue

            ctime = a.get("ctime", a.get("passtime", 0))
            if cutoff and ctime < cutoff:
                continue

            page_all_old = False

            if await store.is_done("audio", auid):
                continue

            items.append(DownloadItem(
                content_type="audio",
                content_id=auid,
                title=title,
                extra={},
            ))

        if cutoff and page_all_old:
            break

        total_pages = resp.get("pageCount", 1)
        if pn >= total_pages:
            break

        pn += 1
        await asyncio.sleep(DEFAULT_INTERVAL)

    if hours is None:
        await store.save_enum_cache("audio", items)

    return items


async def enumerate_audios(uid: int, credential: Credential, store: DownloadStore, hours: int | None = None, retries: int = DEFAULT_RETRY, force: bool = False) -> list[DownloadItem]:
    cached_items = None
    cached_ids = set()
    full_cached = []
    if hours is None and not force:
        cached_items, cached_ids, full_cached = await _load_cached_items("audio", store)

    cutoff = _cutoff(hours)
    u = user.User(uid=uid, credential=credential)

    resp = await _retry_api(lambda: u.get_audios(pn=1, ps=30), retries=retries)
    first_aulist = _parse_audio_list(resp)

    mode = _decide_enum_mode(hours, force, cached_items, first_aulist, lambda a: str(a.get("id", "")), cached_ids)

    if mode == EnumMode.USE_CACHE:
        console.print("  [dim](缓存新鲜，无新增内容)[/dim]")
        return cached_items
    if mode == EnumMode.INCREMENTAL:
        return await _incremental_enum_audios(u, first_aulist, resp, cached_ids, full_cached, cached_items, store, retries)
    return await _full_enum_audios(u, first_aulist, resp, cutoff, store, retries, hours)


# ============================================================
# Article
# ============================================================

async def _incremental_enum_articles(u, first_article_list, cached_ids, full_cached, cached_items, store, retries):
    console.print("  [yellow]检测到新增内容，增量更新...[/yellow]")
    new_items = []
    pn = 1
    while True:
        if pn == 1:
            article_list = first_article_list
        else:
            resp = await _retry_api(lambda _pn=pn: u.get_articles(pn=_pn, ps=30), retries=retries)
            article_list = resp.get("articles", [])

        if not article_list:
            break

        page_all_cached = True
        for a in article_list:
            aid = str(a.get("id", ""))
            title = a.get("title", "")
            if not aid:
                continue
            if aid in cached_ids:
                continue
            page_all_cached = False
            if await store.is_done("article", aid):
                continue
            new_items.append(DownloadItem(
                content_type="article",
                content_id=aid,
                title=title,
                extra={},
            ))

        if page_all_cached:
            break

        pn += 1
        await asyncio.sleep(DEFAULT_INTERVAL)

    unique_items = _merge_items(new_items, full_cached)
    await store.save_enum_cache("article", unique_items)
    console.print(f"  [dim]新增 {len(new_items)} 项，缓存共 {len(unique_items)} 项[/dim]")
    return new_items + cached_items


async def _full_enum_articles(u, first_article_list, cutoff, store, retries, hours):
    items = []
    pn = 1
    while True:
        if pn == 1:
            article_list = first_article_list
        else:
            resp = await _retry_api(lambda _pn=pn: u.get_articles(pn=_pn, ps=30), retries=retries)
            article_list = resp.get("articles", [])

        if not article_list:
            break

        page_all_old = True
        for a in article_list:
            aid = str(a.get("id", ""))
            title = a.get("title", "")
            if not aid:
                continue

            ctime = a.get("ctime", 0)
            if cutoff and ctime < cutoff:
                continue

            page_all_old = False

            if await store.is_done("article", aid):
                continue

            items.append(DownloadItem(
                content_type="article",
                content_id=aid,
                title=title,
                extra={},
            ))

        if cutoff and page_all_old:
            break

        pn += 1
        await asyncio.sleep(DEFAULT_INTERVAL)

    if hours is None:
        await store.save_enum_cache("article", items)

    return items


async def enumerate_articles(uid: int, credential: Credential, store: DownloadStore, hours: int | None = None, retries: int = DEFAULT_RETRY, force: bool = False) -> list[DownloadItem]:
    cached_items = None
    cached_ids = set()
    full_cached = []
    if hours is None and not force:
        cached_items, cached_ids, full_cached = await _load_cached_items("article", store)

    cutoff = _cutoff(hours)
    u = user.User(uid=uid, credential=credential)

    resp = await _retry_api(lambda: u.get_articles(pn=1, ps=30), retries=retries)
    first_article_list = resp.get("articles", [])

    mode = _decide_enum_mode(hours, force, cached_items, first_article_list, lambda a: str(a.get("id", "")), cached_ids)

    if mode == EnumMode.USE_CACHE:
        console.print("  [dim](缓存新鲜，无新增内容)[/dim]")
        return cached_items
    if mode == EnumMode.INCREMENTAL:
        return await _incremental_enum_articles(u, first_article_list, cached_ids, full_cached, cached_items, store, retries)
    return await _full_enum_articles(u, first_article_list, cutoff, store, retries, hours)


# ============================================================
# Dynamic
# ============================================================

async def _incremental_enum_dynamics(u, first_dynamic_items, first_has_more, first_offset, cached_ids, full_cached, cached_items, store, retries):
    console.print("  [yellow]检测到新增内容，增量更新...[/yellow]")
    new_items = []
    offset = ""
    is_first = True
    while True:
        if is_first:
            dynamic_items = first_dynamic_items
            has_more = first_has_more
            offset = first_offset
            is_first = False
        else:
            resp = await _retry_api(lambda _off=offset: u.get_dynamics_new(offset=_off), retries=retries)
            dynamic_items = resp.get("items", [])
            has_more = resp.get("has_more", False)

        if not dynamic_items:
            break

        page_all_cached = True
        for item in dynamic_items:
            dynamic_id = str(item.get("id_str", ""))
            dtype = item.get("type", "")
            if not dynamic_id:
                continue
            if dynamic_id in cached_ids:
                continue
            page_all_cached = False
            if await store.is_done("dynamic", dynamic_id):
                continue
            new_items.append(DownloadItem(
                content_type="dynamic",
                content_id=dynamic_id,
                title=f"dynamic_{dynamic_id}",
                extra={"dtype": dtype, "raw": item},
            ))

        if page_all_cached:
            break

        offset = resp.get("offset", "")
        if not offset or not has_more:
            break

        await asyncio.sleep(DEFAULT_INTERVAL)

    unique_items = _merge_items(new_items, full_cached)
    await store.save_enum_cache("dynamic", unique_items)
    console.print(f"  [dim]新增 {len(new_items)} 项，缓存共 {len(unique_items)} 项[/dim]")
    return new_items + cached_items


async def _full_enum_dynamics(u, first_dynamic_items, first_has_more, first_offset, cutoff, store, retries, hours):
    items = []
    offset = ""
    is_first = True
    while True:
        if is_first:
            dynamic_items = first_dynamic_items
            has_more = first_has_more
            offset = first_offset
            is_first = False
        else:
            resp = await _retry_api(lambda _off=offset: u.get_dynamics_new(offset=_off), retries=retries)
            dynamic_items = resp.get("items", [])
            has_more = resp.get("has_more", False)

        if not dynamic_items:
            break

        page_all_old = True
        for item in dynamic_items:
            dynamic_id = str(item.get("id_str", ""))
            dtype = item.get("type", "")
            if not dynamic_id:
                continue

            pub_ts = item.get("modules", {}).get("module_author", {}).get("pub_ts", 0)
            if pub_ts:
                pub_ts = int(pub_ts) if isinstance(pub_ts, str) else pub_ts

            if cutoff and pub_ts < cutoff:
                continue

            page_all_old = False

            if await store.is_done("dynamic", dynamic_id):
                continue

            items.append(DownloadItem(
                content_type="dynamic",
                content_id=dynamic_id,
                title=f"dynamic_{dynamic_id}",
                extra={"dtype": dtype, "raw": item},
            ))

        if cutoff and page_all_old:
            break

        offset = resp.get("offset", "")
        if not offset or not has_more:
            break

        await asyncio.sleep(DEFAULT_INTERVAL)

    if hours is None:
        await store.save_enum_cache("dynamic", items)

    return items


async def enumerate_dynamics(uid: int, credential: Credential, store: DownloadStore, hours: int | None = None, retries: int = DEFAULT_RETRY, force: bool = False) -> list[DownloadItem]:
    cached_items = None
    cached_ids = set()
    full_cached = []
    if hours is None and not force:
        cached_items, cached_ids, full_cached = await _load_cached_items("dynamic", store)

    cutoff = _cutoff(hours)
    u = user.User(uid=uid, credential=credential)

    resp = await _retry_api(lambda: u.get_dynamics_new(offset=""), retries=retries)
    first_dynamic_items = resp.get("items", [])
    first_has_more = resp.get("has_more", False)
    first_offset = resp.get("offset", "")

    mode = _decide_enum_mode(hours, force, cached_items, first_dynamic_items, lambda item: str(item.get("id_str", "")), cached_ids)

    if mode == EnumMode.USE_CACHE:
        console.print("  [dim](缓存新鲜，无新增内容)[/dim]")
        return cached_items
    if mode == EnumMode.INCREMENTAL:
        return await _incremental_enum_dynamics(u, first_dynamic_items, first_has_more, first_offset, cached_ids, full_cached, cached_items, store, retries)
    return await _full_enum_dynamics(u, first_dynamic_items, first_has_more, first_offset, cutoff, store, retries, hours)
