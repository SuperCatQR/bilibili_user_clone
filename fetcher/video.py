"""
视频下载模块

支持5种下载模式：
- full: 下载视频轨+音频轨，合并为 video.mp4
- video-only: 仅下载视频轨 video.m4v
- audio-only: 仅下载音频轨，转码为 audio.wav (PCM 16bit 16kHz 单声道)
- subtitle-only: 仅下载字幕 subtitles.srt（中文优先）
- none: 仅保存 info.json

所有模式均输出 info.json 元数据文件。
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
from config import DEFAULT_RETRY

console = Console()


async def _download_subtitle(v: video.Video, cid: int, output_dir: Path, credential: Credential = None) -> bool:
    """
    下载视频字幕并保存为SRT格式。
    
    字幕语言优先级：中文（lan以zh开头）> 第一个可用字幕。
    无字幕时返回 False。
    
    Args:
        v: Video对象
        cid: 分P的CID
        output_dir: 输出目录
        credential: 认证凭据（用于携带Cookie/Referer头）
    """
    try:
        subtitle_info = await v.get_subtitle(cid=cid)
        subtitles = subtitle_info.get("subtitles", [])
        if not subtitles:
            console.print("[yellow]无字幕[/yellow]")
            return False

        zh_sub = None
        for s in subtitles:
            lang = s.get("lan", "")
            if lang.startswith("zh"):
                zh_sub = s
                break
        if not zh_sub:
            zh_sub = subtitles[0]

        sub_url = zh_sub.get("subtitle_url", "")
        if not sub_url:
            return False
        if sub_url.startswith("//"):
            sub_url = "https:" + sub_url

        # 使用downloader模块的_build_headers构建认证头
        from downloader import _build_headers
        headers = _build_headers(credential) if credential else {}
        
        import aiohttp
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(sub_url) as resp:
                if resp.status != 200:
                    return False
                sub_data = await resp.json(content_type=None)

        body = sub_data.get("body", [])
        srt_lines = []
        for i, item in enumerate(body, 1):
            start = item.get("from", 0)
            end = item.get("to", 0)
            content = item.get("content", "")
            srt_lines.append(str(i))
            srt_lines.append(f"{_format_srt_time(start)} --> {_format_srt_time(end)}")
            srt_lines.append(content)
            srt_lines.append("")

        srt_path = output_dir / "subtitles.srt"
        srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
        return True
    except Exception as e:
        console.print(f"[red]字幕下载失败: {e}[/red]")
        return False


def _format_srt_time(seconds: float) -> str:
    """将秒数格式化为SRT时间轴 HH:MM:SS,mmm。"""
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
    """
    bvid = item.content_id
    title = item.title
    dir_name = sanitize_filename(f"{bvid} - {title}")
    output_dir = base_dir / "videos" / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)

    v = video.Video(bvid=bvid, credential=credential)

    try:
        info = await v.get_info()
        info_path = output_dir / "info.json"
        info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

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
            
            # 为每个分P创建子目录
            part_dir = output_dir / sanitize_filename(f"P{i} - {part_title}")
            part_dir.mkdir(parents=True, exist_ok=True)
            
            console.print(f"[cyan]下载分P {i}/{len(pages)}: {part_title}[/cyan]")
            success = await _download_single_part(v, cid, part_dir, video_mode, retries, store, f"{bvid}_P{i}", credential)
            if not success:
                all_success = False
        
        # 标记整体状态
        if all_success:
            await store.mark("video", bvid, "done", str(output_dir))
        else:
            await store.mark("video", bvid, "failed", str(output_dir))
        return all_success

    except Exception as e:
        console.print(f"[red]视频 {bvid} 处理失败: {e}[/red]")
        await store.mark("video", bvid, "failed", str(output_dir))
        return False


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
    """
    下载单个分P的视频内容。
    
    Args:
        v: Video对象
        cid: 分P的CID
        output_dir: 输出目录
        video_mode: 下载模式
        retries: 重试次数
        store: 存储对象
        content_id: 内容ID（用于标记状态）
        credential: 认证凭据
    
    Returns:
        True表示成功，False表示失败
    """
    try:
        if video_mode == "subtitle-only":
            ok = await _download_subtitle(v, cid, output_dir, credential)
            await store.mark("video", content_id, "done" if ok else "skipped", str(output_dir))
            return True

        download_data = await v.get_download_url(cid=cid)
        detecter = VideoDownloadURLDataDetecter(download_data)

        if video_mode == "audio-only":
            streams = detecter.detect_best_streams()
            audio_stream = None
            for s in streams:
                if isinstance(s, AudioStreamDownloadURL):
                    audio_stream = s
                    break
            if not audio_stream:
                console.print("[red]未找到音频流[/red]")
                await store.mark("video", content_id, "failed", str(output_dir))
                return False
            temp_path = output_dir / "audio_temp.m4a"
            ok = await download_file(audio_stream.url, temp_path, credential, retries=retries)
            if ok:
                import ffmpeg
                try:
                    (
                        ffmpeg
                        .input(str(temp_path))
                        .output(str(output_dir / "audio.wav"), acodec="pcm_s16le", ar=16000, ac=1)
                        .overwrite_output()
                        .run(quiet=True)
                    )
                    temp_path.unlink(missing_ok=True)
                except (ffmpeg.Error, FileNotFoundError) as e:
                    console.print(f"[red]音频转WAV失败: {e}[/red]")
                    temp_path.unlink(missing_ok=True)
                    await store.mark("video", content_id, "failed", str(output_dir))
                    return False
                await store.mark("video", content_id, "done", str(output_dir))
            else:
                temp_path.unlink(missing_ok=True)
                await store.mark("video", content_id, "failed", str(output_dir))
            return ok

        if video_mode == "video-only":
            streams = detecter.detect_best_streams()
            video_stream = None
            for s in streams:
                if isinstance(s, VideoStreamDownloadURL):
                    video_stream = s
                    break
            if not video_stream:
                console.print("[red]未找到视频流[/red]")
                await store.mark("video", content_id, "failed", str(output_dir))
                return False
            ok = await download_file(video_stream.url, output_dir / "video.m4v", credential, retries=retries)
            if ok:
                await store.mark("video", content_id, "done", str(output_dir))
            else:
                await store.mark("video", content_id, "failed", str(output_dir))
            return ok

        # full模式：下载视频流和音频流并合并
        streams = detecter.detect_best_streams()
        video_stream = None
        audio_stream = None
        for s in streams:
            if isinstance(s, VideoStreamDownloadURL) and video_stream is None:
                video_stream = s
            if isinstance(s, AudioStreamDownloadURL) and audio_stream is None:
                audio_stream = s

        if not video_stream and not audio_stream:
            console.print("[red]未找到音视频流[/red]")
            await store.mark("video", content_id, "failed", str(output_dir))
            return False

        video_ok = None
        audio_ok = None

        if video_stream:
            video_ok = await download_file(video_stream.url, output_dir / "video_temp.m4v", credential, retries=retries)
        if audio_stream:
            audio_ok = await download_file(audio_stream.url, output_dir / "audio_temp.m4a", credential, retries=retries)

        if (video_stream and not video_ok) or (audio_stream and not audio_ok):
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
                a_input = ffmpeg.input(str(a_temp))
                (
                    ffmpeg
                    .output(a_input, str(output_dir / "audio.wav"), acodec="pcm_s16le", ar=16000, ac=1)
                    .overwrite_output()
                    .run(quiet=True)
                )
                a_temp.unlink(missing_ok=True)
        except (ffmpeg.Error, FileNotFoundError) as e:
            console.print(f"[red]ffmpeg 处理失败: {e}[/red]")
            await store.mark("video", content_id, "failed", str(output_dir))
            return False

        await store.mark("video", content_id, "done", str(output_dir))
        return True

    except Exception as e:
        console.print(f"[red]视频 {content_id} 处理失败: {e}[/red]")
        await store.mark("video", content_id, "failed", str(output_dir))
        return False
