"""
视频下载模块

支持5种下载模式：
- full: 下载视频轨+音频轨，ffmpeg合流为 video.mp4
- video-only: 仅下载视频轨 video.m4v
- audio-only: 仅下载音频轨，转码为 audio.wav (PCM 16bit 16kHz 单声道)
- subtitle-only: 仅下载字幕 subtitles.srt（中文优先）
- none: 仅保存 info.json

所有模式均输出 info.json 元数据文件。

多分P视频支持：
- 自动检测视频是否有多个分P
- 遍历所有分P并下载
- 每个分P创建独立子目录
- 整体状态标记：所有分P成功才标记为done

ffmpeg使用说明：
- full模式需要ffmpeg进行音视频合流
- audio-only模式需要ffmpeg进行音频转码
- video-only模式不需要ffmpeg
- ffmpeg通过python-ffmpeg库调用，底层执行ffmpeg命令

下载流程（以full模式为例）：
1. 获取视频元数据（get_info）
2. 获取所有分P的CID列表
3. 对每个分P：
   a. 获取下载URL（get_download_url）
   b. 解析最佳流（VideoDownloadURLDataDetecter）
   c. 下载视频轨（.m4v）
   d. 下载音频轨（.m4a）
   e. ffmpeg合流为.mp4
   f. 清理临时文件
"""

import json
import asyncio
from pathlib import Path

from bilibili_api import video, Credential
from bilibili_api.video import VideoDownloadURLDataDetecter, VideoStreamDownloadURL, AudioStreamDownloadURL
from rich.console import Console

from downloader import download_file
from store import DownloadStore
from utils import sanitize_filename
from ffmpeg_utils import convert_to_wav
from config import DEFAULT_RETRY

console = Console()


async def _get_streams(v: video.Video, cid: int):
    """
    获取下载URL并解析最佳视频流和音频流。

    调用链：
      v.get_download_url(cid) → B站API返回所有可用流URL
      VideoDownloadURLDataDetecter(dd) → 解析原始数据为结构化对象
      detect_best_streams() → 按质量排序，返回最佳流列表

    返回值中的 vs/aus 可能为 None：
      - 部分老视频只有视频流无独立音频流
      - 部分音频区视频只有音频流
    """
    dd = await v.get_download_url(cid=cid)
    det = VideoDownloadURLDataDetecter(dd)
    streams = det.detect_best_streams()

    vs = None
    aus = None
    for s in streams:
        if isinstance(s, VideoStreamDownloadURL) and vs is None:
            vs = s
        if isinstance(s, AudioStreamDownloadURL) and aus is None:
            aus = s
    return vs, aus


async def _download_with_refresh(get_url_fn, path: Path, credential: Credential, retries: int, label: str) -> bool:
    """
    带URL刷新重试的下载。

    执行流：
      第1轮：get_url_fn() → download_file()
        ↓ 失败
      等待3秒（URL可能已过期）
      第2轮：get_url_fn() → download_file()（重新获取URL）
        ↓ 失败
      返回 False

    为什么最多2轮而非retries轮：
      URL有效期约30分钟，如果同一URL重试retries次都失败，
      说明URL已过期而非网络抖动，必须刷新URL才有意义。
      2轮=2个不同URL，每轮内download_file自身有retries次重试。
    """
    for outer in range(2):
        url = await get_url_fn()
        if not url:
            return False
        ok = await download_file(url, path, credential, retries=retries)
        if ok:
            return True
        if outer == 0:
            console.print(f"[yellow]{label} 下载失败，刷新URL重试...[/yellow]")
            await asyncio.sleep(3)
    return False


async def _download_subtitle(v: video.Video, cid: int, output_dir: Path, credential: Credential = None) -> bool:
    """
    下载视频字幕并保存为SRT格式。

    字幕语言优先级：中文（lan以zh开头）> 第一个可用字幕。
    无字幕时返回 False。

    SRT格式说明：
    - 每段字幕包含：序号、时间轴、文本内容
    - 时间轴格式：HH:MM:SS,mmm --> HH:MM:SS,mmm
    - 段与段之间用空行分隔

    Args:
        v: Video对象
        cid: 分P的CID
        output_dir: 输出目录
        credential: 认证凭据（用于携带Cookie/Referer头）

    Returns:
        True下载成功，False无字幕或下载失败
    """
    try:
        # 获取字幕信息
        subtitle_info = await v.get_subtitle(cid=cid)
        subtitles = subtitle_info.get("subtitles", [])

        # 检查是否有可用字幕
        if not subtitles:
            console.print("[yellow]无字幕[/yellow]")
            return False

        # 选择字幕语言（中文优先）
        zh_sub = None
        for s in subtitles:
            lang = s.get("lan", "")
            # 以zh开头的语言代码表示中文（zh-CN, zh-TW等）
            if lang.startswith("zh"):
                zh_sub = s
                break

        # 如果没有中文字幕，使用第一个可用字幕
        if not zh_sub:
            zh_sub = subtitles[0]

        # 获取字幕URL
        sub_url = zh_sub.get("subtitle_url", "")
        if not sub_url:
            return False

        # 补全协议相对URL
        if sub_url.startswith("//"):
            sub_url = "https:" + sub_url

        # 下载字幕JSON
        # 使用downloader模块的_build_headers构建认证头
        from downloader import _build_headers
        headers = _build_headers(credential) if credential else {}

        import aiohttp
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(sub_url) as resp:
                if resp.status != 200:
                    return False
                # content_type=None: 忽略Content-Type检查（B站字幕可能返回错误的Content-Type）
                sub_data = await resp.json(content_type=None)

        # 转换为SRT格式
        body = sub_data.get("body", [])
        srt_lines = []
        for i, item in enumerate(body, 1):
            start = item.get("from", 0)  # 开始时间（秒）
            end = item.get("to", 0)      # 结束时间（秒）
            content = item.get("content", "")  # 字幕文本

            # SRT格式：序号、时间轴、文本、空行
            srt_lines.append(str(i))
            srt_lines.append(f"{_format_srt_time(start)} --> {_format_srt_time(end)}")
            srt_lines.append(content)
            srt_lines.append("")

        # 写入SRT文件
        srt_path = output_dir / "subtitles.srt"
        srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
        return True

    except Exception as e:
        console.print(f"[red]字幕下载失败: {e}[/red]")
        return False


