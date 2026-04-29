"""
专栏下载模块

下载B站专栏文章，保存为 info.json（完整元数据）+ article.md（转换后的Markdown正文）+ images/（内嵌图片）。
使用 article_converter 将HTML正文转为Markdown，图片自动下载到本地。
即使文章无正文内容（content为空）也标记为 done。
"""

import json
from pathlib import Path

from bilibili_api import article, Credential
from rich.console import Console

from article_converter import html_to_markdown
from store import DownloadStore
from utils import sanitize_filename
from config import DEFAULT_RETRY

console = Console()


async def download_article(
    item,
    uid: int,
    credential: Credential,
    store: DownloadStore,
    base_dir: Path,
    retries: int = DEFAULT_RETRY,
) -> bool:
    """
    下载单个专栏文章。
    
    调用 get_detail() 获取完整内容，HTML正文经 article_converter 转为Markdown。
    """
    cvid = int(item.content_id)
    title = item.title
    dir_name = sanitize_filename(f"cv{cvid} - {title}")
    output_dir = base_dir / "articles" / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)

    a = article.Article(cvid=cvid, credential=credential)

    try:
        detail = await a.get_detail()
        html_content = detail.get("content", "")

        info_path = output_dir / "info.json"
        info_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")

        if html_content:
            md = await html_to_markdown(html_content, output_dir, credential, retries=retries)
            md_path = output_dir / "article.md"
            md_path.write_text(md, encoding="utf-8")
        else:
            console.print(f"[yellow]专栏 cv{cvid} 无内容[/yellow]")

        await store.mark("article", str(cvid), "done", str(output_dir))
        return True

    except Exception as e:
        console.print(f"[red]专栏 cv{cvid} 处理失败: {e}[/red]")
        await store.mark("article", str(cvid), "failed", str(output_dir))
        return False
