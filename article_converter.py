import re
import json
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

from downloader import download_file
from bilibili_api import Credential


async def _process_element(el: Tag | NavigableString, output_dir: Path, credential: Credential, image_idx: list) -> str:
    if isinstance(el, NavigableString):
        text = str(el)
        if text.strip() == "":
            return ""
        return text

    if not isinstance(el, Tag):
        return ""

    tag = el.name

    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(tag[1])
        inner = await _process_children(el, output_dir, credential, image_idx)
        return f"\n\n{'#' * level} {inner.strip()}\n\n"

    if tag == "p":
        inner = await _process_children(el, output_dir, credential, image_idx)
        return f"\n\n{inner.strip()}\n\n"

    if tag == "blockquote":
        inner = await _process_children(el, output_dir, credential, image_idx)
        lines = inner.strip().split("\n")
        return "\n\n" + "\n".join(f"> {line}" for line in lines) + "\n\n"

    if tag in ("pre",):
        code_el = el.find("code")
        if code_el:
            lang = code_el.get("class", [""])
            lang_str = lang[0].replace("language-", "") if lang else ""
            code_text = code_el.get_text()
        else:
            lang_str = ""
            code_text = el.get_text()
        return f"\n\n```{lang_str}\n{code_text.strip()}\n```\n\n"

    if tag == "code" and el.parent and el.parent.name != "pre":
        return f"`{el.get_text()}`"

    if tag == "img":
        src = el.get("data-src") or el.get("src", "")
        alt = el.get("alt", "")
        if src:
            image_dir = output_dir / "images"
            image_dir.mkdir(parents=True, exist_ok=True)
            image_idx[0] += 1
            ext_match = re.search(r"\.(jpg|jpeg|png|gif|webp|bmp)", src, re.IGNORECASE)
            ext = ext_match.group(1) if ext_match else "jpg"
            filename = f"img_{image_idx[0]}.{ext}"
            local_path = image_dir / filename
            ok = await download_file(src, local_path, credential)
            if ok:
                return f"\n\n![{alt}](images/{filename})\n\n"
            else:
                return f"\n\n![{alt}]({src})\n\n"
        return ""

    if tag == "a":
        href = el.get("href", "")
        inner = await _process_children(el, output_dir, credential, image_idx)
        return f"[{inner.strip()}]({href})"

    if tag == "strong" or tag == "b":
        inner = await _process_children(el, output_dir, credential, image_idx)
        return f"**{inner.strip()}**"

    if tag == "em" or tag == "i":
        inner = await _process_children(el, output_dir, credential, image_idx)
        return f"*{inner.strip()}*"

    if tag == "ul":
        items = []
        for li in el.find_all("li", recursive=False):
            inner = await _process_children(li, output_dir, credential, image_idx)
            items.append(f"- {inner.strip()}")
        return "\n\n" + "\n".join(items) + "\n\n"

    if tag == "ol":
        items = []
        for i, li in enumerate(el.find_all("li", recursive=False), 1):
            inner = await _process_children(li, output_dir, credential, image_idx)
            items.append(f"{i}. {inner.strip()}")
        return "\n\n" + "\n".join(items) + "\n\n"

    if tag == "br":
        return "\n"

    if tag == "figure":
        return await _process_children(el, output_dir, credential, image_idx)

    if tag == "figcaption":
        inner = await _process_children(el, output_dir, credential, image_idx)
        return f"\n*{inner.strip()}*\n"

    if tag in ("span", "div", "section", "article", "main", "header", "footer", "nav"):
        return await _process_children(el, output_dir, credential, image_idx)

    return await _process_children(el, output_dir, credential, image_idx)


async def _process_children(el: Tag, output_dir: Path, credential: Credential, image_idx: list) -> str:
    result = []
    for child in el.children:
        result.append(await _process_element(child, output_dir, credential, image_idx))
    return "".join(result)


async def html_to_markdown(html: str, output_dir: Path, credential: Credential) -> str:
    soup = BeautifulSoup(html, "lxml")
    root = soup.find("div", class_="article-content") or soup.find("article") or soup.body or soup
    image_idx = [0]
    md = await _process_children(root, output_dir, credential, image_idx)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()
