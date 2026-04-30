"""
枚举模块

分页遍历B站API，收集用户的所有视频/音频/专栏/动态列表。
支持 --hours 时间过滤（提前终止翻页）和断点续传跳过（is_done）。
返回 DownloadItem 列表供下载模块逐项处理。
"""

import asyncio
import time
import traceback
from dataclasses import dataclass

from bilibili_api import user, Credential
from rich.console import Console

from config import DEFAULT_INTERVAL, DEFAULT_RETRY, BACKOFF_BASE
from store import DownloadStore

console = Console()


@dataclass
class DownloadItem:
    """待下载内容项。content_id 为BV号/AU号/cv号/动态ID，extra 存放类型特有数据。"""
    content_type: str
    content_id: str
    title: str
    extra: dict


async def _load_cached_items(content_type: str, store: DownloadStore) -> tuple[list[DownloadItem] | None, set[str]]:
    """
    从缓存加载指定类型的下载项，过滤掉已完成的项。
    同时返回缓存中所有内容的ID集合（用于新鲜度检查）。

    Args:
        content_type: 内容类型（video/audio/article/dynamic）
        store: 存储对象

    Returns:
        (未完成的DownloadItem列表, 所有缓存ID集合)
        如果无缓存返回 (None, set())
    """
    cached = await store.load_enum_cache(content_type)
    if cached is None:
        return None, set()

    all_ids = {d["content_id"] for d in cached}
    result = []
    for d in cached:
        if not await store.is_done(content_type, d["content_id"]):
            result.append(DownloadItem(
                content_type=d["content_type"], content_id=d["content_id"],
                title=d["title"], extra=d["extra"],
            ))
    console.print(f"  [dim](从缓存加载 {len(cached)} 项，{len(cached) - len(result)} 已完成)[/dim]")
    return result, all_ids


def _cutoff(hours: int | None) -> float | None:
    """将小时数转换为Unix时间戳截止点，hours为None时返回None（不过滤）。"""
    return time.time() - hours * 3600 if hours else None


async def _retry_api(fn, retries=DEFAULT_RETRY):
    """带指数退避的API请求重试，退避上限60秒。"""
    for attempt in range(retries):
        try:
            return await fn()
        except Exception as e:
            if attempt < retries - 1:
                wait = min(BACKOFF_BASE * (2 ** attempt), 60)
                # 记录完整的异常堆栈便于调试
                console.print(f"[yellow]API请求失败: {e}[/yellow]")
                console.print(f"[dim]{traceback.format_exc()}[/dim]")
                console.print(f"[yellow]{wait}s后重试...[/yellow]")
                await asyncio.sleep(wait)
            else:
                # 最后一次重试失败，记录完整堆栈并抛出异常
                console.print(f"[red]API请求最终失败: {e}[/red]")
                console.print(f"[dim]{traceback.format_exc()}[/dim]")
                raise


async def enumerate_videos(uid: int, credential: Credential, store: DownloadStore, hours: int | None = None, retries: int = DEFAULT_RETRY) -> list[DownloadItem]:
    """
    枚举用户视频列表。

    分页遍历 get_videos API，每页30条。遇到 created < cutoff 的跳过，
    整页都早于 cutoff 时终止翻页（提前终止优化）。已完成的项（is_done）跳过。
    无 --hours 时先查API第一页判断缓存是否新鲜，有新增内容才完整重新枚举。
    """
    cached_items = None
    cached_ids = set()
    if hours is None:
        cached_items, cached_ids = await _load_cached_items("video", store)

    cutoff = _cutoff(hours)
    u = user.User(uid=uid, credential=credential)

    # 先获取第一页做新鲜度检查
    resp = await _retry_api(lambda: u.get_videos(pn=1, ps=30), retries=retries)
    first_vlist = resp.get("list", {}).get("vlist", [])

    # 无 --hours 且缓存存在时，检查第一页是否有新增内容
    if hours is None and cached_items is not None:
        has_new = False
        for v in first_vlist:
            bvid = v.get("bvid", "")
            if bvid and bvid not in cached_ids:
                has_new = True
                break
        if not has_new:
            console.print("  [dim](缓存新鲜，无新增内容)[/dim]")
            return cached_items
        console.print("  [yellow]检测到新增内容，重新枚举...[/yellow]")

    # 完整枚举（含第一页已获取的数据）
    items = []
    pn = 1
    while True:
        if pn == 1:
            vlist = first_vlist
        else:
            resp = await _retry_api(lambda: u.get_videos(pn=pn, ps=30), retries=retries)
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