def _format_srt_time(seconds: float) -> str:
    """
    将秒数格式化为SRT时间轴 HH:MM:SS,mmm。

    转换逻辑：
    - hours = seconds // 3600
    - minutes = (seconds % 3600) // 60
    - secs = seconds % 60
    - milliseconds = (seconds % 1) * 1000

    Args:
        seconds: 秒数（浮点数）

    Returns:
        格式化的时间字符串，如 "00:01:23,456"
    """
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


async def download_video(
    item,
    uid: int,
    credential: Credential,
    store: DownloadStore,
    base_dir: Path,
    video_mode: str = "full",
    retries: int = DEFAULT_RETRY,
) -> bool:
    """
    下载单个视频，根据 video_mode 决定下载内容。

    支持多分P视频：遍历所有分P并下载。
    返回 True 表示成功（包括 none 模式和 subtitle-only 无字幕标记为 skipped），
    返回 False 表示失败（下次运行会重试）。

    Args:
        item: DownloadItem对象
        uid: 用户UID
        credential: 认证凭据
        store: 下载状态存储
        base_dir: 输出基目录
        video_mode: 视频下载模式
        retries: 重试次数

    Returns:
        True成功，False失败
    """
    bvid = item.content_id
    title = item.title

    # 构建输出目录：videos/<BV号> - <标题>/
    dir_name = sanitize_filename(f"{bvid} - {title}")
    output_dir = base_dir / "videos" / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)

    # 创建Video对象
    v = video.Video(bvid=bvid, credential=credential)

    try:
        # 获取视频元数据
        info = await v.get_info()

        # 保存info.json
        info_path = output_dir / "info.json"
        info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

        # none模式：跳过下载，仅保存元数据
        if video_mode == "none":
            await store.mark("video", bvid, "skipped", str(output_dir))
            return True

        # 获取所有分P的CID列表
        pages = info.get("pages", [])
        if not pages:
            console.print("[red]无法获取分P信息，跳过下载[/red]")
            await store.mark("video", bvid, "failed", str(output_dir))
            return False

        # 如果只有一个分P，直接下载
        if len(pages) == 1:
            cid = pages[0].get("cid", 0)
            if not cid:
                console.print("[red]无法获取CID，跳过下载[/red]")
                await store.mark("video", bvid, "failed", str(output_dir))
                return False
            return await _download_single_part(v, cid, output_dir, video_mode, retries, store, bvid, credential)

        # 多分P视频：遍历所有分P
        console.print(f"[cyan]视频有 {len(pages)} 个分P[/cyan]")
        all_success = True
        for i, page in enumerate(pages, 1):
            cid = page.get("cid", 0)
            part_title = page.get("part", f"第{i}P")

            if not cid:
                console.print(f"[yellow]分P {i} 无法获取CID，跳过[/yellow]")
                continue

            # 为每个分P创建子目录：P<序号> - <分P标题>/
            part_dir = output_dir / sanitize_filename(f"P{i} - {part_title}")
            part_dir.mkdir(parents=True, exist_ok=True)

            console.print(f"[cyan]下载分P {i}/{len(pages)}: {part_title}[/cyan]")
            success = await _download_single_part(v, cid, part_dir, video_mode, retries, store, f"{bvid}_P{i}", credential)
            if not success:
                all_success = False

        # 标记整体状态
        # 所有分P成功才标记为done，任一分P失败标记为failed
        if all_success:
            await store.mark("video", bvid, "done", str(output_dir))
        else:
            await store.mark("video", bvid, "failed", str(output_dir))
        return all_success

    except Exception as e:
        console.print(f"[red]视频 {bvid} 处理失败: {e}[/red]")
        await store.mark("video", bvid, "failed", str(output_dir))
        return False


