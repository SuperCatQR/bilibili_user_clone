"""
动态下载模块

保存动态的完整原始JSON + 摘要info.json + 提取图片。

支持的动态类型：
- DYNAMIC_TYPE_DRAW: 图文动态（下载所有图片）
- DYNAMIC_TYPE_AV: 视频动态（下载封面图）
- DYNAMIC_TYPE_LIVE_RCMD: 直播推荐（下载直播间图片）
- DYNAMIC_TYPE_FORWARD: 转发动态（下载原始动态的图片）
- DYNAMIC_TYPE_WORD: 纯文字动态（无图片）
- DYNAMIC_TYPE_ARTICLE: 专栏动态（无图片）
- DYNAMIC_TYPE_MUSIC: 音频动态（无图片）

嵌入式内容识别：
- 从动态中提取关联的其他内容ID
- DYNAMIC_TYPE_AV → bvid/aid
- DYNAMIC_TYPE_ARTICLE → cvid
- DYNAMIC_TYPE_MUSIC → auid
- 记录在info.json的embedded字段中，便于跨类型数据关联

图片提取优先级：
1. major.archive.cover（视频封面）
2. desc.text 中的图片URL（正文中的图片链接）
3. 整个JSON中正则匹配的图片URL（兜底，最多10张）
"""

import json
import re
from pathlib import Path

from bilibili_api import Credential
from rich.console import Console

from downloader import download_file
from store import DownloadStore
from utils import sanitize_filename, check_signature
from config import DEFAULT_RETRY

console = Console()


def _get_dynamic_type_str(item: dict) -> str:
    """
    提取动态类型字符串。

    Args:
        item: 动态对象字典

    Returns:
        类型字符串，如 DYNAMIC_TYPE_AV、DYNAMIC_TYPE_DRAW 等
    """
    if not item:
        return "UNKNOWN"
    return item.get("type", "UNKNOWN")


def _safe_get_nested(data: dict, *keys, default=None):
    """
    安全地获取嵌套字典中的值，避免NoneType错误。

    使用链式.get()访问嵌套字典，任何中间键不存在或为None时返回默认值。

    Args:
        data: 字典对象
        *keys: 嵌套的键路径
        default: 默认值

    Returns:
        嵌套键对应的值，如果任何中间键不存在或为None则返回default

    Example:
        _safe_get_nested(item, "modules", "module_dynamic", "major", default={})
        等价于: item.get("modules", {}).get("module_dynamic", {}).get("major", {})
        但更安全，因为任何中间值为None时不会抛出AttributeError
    """
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current if current is not None else default


def _extract_embedded_ids(item: dict) -> dict:
    """
    从动态中提取关联的其他内容ID。

    提取逻辑：
    - DYNAMIC_TYPE_AV → bvid/aid（优先从major.archive提取，回退到basic.rid_str）
    - DYNAMIC_TYPE_ARTICLE → cvid
    - DYNAMIC_TYPE_MUSIC → auid

    Args:
        item: 动态对象字典

    Returns:
        包含关联ID的字典，如 {"bvid": "BV1xx411x7xx"} 或 {"cvid": "12345"}
    """
    result = {}
    if not item:
        return result

    type_str = _get_dynamic_type_str(item)

    # basic.rid_str 是关联资源的ID（回退值）
    basic = item.get("basic") or {}
    rid_str = basic.get("rid_str", "") if isinstance(basic, dict) else ""

    if type_str == "DYNAMIC_TYPE_AV":
        # 视频动态：提取bvid或aid
        major = _safe_get_nested(item, "modules", "module_dynamic", "major", default={})
        archive = major.get("archive", {}) if isinstance(major, dict) else {}
        bvid = archive.get("bvid", "") if isinstance(archive, dict) else ""
        aid = archive.get("aid", "") if isinstance(archive, dict) else ""

        if bvid:
            result["bvid"] = bvid
        elif aid:
            result["aid"] = str(aid)
        if rid_str:
            result.setdefault("aid", rid_str)

    elif type_str == "DYNAMIC_TYPE_ARTICLE":
        # 专栏动态：提取cvid
        major = _safe_get_nested(item, "modules", "module_dynamic", "major", default={})
        article = major.get("article", {}) if isinstance(major, dict) else {}
        cvid = article.get("id", "") if isinstance(article, dict) else ""

        if cvid:
            result["cvid"] = str(cvid)
        elif rid_str:
            result["cvid"] = rid_str

    elif type_str == "DYNAMIC_TYPE_MUSIC":
        # 音频动态：提取auid
        major = _safe_get_nested(item, "modules", "module_dynamic", "major", default={})
        music = major.get("music", {}) if isinstance(major, dict) else {}
        auid = music.get("id", "") if isinstance(music, dict) else ""

        if auid:
            result["auid"] = str(auid)
        elif rid_str:
            result["auid"] = rid_str

    return result


