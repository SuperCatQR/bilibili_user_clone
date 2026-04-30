"""
枚举模块

分页遍历B站API，收集用户的所有视频/音频/专栏/动态列表。

核心功能：
- 分页遍历：自动翻页获取所有内容
- 时间过滤：--hours 参数在枚举阶段生效，遇到整页都早于截止时间时提前终止
- 断点续传跳过：已完成的项（is_done）在枚举阶段被跳过
- 枚举缓存：缓存枚举结果，避免重复调用API
- 缓存新鲜度检查：先查API第一页，有新增内容才完整重新枚举
- 重试机制：API请求失败时指数退避重试

枚举缓存策略：
- 无 --hours 时：先查缓存，再检查API第一页是否有新增内容
  - 缓存新鲜（无新增）：直接使用缓存
  - 缓存不新鲜（有新增）：完整重新枚举并更新缓存
- 有 --hours 时：不使用缓存（因为时间范围是动态的）

返回值：DownloadItem 列表，供下载模块逐项处理
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
    """
    待下载内容项。

    content_id 为BV号/AU号/cv号/动态ID，是内容的唯一标识。
    extra 存放类型特有数据，如视频的aid/cid、动态的原始数据等。

    使用dataclass而非普通类，因为：
    1. 自动生成__init__、__repr__等方法
    2. 语义清晰，表示这是一个纯数据容器
    3. 支持类型注解
    """
    content_type: str   # 内容类型：video/audio/article/dynamic
    content_id: str     # 内容ID：BV号/AU号/cv号/动态ID
    title: str          # 内容标题
    extra: dict         # 类型特有数据（如视频的aid/cid、动态的原始数据）


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
    # 尝试从缓存加载
    cached = await store.load_enum_cache(content_type)
    if cached is None:
        return None, set()

    # 提取所有缓存ID（用于新鲜度检查）
    all_ids = {d["content_id"] for d in cached}

    # 过滤掉已完成的项
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
    """
    带指数退避的API请求重试。

    重试策略：
    - 指数退避：等待时间 = BACKOFF_BASE * 2^attempt
    - 退避上限：60秒
    - 最后一次重试失败时记录完整堆栈并抛出异常

    Args:
        fn: 异步函数（无参数）
        retries: 最大重试次数

    Returns:
        fn()的返回值

    Raises:
        最后一次重试失败时抛出原始异常
    """
    for attempt in range(retries):
        try:
            return await fn()
        except Exception as e:
            if attempt < retries - 1:
                # 计算退避时间
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


async def enumerate_videos(uid: int, credential: Credential, store: DownloadStore, hours: int | None = None, retries: int = DEFAULT_RETRY, force: bool = False) -> list[DownloadItem]:
    """
    枚举用户视频列表。

    分页遍历 get_videos API，每页30条。

    枚举策略：
    1. 无 --hours 且非 force 且缓存存在时：
       - 先获取API第一页
       - 检查第一页是否有新增内容（不在缓存ID集合中）
       - 无新增：直接返回缓存（跳过完整枚举）
       - 有新增：完整重新枚举
    2. 有 --hours 或 force=True 时：
       - 直接完整枚举（不使用缓存）
       - 遇到 created < cutoff 的跳过
       - 整页都早于 cutoff 时终止翻页（提前终止优化）

    Args:
        uid: 用户UID
        credential: 认证凭据
        store: 下载状态存储
        hours: 只枚举指定小时内发布的内容（None表示不限制）
        retries: API重试次数
        force: 强制重新枚举，忽略缓存

    Returns:
        DownloadItem列表（不含已完成的项）
    """
    # 加载缓存（仅无--hours且非force时）
    cached_items = None
    cached_ids = set()
    if hours is None and not force:
        cached_items, cached_ids = await _load_cached_items("video", store)

    # 计算时间截止点
    cutoff = _cutoff(hours)

    # 创建User对象
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
            # 缓存新鲜，无新增内容，直接返回缓存
            console.print("  [dim](缓存新鲜，无新增内容)[/dim]")
            return cached_items
        # 检测到新增内容，需要完整重新枚举
        console.print("  [yellow]检测到新增内容，重新枚举...[/yellow]")

    # 完整枚举（含第一页已获取的数据）
    items = []
    pn = 1
    while True:
        # 第一页已获取，直接使用；后续页需要重新请求
        if pn == 1:
            vlist = first_vlist
        else:
            resp = await _retry_api(lambda: u.get_videos(pn=pn, ps=30), retries=retries)
            vlist = resp.get("list", {}).get("vlist", [])

        # 空页表示已遍历完所有内容
        if not vlist:
            break

        # 标记当前页是否所有内容都早于截止时间
        page_all_old = True

        for v in vlist:
            bvid = v.get("bvid", "")
            title = v.get("title", "")

            # 跳过无BV号的无效记录
            if not bvid:
                continue

            # 时间过滤：跳过早于截止时间的内容
            created = v.get("created", 0)
            if cutoff and created < cutoff:
                continue

            # 当前页有新于截止时间的内容
            page_all_old = False

            # 跳过已完成的内容
            if await store.is_done("video", bvid):
                continue

            # 添加到待下载列表
            items.append(DownloadItem(
                content_type="video",
                content_id=bvid,
                title=title,
                extra={"aid": v.get("aid"), "cid": v.get("cid")},
            ))

        # 提前终止优化：整页都早于截止时间时，无需继续翻页
        if cutoff and page_all_old:
            break

        pn += 1
        # 翻页间隔，避免触发限速
        await asyncio.sleep(DEFAULT_INTERVAL)

    # 无 --hours 时更新缓存
    if hours is None:
        await store.save_enum_cache("video", items)

    return items


async def enumerate_audios(uid: int, credential: Credential, store: DownloadStore, hours: int | None = None, retries: int = DEFAULT_RETRY, force: bool = False) -> list[DownloadItem]:
    """
    枚举用户音频区列表。

    兼容API返回格式差异：data可能为列表或字典。
    使用 pageCount 判断是否还有下一页。

    Args:
        uid: 用户UID
        credential: 认证凭据
        store: 下载状态存储
        hours: 只枚举指定小时内发布的内容
        retries: API重试次数
        force: 强制重新枚举，忽略缓存

    Returns:
        DownloadItem列表
    """
    # 加载缓存
    cached_items = None
    cached_ids = set()
    if hours is None and not force:
        cached_items, cached_ids = await _load_cached_items("audio", store)

    cutoff = _cutoff(hours)
    u = user.User(uid=uid, credential=credential)

    # 先获取第一页做新鲜度检查
    resp = await _retry_api(lambda: u.get_audios(pn=1, ps=30), retries=retries)

    # 兼容API返回格式差异
    # 有些版本返回 {"data": [...]}，有些返回 {"data": {"list": [...]}}
    data = resp.get("data", resp)
    if isinstance(data, list):
        first_aulist = data
    elif isinstance(data, dict):
        first_aulist = data.get("list", [])
    else:
        first_aulist = []

    # 新鲜度检查
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

            # 兼容不同API版本的时间字段
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

        # 使用 pageCount 判断是否还有下一页
        total_pages = resp.get("pageCount", 1)
        if pn >= total_pages:
            break

        pn += 1
        await asyncio.sleep(DEFAULT_INTERVAL)

    if hours is None:
        await store.save_enum_cache("audio", items)

    return items


async def enumerate_articles(uid: int, credential: Credential, store: DownloadStore, hours: int | None = None, retries: int = DEFAULT_RETRY, force: bool = False) -> list[DownloadItem]:
    """枚举用户专栏列表，使用 get_articles API 的 articles 字段。无 --hours 且非 force 时先查API第一页判断缓存是否新鲜。"""
    cached_items = None
    cached_ids = set()
    if hours is None and not force:
        cached_items, cached_ids = await _load_cached_items("article", store)

    cutoff = _cutoff(hours)
    u = user.User(uid=uid, credential=credential)

    # 先获取第一页做新鲜度检查
    resp = await _retry_api(lambda: u.get_articles(pn=1, ps=30), retries=retries)
    first_article_list = resp.get("articles", [])

    # 新鲜度检查
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


async def enumerate_dynamics(uid: int, credential: Credential, store: DownloadStore, hours: int | None = None, retries: int = DEFAULT_RETRY, force: bool = False) -> list[DownloadItem]:
    """
    枚举用户动态列表。

    使用 offset 分页（非页码），has_more 判断是否继续。
    pub_ts 为发布时间戳（API返回可能为字符串，需强制转int）。
    raw 数据完整保存在 extra 中供下载模块使用。
    无 --hours 且非 force 时先查API第一页判断缓存是否新鲜，有新增内容才完整重新枚举。
    """
    cached_items = None
    cached_ids = set()
    if hours is None and not force:
        cached_items, cached_ids = await _load_cached_items("dynamic", store)

    cutoff = _cutoff(hours)
    u = user.User(uid=uid, credential=credential)

    # 先获取第一页做新鲜度检查
    resp = await _retry_api(lambda: u.get_dynamics_new(offset=""), retries=retries)
    first_dynamic_items = resp.get("items", [])
    first_has_more = resp.get("has_more", False)
    first_offset = resp.get("offset", "")

    # 新鲜度检查
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
            # 第一页已获取，直接使用
            dynamic_items = first_dynamic_items
            has_more = first_has_more
            offset = first_offset
            is_first = False
        else:
            # 使用offset分页（非页码）
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

            # pub_ts可能为字符串，需强制转int
            pub_ts = item.get("modules", {}).get("module_author", {}).get("pub_ts", 0)
            if pub_ts:
                pub_ts = int(pub_ts) if isinstance(pub_ts, str) else pub_ts

            if cutoff and pub_ts < cutoff:
                continue

            page_all_old = False

            if await store.is_done("dynamic", dynamic_id):
                continue

            # raw 数据完整保存在 extra 中供下载模块使用
            items.append(DownloadItem(
                content_type="dynamic",
                content_id=dynamic_id,
                title=f"dynamic_{dynamic_id}",
                extra={"dtype": dtype, "raw": item},
            ))

        if cutoff and page_all_old:
            break

        # offset分页：使用API返回的offset值
        offset = resp.get("offset", "")
        if not offset or not has_more:
            break

        await asyncio.sleep(DEFAULT_INTERVAL)

    if hours is None:
        await store.save_enum_cache("dynamic", items)

    return items
