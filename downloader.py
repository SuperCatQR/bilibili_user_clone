"""
异步文件下载模块

提供带重试和412指数退避的文件下载功能。
自动补全 // 开头的URL、携带 Cookie/Referer 头、分块写入磁盘。
"""

import asyncio
import aiohttp
from pathlib import Path

from bilibili_api import Credential
from rich.console import Console

from config import CHUNK_SIZE, DEFAULT_RETRY, BACKOFF_BASE, BACKOFF_MAX

console = Console()


def _build_headers(credential: Credential) -> dict:
    """
    构建下载请求头，包含 User-Agent、Referer 和 Cookie。
    Cookie 从 Credential 对象中提取，用于通过B站的防盗链验证。
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com",
    }
    cookies = credential.get_cookies()
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items() if v)
        headers["Cookie"] = cookie_str
    return headers


async def download_file(
    url: str,
    output_path: Path,
    credential: Credential,
    retries: int = DEFAULT_RETRY,
) -> bool:
    """
    异步下载文件到指定路径。
    
    特性：
    - 自动补全 // 开头的URL为 https:
    - 412 响应触发指数退避重试（5*2^attempt 秒，上限300秒）
    - 非200响应直接重试
    - 网络异常（ClientError/TimeoutError）重试
    - 分块写入（CHUNK_SIZE），避免大文件占满内存
    
    Returns:
        True 下载成功，False 所有重试均失败
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    headers = _build_headers(credential)

    if url.startswith("//"):
        url = "https:" + url

    for attempt in range(retries):
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                    if resp.status == 412:
                        wait = min(BACKOFF_BASE * (2 ** attempt), BACKOFF_MAX)
                        console.print(f"[yellow]412 限速，等待 {wait}s...[/yellow]")
                        await asyncio.sleep(wait)
                        continue
                    if resp.status != 200:
                        console.print(f"[red]HTTP {resp.status}[/red]")
                        continue
                    with open(output_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                            f.write(chunk)
                    return True
        except (aiohttp.ClientError, asyncio.TimeoutError, AssertionError) as e:
            console.print(f"[yellow]网络异常: {e}，重试...[/yellow]")
            await asyncio.sleep(2)

    return False
