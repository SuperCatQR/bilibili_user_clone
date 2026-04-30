# 避免重复下载：签名文件检查

## 问题

当前去重仅依赖 `DownloadStore.is_done()`（SQLite 数据库）。如果用户删除数据库但保留 output 目录，或换机器但拷贝了 output 目录，已完成的内容会被重复下载。

## 方案

在每个 `download_*` 函数入口处检查签名文件——该模式下必需的输出文件是否已存在于 `output_dir`。存在则跳过并标记 done。

## 签名文件定义

| 内容类型 | 模式 | 签名文件 | 说明 |
|----------|------|----------|------|
| video | full | `video.mp4` 或 `audio.wav` | 有视频轨→video.mp4；仅音频轨→audio.wav |
| video | audio-only | `audio.wav` | |
| video | video-only | `video.m4v` | |
| video | subtitle-only | `subtitles.srt` | 无字幕时无此文件，标记 skipped |
| video | none | `info.json` | 仅元数据 |
| audio | - | `audio.wav` | |
| article | - | `article.md` 或 `info.json` | 无正文时只有 info.json |
| dynamic | - | `dynamic.json` | |

## 实现细节

### `utils.py` 新增 `check_signature()`

```python
def check_signature(output_dir: Path, *signatures: str) -> bool:
    return any((output_dir / s).exists() for s in signatures)
```

### 各下载函数的调用位置

在 `output_dir.mkdir()` 之后、网络请求（API 调用/下载）之前。

### 多分P视频

不做逐P文件检查，依赖数据库去重。原因：分P目录名依赖 `info.get("pages")` 中的 `part` 字段，在获取元数据前无法确定。

### 不做的事

- 不修改数据库 schema
- 不做文件完整性/大小校验
- 不处理多分P视频的逐P文件检查

## 影响范围

- `utils.py`：新增 `check_signature()`
- `fetcher/video.py`：`download_video()` 入口检查
- `fetcher/audio.py`：`download_audio()` 入口检查
- `fetcher/article.py`：`download_article()` 入口检查
- `fetcher/dynamic.py`：`download_dynamic()` 入口检查
