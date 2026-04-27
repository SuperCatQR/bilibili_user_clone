import asyncio
import aiohttp
from pathlib import Path

from bilibili_api import Credential

from config import CHUNK_SIZE, DEFAULT_RETRY, BACKOFF_BASE, BACKOFF_MAX


def _build_headers(credential: Credential) -> dict:
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
                        from rich.console import Console
                        Console().print(f"[yellow]412 限速，等待 {wait}s...[/yellow]")
                        await asyncio.sleep(wait)
                        continue
                    if resp.status != 200:
                        from rich.console import Console
                        Console().print(f"[red]HTTP {resp.status}[/red]")
                        continue
                    with open(output_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                            f.write(chunk)
                    return True
        except (aiohttp.ClientError, asyncio.TimeoutError, AssertionError) as e:
            from rich.console import Console
            Console().print(f"[yellow]网络异常: {e}，重试...[/yellow]")
            await asyncio.sleep(2)

    return False
