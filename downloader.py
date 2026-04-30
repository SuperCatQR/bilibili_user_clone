"""
异步文件下载模块

提供带重试和412指数退避的文件下载功能。
自动补全 // 开头的URL、携带 Cookie/Referer 头、分块写入磁盘。
支持共享aiohttp.ClientSession以复用TCP连接。
"""

import asyncio
import aiohttp
from pathlib import Path
from typing import Optional

from bilibili_api import Credential
from rich.console import Console

from config import CHUNK_SIZE, DEFAULT_RETRY, BACKOFF_BASE, BACKOFF_MAX

console = Console()

# 全局共享的aiohttp.ClientSession，用于复用TCP连接
_shared_session: Optional[aiohttp.ClientSession] = None


async def get_shared_session(headers: dict = None) -> aiohttp.ClientSession:
    """
    获取共享的aiohttp.ClientSession实例。
    
    如果session不存在或已关闭，创建新session。
    共享session可以复用TCP连接，提高下载效率。
    
    Args:
        headers: 请求头，仅在创建新session时使用
    
    Returns:
        共享的aiohttp.ClientSession实例
    """
    global _shared_session
    if _shared_session is None or _shared_session.closed:
        _shared_session = aiohttp.ClientSession(headers=headers)
    return _shared_session


async def close_shared_session():
    """关闭共享的aiohttp.ClientSession。"""
    global _shared_session
    if _shared_session and not _shared_session.closed:
        await _shared_session.close()
        _shared_session = None


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
    use_shared_session: bool = True,
) -> bool:
    """
    异步下载文件到指定路径。
    
    特性：
    - 自动补全 // 开头的URL为 https:
    - 412 响应触发指数退避重试（5*2^attempt 秒，上限300秒）
    - 非200响应直接重试
    - 网络异常（ClientError/TimeoutError/PayloadError）重试
    - 分块写入（CHUNK_SIZE），避免大文件占满内存
    - 支持共享session复用TCP连接
    - 下载失败时自动清理部分文件
    
    Args:
        url: 下载URL
        output_path: 输出文件路径
        credential: 认证凭据
        retries: 重试次数
        use_shared_session: 是否使用共享session（默认True）
    
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
            # 根据参数决定使用共享session还是创建新session
            if use_shared_session:
                session = await get_shared_session(headers)
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=600, connect=30)) as resp:
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
            else:
                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=600, connect=30)) as resp:
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
        except (
            aiohttp.ClientError,
            aiohttp.ClientPayloadError,
            aiohttp.ClientResponseError,
            asyncio.TimeoutError,
            AssertionError,
            ConnectionError,
            OSError,
        ) as e:
            # 清理可能的部分文件
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
            
            # 区分超时和其他网络错误
            if isinstance(e, asyncio.TimeoutError):
                console.print(f"[yellow]下载超时（尝试 {attempt + 1}/{retries}）: {e}[/yellow]")
            else:
                console.print(f"[yellow]网络异常（尝试 {attempt + 1}/{retries}）: {e}[/yellow]")
            
            # 指数退避等待
            if attempt < retries - 1:
                wait = min(2 * (2 ** attempt), 30)
                await asyncio.sleep(wait)

    return False
