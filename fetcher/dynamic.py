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

console = Console()


def _get_dynamic_type_str(item: dict) -> str:
    """提取动态类型字符串，如 DYNAMIC_TYPE_AV、DYNAMIC_TYPE_DRAW 等。"""
    return item.get("type", "UNKNOWN")


def _extract_embedded_ids(item: dict) -> dict:
    """
    从动态中提取关联的其他内容ID。
    
    - DYNAMIC_TYPE_AV → bvid/aid
    - DYNAMIC_TYPE_ARTICLE → cvid
    - DYNAMIC_TYPE_MUSIC → auid
    优先从 major 子对象提取，回退到 basic.rid_str。
    """
    result = {}
    type_str = _get_dynamic_type_str(item)
    basic = item.get("basic", {})

    if type_str == "DYNAMIC_TYPE_AV":
        rid_str = basic.get("rid_str", "")
        major = item.get("modules", {}).get("module_dynamic", {}).get("major", {})
        archive = major.get("archive", {}) if isinstance(major, dict) else {}
        bvid = archive.get("bvid", "")
        aid = archive.get("aid", "")
        if bvid:
            result["bvid"] = bvid
        elif aid:
            result["aid"] = str(aid)
        if rid_str:
            result.setdefault("aid", rid_str)
    elif type_str == "DYNAMIC_TYPE_ARTICLE":
        major = item.get("modules", {}).get("module_dynamic", {}).get("major", {})
        article = major.get("article", {}) if isinstance(major, dict) else {}
        cvid = article.get("id", "")
        rid_str = basic.get("rid_str", "")
        if cvid:
            result["cvid"] = str(cvid)
        elif rid_str:
            result["cvid"] = rid_str
    elif type_str == "DYNAMIC_TYPE_MUSIC":
        major = item.get("modules", {}).get("module_dynamic", {}).get("major", {})
        music = major.get("music", {}) if isinstance(major, dict) else {}
        auid = music.get("id", "")
        rid_str = basic.get("rid_str", "")
        if auid:
            result["auid"] = str(auid)
        elif rid_str:
            result["auid"] = rid_str
    return result


async def _download_images_from_item(item: dict, output_dir: Path, credential: Credential) -> list[str]:
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

    dyn_module = item.get("modules", {}).get("module_dynamic", {})
    major = dyn_module.get("major", {})
    if isinstance(major, dict):
        archive = major.get("archive", {})
        if isinstance(archive, dict):
            cover = archive.get("cover", "")
            if cover:
                draw_items.append(cover)

    desc_module = item.get("modules", {}).get("module_dynamic", {}).get("desc", {})
    if isinstance(desc_module, dict):
        text = desc_module.get("text", "")
        if isinstance(text, str):
            urls = re.findall(r'https?://[^\s"]+\.(?:jpg|jpeg|png|gif|webp)', text)
            draw_items.extend(urls)

    if not draw_items:
        item_str = json.dumps(item, ensure_ascii=False)
        urls = re.findall(r'https?://[^\s"]+\.(?:jpg|jpeg|png|gif|webp)', item_str)
        draw_items = list(set(urls))[:10]

    image_dir = output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    for i, url in enumerate(draw_items, 1):
        ext_match = re.search(r"\.(jpg|jpeg|png|gif|webp)", url, re.IGNORECASE)
        ext = ext_match.group(1) if ext_match else "jpg"
        filename = f"img_{i}.{ext}"
        local_path = image_dir / filename
        ok = await download_file(url, local_path, credential)
        if ok:
            images.append(f"images/{filename}")

    return images


async def download_dynamic(
    item,
    uid: int,
    credential: Credential,
    store: DownloadStore,
    base_dir: Path,
) -> bool:
    """
    下载单条动态。
    
    保存完整原始JSON、提取图片、生成摘要info.json。
    对于图文/视频/直播/转发动态会下载相关图片。
    返回 True/False 表示成功/失败。
    """
    dynamic_id = item.content_id
    raw = item.extra.get("raw", {})
    dir_name = sanitize_filename(dynamic_id)
    output_dir = base_dir / "dynamics" / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        json_path = output_dir / "dynamic.json"
        json_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

        type_str = _get_dynamic_type_str(raw)

        if type_str in ("DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_LIVE_RCMD", "DYNAMIC_TYPE_AV"):
            await _download_images_from_item(raw, output_dir, credential)

        if type_str == "DYNAMIC_TYPE_FORWARD":
            orig = raw.get("orig", {})
            if isinstance(orig, dict) and orig:
                orig_type = _get_dynamic_type_str(orig)
                if orig_type in ("DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_AV"):
                    await _download_images_from_item(orig, output_dir, credential)

        embedded = _extract_embedded_ids(raw)

        info = {"dynamic_id": dynamic_id, "type": type_str, "embedded": embedded}
        info_path = output_dir / "info.json"
        info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

        await store.mark("dynamic", dynamic_id, "done", str(output_dir))
        return True

    except Exception as e:
        console.print(f"[red]动态 {dynamic_id} 处理失败: {e}[/red]")
        await store.mark("dynamic", dynamic_id, "failed", str(output_dir))
        return False