async def enumerate_audios(uid: int, credential: Credential, store: DownloadStore, hours: int | None = None, retries: int = DEFAULT_RETRY) -> list[DownloadItem]:
    """
    枚举用户音频区列表。

    兼容API返回格式差异：data可能为列表或字典。
    使用 pageCount 判断是否还有下一页。
    无 --hours 时先查API第一页判断缓存是否新鲜，有新增内容才完整重新枚举。
    """
    cached_items = None
    cached_ids = set()
    if hours is None:
        cached_items, cached_ids = await _load_cached_items("audio", store)

    cutoff = _cutoff(hours)
    u = user.User(uid=uid, credential=credential)

    # 先获取第一页做新鲜度检查
    resp = await _retry_api(lambda: u.get_audios(pn=1, ps=30), retries=retries)
    data = resp.get("data", resp)
    if isinstance(data, list):
        first_aulist = data
    elif isinstance(data, dict):
        first_aulist = data.get("list", [])
    else:
        first_aulist = []

    # 无 --hours 且缓存存在时，检查第一页是否有新增内容
    if hours is None and cached_items is not None:
        has_new = False
        for a in first_aulist:
            auid = str(a.get("id", ""))
            if auid and auid not in cached_ids:
                has_new = True
                break
        if not has_new:
            console.print("  [dim](缓存新鲜，无新增内容)[/dim]")
            return cached_items
        console.print("  [yellow]检测到新增内容，重新枚举...[/yellow]")

    # 完整枚举
    items = []
    pn = 1
    while True:
        if pn == 1:
            aulist = first_aulist
        else:
            resp = await _retry_api(lambda: u.get_audios(pn=pn, ps=30), retries=retries)
            data = resp.get("data", resp)
            if isinstance(data, list):
                aulist = data
            elif isinstance(data, dict):
                aulist = data.get("list", [])
            else:
                aulist = []
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


async def enumerate_articles(uid: int, credential: Credential, store: DownloadStore, hours: int | None = None, retries: int = DEFAULT_RETRY) -> list[DownloadItem]:
    """枚举用户专栏列表，使用 get_articles API 的 articles 字段。无 --hours 时先查API第一页判断缓存是否新鲜。"""
    cached_items = None
    cached_ids = set()
    if hours is None:
        cached_items, cached_ids = await _load_cached_items("article", store)

    cutoff = _cutoff(hours)
    u = user.User(uid=uid, credential=credential)

    # 先获取第一页做新鲜度检查
    resp = await _retry_api(lambda: u.get_articles(pn=1, ps=30), retries=retries)
    first_article_list = resp.get("articles", [])

    # 无 --hours 且缓存存在时，检查第一页是否有新增内容
    if hours is None and cached_items is not None:
        has_new = False
        for a in first_article_list:
            aid = str(a.get("id", ""))
            if aid and aid not in cached_ids:
                has_new = True
                break
        if not has_new:
            console.print("  [dim](缓存新鲜，无新增内容)[/dim]")
            return cached_items
        console.print("  [yellow]检测到新增内容，重新枚举...[/yellow]")

    # 完整枚举
    items = []
    pn = 1
    while True:
        if pn == 1:
            article_list = first_article_list
        else:
            resp = await _retry_api(lambda: u.get_articles(pn=pn, ps=30), retries=retries)
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


async def enumerate_dynamics(uid: int, credential: Credential, store: DownloadStore, hours: int | None = None, retries: int = DEFAULT_RETRY) -> list[DownloadItem]:
    """
    枚举用户动态列表。

    使用 offset 分页（非页码），has_more 判断是否继续。
    pub_ts 为发布时间戳（API返回可能为字符串，需强制转int）。
    raw 数据完整保存在 extra 中供下载模块使用。
    无 --hours 时先查API第一页判断缓存是否新鲜，有新增内容才完整重新枚举。
    """
    cached_items = None
    cached_ids = set()
    if hours is None:
        cached_items, cached_ids = await _load_cached_items("dynamic", store)

    cutoff = _cutoff(hours)
    u = user.User(uid=uid, credential=credential)

    # 先获取第一页做新鲜度检查
    resp = await _retry_api(lambda: u.get_dynamics_new(offset=""), retries=retries)
    first_dynamic_items = resp.get("items", [])
    first_has_more = resp.get("has_more", False)
    first_offset = resp.get("offset", "")

    # 无 --hours 且缓存存在时，检查第一页是否有新增内容
    if hours is None and cached_items is not None:
        has_new = False
        for item in first_dynamic_items:
            dynamic_id = str(item.get("id_str", ""))
            if dynamic_id and dynamic_id not in cached_ids:
                has_new = True
                break
        if not has_new:
            console.print("  [dim](缓存新鲜，无新增内容)[/dim]")
            return cached_items
        console.print("  [yellow]检测到新增内容，重新枚举...[/yellow]")

    # 完整枚举
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
            resp = await _retry_api(lambda: u.get_dynamics_new(offset=offset), retries=retries)
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
