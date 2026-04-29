"""
音频下载模块

从B站音频区下载音频，保存为 info.json + audio.wav。
下载流程：获取元数据 → 获取CDN下载URL → 下载m4a临时文件 →
ffmpeg转码为PCM 16bit 44100Hz立体声WAV → 删除临时文件。

CDN地址从 get_download_url() 返回值中提取，兼容 cdns 列表和 cdn 单值两种格式。
"""

import json
import ffmpeg
from pathlib import Path

from bilibili_api import audio, Credential
from rich.console import Console

from downloader import download_file
from store import DownloadStore
from utils import sanitize_filename

console = Console()


async def download_audio(
    item,
    uid: int,
    credential: Credential,
    store: DownloadStore,
    base_dir: Path,
) -> bool:
    """
    下载单个音频。
    
    先下载原始m4a到临时文件，再用ffmpeg转码为WAV后删除临时文件。
    转码失败时标记为 failed，下次运行会重试。
    """
    auid = int(item.content_id)
    title = item.title
    dir_name = sanitize_filename(f"AU{auid} - {title}")
    output_dir = base_dir / "audios" / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)

    a = audio.Audio(auid=auid, credential=credential)

    try:
        info = await a.get_info()
        info_path = output_dir / "info.json"
        info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

        dl_data = await a.get_download_url()
        cdns = dl_data.get("cdns", []) if isinstance(dl_data, dict) else []
        if not cdns:
            cdn = dl_data.get("cdn", "") if isinstance(dl_data, dict) else ""
            if cdn:
                cdns = [cdn]

        if not cdns or not cdns[0]:
            console.print(f"[red]未找到音频下载URL: AU{auid}[/red]")
            await store.mark("audio", str(auid), "failed", str(output_dir))
            return False

        temp_path = output_dir / "audio_temp.m4a"
        ok = await download_file(cdns[0], temp_path, credential)
        if ok:
            try:
                (
                    ffmpeg
                    .input(str(temp_path))
                    .output(str(output_dir / "audio.wav"), acodec="pcm_s16le", ar=44100, ac=2)
                    .overwrite_output()
                    .run(quiet=True)
                )
                temp_path.unlink(missing_ok=True)
            except ffmpeg.Error as e:
                console.print(f"[red]音频转WAV失败: {e}[/red]")
                await store.mark("audio", str(auid), "failed", str(output_dir))
                return False
            await store.mark("audio", str(auid), "done", str(output_dir))
        else:
            await store.mark("audio", str(auid), "failed", str(output_dir))
        return ok

    except Exception as e:
        console.print(f"[red]音频 AU{auid} 处理失败: {e}[/red]")
        await store.mark("audio", str(auid), "failed", str(output_dir))
        return False
