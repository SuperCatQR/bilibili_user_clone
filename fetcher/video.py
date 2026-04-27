import json
import asyncio
from pathlib import Path

from bilibili_api import video, Credential
from bilibili_api.video import VideoDownloadURLDataDetecter, VideoStreamDownloadURL, AudioStreamDownloadURL
from rich.console import Console

from downloader import download_file
from store import DownloadStore
from utils import sanitize_filename
from config import DEFAULT_INTERVAL

console = Console()


async def _get_cid(v: video.Video) -> int:
    info = await v.get_info()
    pages = info.get("pages", [])
    if pages:
        return pages[0].get("cid", 0)
    return 0


async def _download_subtitle(v: video.Video, cid: int, output_dir: Path) -> bool:
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

        import aiohttp
        async with aiohttp.ClientSession() as session:
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
) -> bool:
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

        if video_mode == "subtitle-only":
            cid = info.get("pages", [{}])[0].get("cid", 0) if info.get("pages") else await _get_cid(v)
            ok = await _download_subtitle(v, cid, output_dir)
            await store.mark("video", bvid, "done" if ok else "skipped", str(output_dir))
            return True

        download_data = await v.get_download_url(cid=info.get("pages", [{}])[0].get("cid", 0) if info.get("pages") else await _get_cid(v))
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
                await store.mark("video", bvid, "failed", str(output_dir))
                return False
            ok = await download_file(audio_stream.url, output_dir / "audio.m4a", credential)
            if ok:
                await store.mark("video", bvid, "done", str(output_dir))
            else:
                await store.mark("video", bvid, "failed", str(output_dir))
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
                await store.mark("video", bvid, "failed", str(output_dir))
                return False
            ok = await download_file(video_stream.url, output_dir / "video.m4v", credential)
            if ok:
                await store.mark("video", bvid, "done", str(output_dir))
            else:
                await store.mark("video", bvid, "failed", str(output_dir))
            return ok

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
            await store.mark("video", bvid, "failed", str(output_dir))
            return False

        video_ok = True
        audio_ok = True

        if video_stream:
            video_ok = await download_file(video_stream.url, output_dir / "video_temp.m4v", credential)
        if audio_stream:
            audio_ok = await download_file(audio_stream.url, output_dir / "audio_temp.m4a", credential)

        if not video_ok or not audio_ok:
            console.print("[red]音视频下载不完整[/red]")
            await store.mark("video", bvid, "failed", str(output_dir))
            return False

        import ffmpeg
        try:
            v_input = ffmpeg.input(str(output_dir / "video_temp.m4v"))
            a_input = ffmpeg.input(str(output_dir / "audio_temp.m4a"))
            (
                ffmpeg
                .output(v_input, a_input, str(output_dir / "video.mp4"), vcodec="copy", acodec="copy")
                .overwrite_output()
                .run(quiet=True)
            )
            (output_dir / "video_temp.m4v").unlink(missing_ok=True)
            (output_dir / "audio_temp.m4a").unlink(missing_ok=True)
        except ffmpeg.Error as e:
            console.print(f"[red]ffmpeg 合流失败: {e}[/red]")
            await store.mark("video", bvid, "failed", str(output_dir))
            return False

        await store.mark("video", bvid, "done", str(output_dir))
        return True

    except Exception as e:
        console.print(f"[red]视频 {bvid} 处理失败: {e}[/red]")
        await store.mark("video", bvid, "failed", str(output_dir))
        return False
