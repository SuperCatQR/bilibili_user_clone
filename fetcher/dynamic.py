"""
动态下载模块

保存动态的完整原始JSON + 摘要info.json + 提取图片。
支持多种动态类型的图片提取（图文、视频封面、直播间、转发动态）。
识别嵌入式内容（关联视频/专栏/音频的ID）并记录在info.json的embedded字段中。
"""

import json
import re
from pathlib import Path

from bilibili_api import Credential
from rich.console import Console

from downloader import download_file
from store import DownloadStore
from utils import sanitize_filename
from config import DEFAULT_RETRY

console = Console()


def _get_dynamic_type_str(item: dict) -> str:
    """提取动态类型字符串，如 DYNAMIC_TYPE_AV、DYNAMIC_TYPE_DRAW 等。"""
    if not item:
        return "UNKNOWN"
    return item.get("type", "UNKNOWN")


def _safe_get_nested(data: dict, *keys, default=None):
    """
    安全地获取嵌套字典中的值，避免NoneType错误。
    
    Args:
        data: 字典对象
        *keys: 嵌套的键路径
        default: 默认值
    
    Returns:
        嵌套键对应的值，如果任何中间键不存在或为None则返回default
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
    
    - DYNAMIC_TYPE_AV → bvid/aid
    - DYNAMIC_TYPE_ARTICLE → cvid
    - DYNAMIC_TYPE_MUSIC → auid
    优先从 major 子对象提取，回退到 basic.rid_str。
    """
    result = {}
    if not item:
        return result
        
    type_str = _get_dynamic_type_str(item)
    basic = item.get("basic") or {}
    rid_str = basic.get("rid_str", "") if isinstance(basic, dict) else ""

    if type_str == "DYNAMIC_TYPE_AV":
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
        major = _safe_get_nested(item, "modules", "module_dynamic", "major", default={})
        article = major.get("article", {}) if isinstance(major, dict) else {}
        cvid = article.get("id", "") if isinstance(article, dict) else ""
        if cvid:
            result["cvid"] = str(cvid)
        elif rid_str:
            result["cvid"] = rid_str
    elif type_str == "DYNAMIC_TYPE_MUSIC":
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
    
    Returns:
        下载成功的本地相对路径列表
    """
    images = []
    draw_items = []

    if not item:
        return images

    dyn_module = _safe_get_nested(item, "modules", "module_dynamic", default={})
    major = dyn_module.get("major", {}) if isinstance(dyn_module, dict) else {}
    if isinstance(major, dict):
        archive = major.get("archive", {})
        if isinstance(archive, dict):
            cover = archive.get("cover", "")
            if cover:
                draw_items.append(cover)

    desc_module = _safe_get_nested(item, "modules", "module_dynamic", "desc", default={})
    if isinstance(desc_module, dict):
        text = desc_module.get("text", "")
        if isinstance(text, str):
            urls = re.findall(r'https?://[^\s"]+\.(?:jpg|jpeg|png|gif|webp)', text)
            draw_items.extend(urls)

    if not draw_items:
        item_str = json.dumps(item, ensure_ascii=False)
        urls = re.findall(r'https?://[^\s"]+\.(?:jpg|jpeg|png|gif|webp)', item_str)
        draw_items = list(set(urls))[:10]

    if not draw_items:
        return images

    image_dir = output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    for i, url in enumerate(draw_items, 1):
        if not url:
            continue
        ext_match = re.search(r"\.(jpg|jpeg|png|gif|webp)", url, re.IGNORECASE)
        ext = ext_match.group(1) if ext_match else "jpg"
        filename = f"img_{i}.{ext}"
        local_path = image_dir / filename
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
    
    保存完整原始JSON、提取图片、生成摘要info.json。
    对于图文/视频/直播/转发动态会下载相关图片。
    返回 True/False 表示成功/失败。
    """
    dynamic_id = item.content_id
    raw = item.extra.get("raw", {}) if item.extra else {}
    dir_name = sanitize_filename(dynamic_id)
    output_dir = base_dir / "dynamics" / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        json_path = output_dir / "dynamic.json"
        json_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

        type_str = _get_dynamic_type_str(raw)

        if type_str in ("DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_LIVE_RCMD", "DYNAMIC_TYPE_AV"):
            await _download_images_from_item(raw, output_dir, credential, retries=retries)

        if type_str == "DYNAMIC_TYPE_FORWARD":
            orig = raw.get("orig")
            if isinstance(orig, dict) and orig:
                orig_type = _get_dynamic_type_str(orig)
                if orig_type in ("DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_AV"):
                    await _download_images_from_item(orig, output_dir, credential, retries=retries)

        embedded = _extract_embedded_ids(raw)

        info = {"dynamic_id": dynamic_id, "type": type_str, "embedded": embedded}
        info_path = output_dir / "info.json"
        info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

        await store.mark("dynamic", dynamic_id, "done", str(output_dir))
        return True

    except Exception as e:
        console.print(f"[red]动态 {dynamic_id} 处理失败: {e}[/red]")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        await store.mark("dynamic", dynamic_id, "failed", str(output_dir))
        return False
