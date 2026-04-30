"""
专栏下载模块

下载B站专栏文章，保存为：
- info.json（完整元数据，含原始HTML）
- article.md（转换后的Markdown正文）
- images/（内嵌图片）

下载流程：
1. 调用 get_detail() 获取完整内容
2. HTML正文经 article_converter 转为Markdown
3. 图片自动下载到本地images目录

注意：即使文章无正文内容（content为空）也标记为 done，
因为 info.json 中的元数据已经保存成功。
"""

import json
from pathlib import Path

from bilibili_api import article, Credential
from rich.console import Console

from article_converter import html_to_markdown
from store import DownloadStore
from utils import sanitize_filename, check_signature
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
    try:
        cvid = int(item.content_id)
    except (ValueError, TypeError):
        console.print(f"[red]专栏 content_id 无效: {item.content_id}[/red]")
        await store.mark("article", str(item.content_id), "failed", None)
        return False
    title = item.title

    # 构建输出目录：articles/cv<号> - <标题>/
    dir_name = sanitize_filename(f"cv{cvid} - {title}")
    output_dir = base_dir / "articles" / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)

    if check_signature(output_dir, "article.md", "info.json"):
        console.print(f"[yellow]已存在，跳过[/yellow]")
        await store.mark("article", str(cvid), "done", str(output_dir))
        return True

    # 创建Article对象
    a = article.Article(cvid=cvid, credential=credential)

    try:
        # 获取专栏详情（含HTML正文）
        detail = await a.get_detail()
        html_content = detail.get("content", "")

        # 保存info.json（完整元数据，含原始HTML）
        info_path = output_dir / "info.json"
        info_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")

        # 转换HTML为Markdown
        if html_content:
            # html_to_markdown会下载图片到images目录
            md = await html_to_markdown(html_content, output_dir, credential, retries=retries)

            # 写入Markdown文件
            md_path = output_dir / "article.md"
            md_path.write_text(md, encoding="utf-8")
        else:
            console.print(f"[yellow]专栏 cv{cvid} 无内容[/yellow]")

        # 标记为完成（即使无内容，元数据已保存）
        await store.mark("article", str(cvid), "done", str(output_dir))
        return True

    except Exception as e:
        console.print(f"[red]专栏 cv{cvid} 处理失败: {e}[/red]")
        await store.mark("article", str(cvid), "failed", str(output_dir))
        return False
