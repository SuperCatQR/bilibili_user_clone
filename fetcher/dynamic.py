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
    return item.get("type", "UNKNOWN")


def _extract_embedded_ids(item: dict) -> dict:
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
    selected_types: set[str],
) -> list[dict]:
    dynamic_id = item.content_id
    raw = item.extra.get("raw", {})
    dir_name = sanitize_filename(dynamic_id)
    output_dir = base_dir / "dynamics" / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)

    embedded_items = []

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
        if type_str == "DYNAMIC_TYPE_AV" and "video" in selected_types and embedded.get("bvid"):
            embedded_items.append({"content_type": "video", "content_id": embedded["bvid"]})
        if type_str == "DYNAMIC_TYPE_ARTICLE" and "article" in selected_types and embedded.get("cvid"):
            embedded_items.append({"content_type": "article", "content_id": embedded["cvid"]})
        if type_str == "DYNAMIC_TYPE_MUSIC" and "audio" in selected_types and embedded.get("auid"):
            embedded_items.append({"content_type": "audio", "content_id": embedded["auid"]})

        info = {"dynamic_id": dynamic_id, "type": type_str, "embedded": embedded}
        info_path = output_dir / "info.json"
        info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

        await store.mark("dynamic", dynamic_id, "done", str(output_dir))
        return True

    except Exception as e:
        console.print(f"[red]动态 {dynamic_id} 处理失败: {e}[/red]")
        await store.mark("dynamic", dynamic_id, "failed", str(output_dir))
        return False
