"""
枚举模块

分页遍历B站API，收集用户的所有视频/音频/专栏/动态列表。
支持 --hours 时间过滤（提前终止翻页）和断点续传跳过（is_done）。
返回 DownloadItem 列表供下载模块逐项处理。
"""

import asyncio
import time
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
                console.print(f"[yellow]API请求失败: {e}, {wait}s后重试...[/yellow]")
                await asyncio.sleep(wait)
            else:
                raise


async def enumerate_videos(uid: int, credential: Credential, store: DownloadStore, hours: int | None = None) -> list[DownloadItem]:
    """
    枚举用户视频列表。
    
    分页遍历 get_videos API，每页30条。遇到 created < cutoff 的跳过，
    整页都早于 cutoff 时终止翻页（提前终止优化）。已完成的项（is_done）跳过。
    """
    items = []
    cutoff = _cutoff(hours)
    u = user.User(uid=uid, credential=credential)
    pn = 1
    while True:
        resp = await _retry_api(lambda: u.get_videos(pn=pn, ps=30))
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
    return items


async def enumerate_audios(uid: int, credential: Credential, store: DownloadStore, hours: int | None = None) -> list[DownloadItem]:
    """
    枚举用户音频区列表。
    
    兼容API返回格式差异：data可能为列表或字典。
    使用 pageCount 判断是否还有下一页。
    """
    items = []
    cutoff = _cutoff(hours)
    u = user.User(uid=uid, credential=credential)
    pn = 1
    while True:
        resp = await _retry_api(lambda: u.get_audios(pn=pn, ps=30))
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
    return items


async def enumerate_articles(uid: int, credential: Credential, store: DownloadStore, hours: int | None = None) -> list[DownloadItem]:
    """枚举用户专栏列表，使用 get_articles API 的 articles 字段。"""
    items = []
    cutoff = _cutoff(hours)
    u = user.User(uid=uid, credential=credential)
    pn = 1
    while True:
        resp = await _retry_api(lambda: u.get_articles(pn=pn, ps=30))
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
    return items


async def enumerate_dynamics(uid: int, credential: Credential, store: DownloadStore, hours: int | None = None) -> list[DownloadItem]:
    """
    枚举用户动态列表。
    
    使用 offset 分页（非页码），has_more 判断是否继续。
    pub_ts 为发布时间戳（API返回可能为字符串，需强制转int）。
    raw 数据完整保存在 extra 中供下载模块使用。
    """
    items = []
    cutoff = _cutoff(hours)
    u = user.User(uid=uid, credential=credential)
    offset = ""
    while True:
        resp = await _retry_api(lambda: u.get_dynamics_new(offset=offset))
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
    return items