async def _download_images_from_item(item: dict, output_dir: Path, credential: Credential, retries: int = DEFAULT_RETRY) -> list[str]:
    """
    从动态对象中提取并下载图片。

    图片来源优先级：
    1. major.archive.cover（视频封面）
    2. desc.text 中的图片URL（正文中的图片链接）
    3. 整个JSON中正则匹配的图片URL（兜底，最多10张）

    Args:
        item: 动态对象字典
        output_dir: 输出目录
        credential: 认证凭据
        retries: 重试次数

    Returns:
        下载成功的本地相对路径列表，如 ["images/img_1.jpg", "images/img_2.png"]
    """
    images = []
    draw_items = []

    if not item:
        return images

    # 来源1: 视频封面
    dyn_module = _safe_get_nested(item, "modules", "module_dynamic", default={})
    major = dyn_module.get("major", {}) if isinstance(dyn_module, dict) else {}
    if isinstance(major, dict):
        archive = major.get("archive", {})
        if isinstance(archive, dict):
            cover = archive.get("cover", "")
            if cover:
                draw_items.append(cover)

    # 来源2: 正文中的图片URL
    desc_module = _safe_get_nested(item, "modules", "module_dynamic", "desc", default={})
    if isinstance(desc_module, dict):
        text = desc_module.get("text", "")
        if isinstance(text, str):
            # 正则匹配图片URL
            urls = re.findall(r'https?://[^\s"]+\.(?:jpg|jpeg|png|gif|webp)', text)
            draw_items.extend(urls)

    # 来源3: 整个JSON中正则匹配的图片URL（兜底）
    if not draw_items:
        item_str = json.dumps(item, ensure_ascii=False)
        urls = re.findall(r'https?://[^\s"]+\.(?:jpg|jpeg|png|gif|webp)', item_str)
        # 去重并限制最多10张
        draw_items = list(set(urls))[:10]

    if not draw_items:
        return images

    # 创建images目录
    image_dir = output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    # 下载图片
    for i, url in enumerate(draw_items, 1):
        if not url:
            continue

        # 从URL中提取扩展名
        ext_match = re.search(r"\.(jpg|jpeg|png|gif|webp)", url, re.IGNORECASE)
        ext = ext_match.group(1) if ext_match else "jpg"

        # 生成本地文件名
        filename = f"img_{i}.{ext}"
        local_path = image_dir / filename

        # 下载图片
        ok = await download_file(url, local_path, credential, retries=retries)
        if ok:
            images.append(f"images/{filename}")

    return images


async def download_dynamic(
    item,
    uid: int,
    credential: Credential,
    store: DownloadStore,
    base_dir: Path,
    retries: int = DEFAULT_RETRY,
) -> bool:
    """
    下载单条动态。

    保存内容：
    1. dynamic.json: 完整原始数据
    2. images/: 提取的图片（部分类型）
    3. info.json: 摘要信息（ID、类型、关联内容）

    Args:
        item: DownloadItem对象
        uid: 用户UID
        credential: 认证凭据
        store: 下载状态存储
        base_dir: 输出基目录
        retries: 重试次数

    Returns:
        True成功，False失败
    """
    dynamic_id = item.content_id
    raw = item.extra.get("raw", {}) if item.extra else {}

    # 构建输出目录：dynamics/<动态ID>/
    dir_name = sanitize_filename(dynamic_id)
    output_dir = base_dir / "dynamics" / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)

    if check_signature(output_dir, "dynamic.json"):
        console.print(f"[yellow]已存在，跳过[/yellow]")
        await store.mark("dynamic", dynamic_id, "done", str(output_dir))
        return True

    try:
        # 保存完整原始JSON
        json_path = output_dir / "dynamic.json"
        json_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

        # 获取动态类型
        type_str = _get_dynamic_type_str(raw)

        # 下载图片（部分类型）
        if type_str in ("DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_LIVE_RCMD", "DYNAMIC_TYPE_AV"):
            await _download_images_from_item(raw, output_dir, credential, retries=retries)

        # 转发动态：下载原始动态的图片
        if type_str == "DYNAMIC_TYPE_FORWARD":
            orig = raw.get("orig")
            if isinstance(orig, dict) and orig:
                orig_type = _get_dynamic_type_str(orig)
                if orig_type in ("DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_AV"):
                    await _download_images_from_item(orig, output_dir, credential, retries=retries)

        # 提取嵌入式内容ID
        embedded = _extract_embedded_ids(raw)

        # 生成摘要info.json
        info = {"dynamic_id": dynamic_id, "type": type_str, "embedded": embedded}
        info_path = output_dir / "info.json"
        info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

        # 标记为完成
        await store.mark("dynamic", dynamic_id, "done", str(output_dir))
        return True

    except Exception as e:
        console.print(f"[red]动态 {dynamic_id} 处理失败: {e}[/red]")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        await store.mark("dynamic", dynamic_id, "failed", str(output_dir))
        return False
