import json
from pathlib import Path

from bilibili_api import article, Credential
from rich.console import Console

from article_converter import html_to_markdown
from store import DownloadStore
from utils import sanitize_filename

console = Console()


async def download_article(
    item,
    uid: int,
    credential: Credential,
    store: DownloadStore,
    base_dir: Path,
) -> bool:
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
            md = await html_to_markdown(html_content, output_dir, credential)
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