async def _download_subtitle_only(v: video.Video, cid: int, output_dir: Path, credential: Credential, store: DownloadStore, content_id: str) -> bool:
    ok = await _download_subtitle(v, cid, output_dir, credential)
    await store.mark("video", content_id, "done" if ok else "skipped", str(output_dir))
    return True


async def _download_audio_only(v: video.Video, cid: int, output_dir: Path, credential: Credential, store: DownloadStore, content_id: str, retries: int) -> bool:
    async def _get_audio_url():
        _, aus = await _get_streams(v, cid)
        return aus.url if aus else None

    temp_path = output_dir / "audio_temp.m4a"
    ok = await _download_with_refresh(_get_audio_url, temp_path, credential, retries, "音频")

    if ok:
        if convert_to_wav(temp_path, output_dir):
            await store.mark("video", content_id, "done", str(output_dir))
        else:
            await store.mark("video", content_id, "failed", str(output_dir))
            return False
    else:
        temp_path.unlink(missing_ok=True)
        await store.mark("video", content_id, "failed", str(output_dir))
    return ok


async def _download_video_only(v: video.Video, cid: int, output_dir: Path, credential: Credential, store: DownloadStore, content_id: str, retries: int) -> bool:
    async def _get_video_url():
        vs, _ = await _get_streams(v, cid)
        return vs.url if vs else None

    ok = await _download_with_refresh(_get_video_url, output_dir / "video.m4v", credential, retries, "视频")
    await store.mark("video", content_id, "done" if ok else "failed", str(output_dir))
    return ok


async def _download_full(v: video.Video, cid: int, output_dir: Path, credential: Credential, store: DownloadStore, content_id: str, retries: int) -> bool:
    vs, aus = await _get_streams(v, cid)
    if not vs and not aus:
        console.print("[red]未找到音视频流[/red]")
        await store.mark("video", content_id, "failed", str(output_dir))
        return False

    video_ok = None
    audio_ok = None

    if vs:
        async def _get_video_url():
            v2, _ = await _get_streams(v, cid)
            return v2.url if v2 else None
        video_ok = await _download_with_refresh(_get_video_url, output_dir / "video_temp.m4v", credential, retries, "视频")

    if aus:
        async def _get_audio_url():
            _, a2 = await _get_streams(v, cid)
            return a2.url if a2 else None
        audio_ok = await _download_with_refresh(_get_audio_url, output_dir / "audio_temp.m4a", credential, retries, "音频")

    if (vs and not video_ok) or (aus and not audio_ok):
        console.print("[red]音视频下载不完整[/red]")
        (output_dir / "video_temp.m4v").unlink(missing_ok=True)
        (output_dir / "audio_temp.m4a").unlink(missing_ok=True)
        await store.mark("video", content_id, "failed", str(output_dir))
        return False

    import ffmpeg
    try:
        v_temp = output_dir / "video_temp.m4v"
        a_temp = output_dir / "audio_temp.m4a"
        v_final = output_dir / "video.mp4"

        if video_ok and audio_ok:
            v_input = ffmpeg.input(str(v_temp))
            a_input = ffmpeg.input(str(a_temp))
            (
                ffmpeg
                .output(v_input, a_input, str(v_final), vcodec="copy", acodec="aac")
                .overwrite_output()
                .run(quiet=True)
            )
            v_temp.unlink(missing_ok=True)
            a_temp.unlink(missing_ok=True)
        elif video_ok:
            v_input = ffmpeg.input(str(v_temp))
            (
                ffmpeg
                .output(v_input, str(v_final), vcodec="copy", an=None)
                .overwrite_output()
                .run(quiet=True)
            )
            v_temp.unlink(missing_ok=True)
        elif audio_ok:
            convert_to_wav(a_temp, output_dir)
    except (ffmpeg.Error, FileNotFoundError) as e:
        console.print(f"[red]ffmpeg 处理失败: {e}[/red]")
        await store.mark("video", content_id, "failed", str(output_dir))
        return False

    await store.mark("video", content_id, "done", str(output_dir))
    return True


async def _download_single_part(
    v: video.Video,
    cid: int,
    output_dir: Path,
    video_mode: str,
    retries: int,
    store: DownloadStore,
    content_id: str,
    credential: Credential,
) -> bool:
    try:
        if video_mode == "subtitle-only":
            return await _download_subtitle_only(v, cid, output_dir, credential, store, content_id)
        if video_mode == "audio-only":
            return await _download_audio_only(v, cid, output_dir, credential, store, content_id, retries)
        if video_mode == "video-only":
            return await _download_video_only(v, cid, output_dir, credential, store, content_id, retries)
        return await _download_full(v, cid, output_dir, credential, store, content_id, retries)
    except Exception as e:
        console.print(f"[red]视频 {content_id} 处理失败: {e}[/red]")
        await store.mark("video", content_id, "failed", str(output_dir))
        return False
