"""
ffmpeg 工具模块

封装常用的 ffmpeg 转码操作，消除 video.py / audio.py 中的重复调用。

convert_to_wav:
  m4a 临时文件 → PCM 16bit 16kHz 单声道 WAV → 删除临时文件
  调用者只需关心输入路径和输出目录，转码细节由此模块封装。
"""

import asyncio
import ffmpeg
from pathlib import Path

from rich.console import Console

console = Console()

# ffmpeg 重试次数
FFMPEG_RETRY_COUNT = 2


def convert_to_wav(temp_path: Path, output_dir: Path) -> bool:
    """
    将音频临时文件转码为 PCM 16bit 16kHz 单声道 WAV，成功后删除临时文件。

    ffmpeg 调用链：
      .input(temp_path)           → 读取输入文件（自动检测容器/编码格式）
      .output(audio.wav,          → 输出路径
        acodec="pcm_s16le",       → PCM signed 16-bit little-endian（无压缩原始波形）
        ar=16000,                 → 采样率 16kHz（语音识别标准采样率，Nyquist=8kHz）
        ac=1)                     → 单声道（降混为1通道，减少50%数据量）
      .overwrite_output()         → 覆盖已存在文件（避免交互式提示阻塞进程）
      .run(quiet=True)            → 执行ffmpeg命令，静默stderr输出

    内存模型：
      ffmpeg 是外部子进程，通过管道与Python进程通信。
      .run() 内部调用 subprocess.Popen，ffmpeg 进程独立于 Python 事件循环。
      数据流：磁盘→ffmpeg进程内存→磁盘，Python进程不持有音频数据。
      因此对 asyncio 事件循环无阻塞风险（IO由子进程完成）。

    错误场景：
      - ffmpeg.Error: ffmpeg 进程返回非零退出码（编码参数错误/输入文件损坏）
      - FileNotFoundError: ffmpeg 二进制不在 PATH 上

    Args:
        temp_path: 临时音频文件路径（m4a/mp4等ffmpeg可识别格式）
        output_dir: 输出目录，WAV文件保存为 output_dir/audio.wav

    Returns:
        True 转码成功（临时文件已删除），False 转码失败（临时文件已删除）
    """
    wav_path = output_dir / "audio.wav"

    for attempt in range(FFMPEG_RETRY_COUNT):
        try:
            (
                ffmpeg
                .input(str(temp_path))
                .output(str(wav_path), acodec="pcm_s16le", ar=16000, ac=1)
                .overwrite_output()
                .run(quiet=True)
            )
            temp_path.unlink(missing_ok=True)
            return True
        except ffmpeg.Error as e:
            if attempt < FFMPEG_RETRY_COUNT - 1:
                console.print(f"[yellow]ffmpeg 转码失败，重试中... ({attempt + 1}/{FFMPEG_RETRY_COUNT})[/yellow]")
                # 删除可能的不完整输出文件
                if wav_path.exists():
                    try:
                        wav_path.unlink()
                    except OSError:
                        pass
            else:
                console.print(f"[red]音频转WAV失败: {e}[/red]")
        except FileNotFoundError:
            console.print("[red]ffmpeg 未找到，请安装 ffmpeg[/red]")
            temp_path.unlink(missing_ok=True)
            return False

    temp_path.unlink(missing_ok=True)
    return False
