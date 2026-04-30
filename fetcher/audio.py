"""
音频下载模块

从B站音频区下载音频，保存为 info.json + audio.wav。

下载流程：
1. 获取音频元数据（get_info）
2. 获取CDN下载URL（get_download_url）
3. 下载原始m4a到临时文件
4. ffmpeg转码为PCM 16bit 16kHz单声道WAV
5. 删除临时文件

CDN地址获取：
- get_download_url() 返回值可能有两种格式：
  - {"cdns": ["url1", "url2", ...]}  （列表）
  - {"cdn": "url1"}  （单值）
- 代码兼容两种格式

音频转码参数说明：
- acodec="pcm_s16le": PCM 16bit 小端序编码（无压缩）
- ar=16000: 采样率16kHz（语音识别标准采样率）
- ac=1: 单声道（音频区内容通常是单声道）
"""

import json
from pathlib import Path

from bilibili_api import audio, Credential
from rich.console import Console

from downloader import download_file
from store import DownloadStore
from utils import sanitize_filename, check_signature
from ffmpeg_utils import convert_to_wav
from config import DEFAULT_RETRY

console = Console()


async def download_audio(
    item,
    uid: int,
    credential: Credential,
    store: DownloadStore,
    base_dir: Path,
    retries: int = DEFAULT_RETRY,
) -> bool:
    """
    下载单个音频。

    先下载原始m4a到临时文件，再用ffmpeg转码为WAV后删除临时文件。
    转码失败时标记为 failed，下次运行会重试。

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
        auid = int(item.content_id)
    except (ValueError, TypeError):
        console.print(f"[red]音频 content_id 无效: {item.content_id}[/red]")
        await store.mark("audio", str(item.content_id), "failed", None)
        return False
    title = item.title

    # 构建输出目录：audios/AU<号> - <标题>/
    dir_name = sanitize_filename(f"AU{auid} - {title}")
    output_dir = base_dir / "audios" / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)

    if check_signature(output_dir, "audio.wav"):
        console.print(f"[yellow]已存在，跳过[/yellow]")
        await store.mark("audio", str(auid), "done", str(output_dir))
        return True

    # 创建Audio对象
    a = audio.Audio(auid=auid, credential=credential)

    try:
        # 获取音频元数据
        info = await a.get_info()

        # 保存info.json
        info_path = output_dir / "info.json"
        info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

        # 获取下载URL
        dl_data = await a.get_download_url()

        # 兼容两种API返回格式
        # 格式1: {"cdns": ["url1", "url2"]}
        cdns = dl_data.get("cdns", []) if isinstance(dl_data, dict) else []
        if not cdns:
            # 格式2: {"cdn": "url1"}
            cdn = dl_data.get("cdn", "") if isinstance(dl_data, dict) else ""
            if cdn:
                cdns = [cdn]

        # 检查是否有可用的下载URL
        if not cdns or not cdns[0]:
            console.print(f"[red]未找到音频下载URL: AU{auid}[/red]")
            await store.mark("audio", str(auid), "failed", str(output_dir))
            return False

        # 下载m4a临时文件
        temp_path = output_dir / "audio_temp.m4a"
        ok = await download_file(cdns[0], temp_path, credential, retries=retries)

        if ok:
            if convert_to_wav(temp_path, output_dir):
                await store.mark("audio", str(auid), "done", str(output_dir))
            else:
                await store.mark("audio", str(auid), "failed", str(output_dir))
                return False
        else:
            await store.mark("audio", str(auid), "failed", str(output_dir))
        return ok

    except Exception as e:
        console.print(f"[red]音频 AU{auid} 处理失败: {e}[/red]")
        await store.mark("audio", str(auid), "failed", str(output_dir))
        return False
