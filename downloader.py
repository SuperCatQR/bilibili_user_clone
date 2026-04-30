"""
异步文件下载模块

提供带重试和412指数退避的文件下载功能。

核心特性：
- 自动补全 // 开头的URL（B站CDN经常返回协议相对URL）
- 携带 Cookie/Referer 头（通过B站防盗链验证）
- 分块写入磁盘（避免大文件占满内存）
- 支持共享aiohttp.ClientSession以复用TCP连接
- 412限速时自动指数退避重试
- 下载失败时自动清理部分文件
- 长连接被CDN切断时自动回退到Range分段下载

HTTP 412状态码说明：
B站API在检测到异常请求频率时会返回412状态码（Precondition Failed），
此时需要等待一段时间后重试。指数退避策略可以避免雪崩效应。

aiohttp选择理由：
- 纯异步HTTP客户端，与asyncio完美集成
- 支持流式下载（iter_chunked），适合大文件
- 连接池管理，支持TCP连接复用
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
# 使用单例模式，所有下载共享同一个连接池
# 好处：减少TCP握手开销，提高下载效率
# 注意：需要在程序退出时调用close_shared_session()关闭
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

    # 检查session是否存在且未关闭
    # aiohttp.ClientSession.closed 属性表示session是否已关闭
    if _shared_session is None or _shared_session.closed:
        _shared_session = aiohttp.ClientSession(headers=headers)

    return _shared_session


async def close_shared_session():
    """关闭共享的aiohttp.ClientSession。在程序退出时调用，释放资源。"""
    global _shared_session
    if _shared_session and not _shared_session.closed:
        await _shared_session.close()
        _shared_session = None


def _build_headers(credential: Credential) -> dict:
    """
    构建下载请求头，包含 User-Agent、Referer 和 Cookie。

    这些请求头是通过B站防盗链验证的必要条件：
    - User-Agent: 模拟浏览器，避免被识别为爬虫
    - Referer: 告诉服务器请求来源是bilibili.com
    - Cookie: 携带登录凭据，通过身份验证

    Args:
        credential: B站认证凭据

    Returns:
        包含认证信息的请求头字典
    """
    headers = {
        # 模拟Chrome浏览器的User-Agent
        # 这是最常见的浏览器UA，不容易被识别为爬虫
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        # Referer设置为bilibili.com，通过防盗链验证
        "Referer": "https://www.bilibili.com",
    }

    # 从Credential对象提取Cookie
    # Credential.get_cookies() 返回字典，包含sessdata等会话Cookie
    cookies = credential.get_cookies()
    if cookies:
        # 将Cookie字典转换为字符串格式：key1=value1; key2=value2
        # 过滤空值Cookie
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items() if v)
        headers["Cookie"] = cookie_str

    return headers


async def _download_with_ranges(url: str, output_path: Path, headers: dict, range_size: int = 1024 * 1024) -> bool:
    """
    使用 HTTP Range 请求分段下载文件。

    某些B站CDN节点会中途切断长连接大文件传输，但 Range 请求稳定。
    Range请求的原理：
    1. 先用 HEAD 请求获取文件总大小
    2. 然后逐段下载，每段 range_size 字节
    3. 每段使用独立的TCP连接，避免长连接被切断

    Args:
        url: 下载URL
        output_path: 输出文件路径
        headers: 请求头
        range_size: 每段大小（字节），默认1MB

    Returns:
        True下载成功，False失败
    """
    output_path = Path(output_path)

    # 第一步：获取文件总大小
    # 使用HEAD请求，不下载文件内容，只获取元数据
    # allow_redirects=True: 跟随重定向（B站CDN经常重定向）
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.head(url, timeout=aiohttp.ClientTimeout(total=30, connect=10), allow_redirects=True) as resp:
            if resp.status not in (200, 302):
                return False
            # Content-Length头包含文件总大小（字节）
            total_size = int(resp.headers.get("Content-Length", 0))
            if not total_size:
                return False

    # 第二步：逐段下载
    success = False
    try:
        with open(output_path, "wb") as f:
            start = 0
            chunk_num = 0
            while start < total_size:
                # 计算当前段的结束位置
                end = min(start + range_size - 1, total_size - 1)

                # 构造Range请求头
                # 格式：bytes=开始字节-结束字节（包含）
                range_headers = dict(headers)
                range_headers["Range"] = f"bytes={start}-{end}"

                # 每段最多重试3次
                for attempt in range(3):
                    try:
                        async with aiohttp.ClientSession(headers=range_headers) as session:
                            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60, connect=10)) as resp:
                                # 200: 完整响应（服务器不支持Range）
                                # 206: 部分内容（服务器支持Range）
                                if resp.status not in (200, 206):
                                    return False
                                data = await resp.read()
                                f.write(data)
                                chunk_num += 1
                                # 移动到下一段
                                start = end + 1
                                break
                    except Exception:
                        if attempt < 2:
                            await asyncio.sleep(2)
                        else:
                            return False

            success = True
    finally:
        # 下载失败时清理残留的部分文件，避免下次运行误判
        if not success and output_path.exists():
            try:
                output_path.unlink()
            except OSError:
                pass

    return success


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
    - 长连接被CDN切断时自动回退到 Range 分段下载

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
    # 确保输出目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 构建包含认证信息的请求头
    headers = _build_headers(credential)

    # 自动补全协议相对URL（//开头的URL）
    # B站CDN经常返回 //i0.hdslb.com/... 这样的URL
    if url.startswith("//"):
        url = "https:" + url

    timeout = aiohttp.ClientTimeout(total=600, connect=30)

    for attempt in range(retries):
        try:
            if use_shared_session:
                session = await get_shared_session(headers)
                ctx = session.get(url, timeout=timeout)
            else:
                session = aiohttp.ClientSession(headers=headers)
                ctx = session.get(url, timeout=timeout)

            async with ctx as resp:
                if resp.status == 412:
                    if not use_shared_session:
                        await session.close()
                    wait = min(BACKOFF_BASE * (2 ** attempt), BACKOFF_MAX)
                    console.print(f"[yellow]412 限速，等待 {wait}s...[/yellow]")
                    await asyncio.sleep(wait)
                    continue

                if resp.status != 200:
                    if not use_shared_session:
                        await session.close()
                    console.print(f"[red]HTTP {resp.status}[/red]")
                    continue

                with open(output_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                        f.write(chunk)

            if not use_shared_session:
                await session.close()
            return True

        except (
            aiohttp.ClientError,        # HTTP客户端错误
            aiohttp.ClientPayloadError, # 响应体传输错误（CDN切断连接）
            aiohttp.ClientResponseError,# 响应错误
            asyncio.TimeoutError,       # 超时
            AssertionError,             # 断言失败
            ConnectionError,            # 连接错误
            OSError,                    # 操作系统错误（网络不可达等）
        ) as e:
            # 清理可能的部分文件
            # 避免留下不完整的文件导致后续判断错误
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass

            # 区分超时和其他网络错误，提供更精确的日志
            if isinstance(e, asyncio.TimeoutError):
                console.print(f"[yellow]下载超时（尝试 {attempt + 1}/{retries}）: {e}[/yellow]")
            else:
                console.print(f"[yellow]网络异常（尝试 {attempt + 1}/{retries}）: {e}[/yellow]")

            # 最后一次尝试失败且是 Payload 错误，回退到 Range 分段下载
            # PayloadError通常表示长连接被CDN切断，Range请求更稳定
            if attempt == retries - 1 and isinstance(e, aiohttp.ClientPayloadError):
                console.print(f"[yellow]尝试 Range 分段下载...[/yellow]")
                if await _download_with_ranges(url, output_path, headers):
                    return True

            # 指数退避等待
            # 等待时间 = 2 * 2^attempt，上限30秒
            # 比412退避更短，因为网络错误恢复更快
            if attempt < retries - 1:
                wait = min(2 * (2 ** attempt), 30)
                await asyncio.sleep(wait)

    return False
