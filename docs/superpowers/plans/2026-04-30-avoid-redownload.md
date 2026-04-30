# 避免重复下载：签名文件检查 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在每个下载函数入口检查签名文件，避免已有文件被重复下载。

**Architecture:** 新增 `check_signature()` 工具函数，4个下载函数在 `output_dir.mkdir()` 后、网络请求前调用。签名文件按内容类型和模式定义，任一签名文件存在即视为已完成。

**Tech Stack:** Python 3.13, pathlib

---

### Task 1: 新增 `check_signature()` 到 `utils.py`

**Files:**
- Modify: `utils.py`

- [ ] **Step 1: 在 `utils.py` 末尾添加 `check_signature()` 函数**

```python
def check_signature(output_dir: Path, *signatures: str) -> bool:
    """
    检查 output_dir 下是否存在任一签名文件。

    签名文件是每种下载模式产出必需文件的集合。
    任一签名文件存在即视为该内容已下载完成。
    用 any 而非 all：不同流可用性产出不同文件（如 full 模式下
    有视频轨产出 video.mp4，仅音频轨产出 audio.wav）。

    Args:
        output_dir: 输出目录
        *signatures: 签名文件名列表

    Returns:
        True 表示至少一个签名文件存在
    """
    return any((output_dir / s).exists() for s in signatures)
```

注意：需要在 `utils.py` 顶部添加 `from pathlib import Path` 导入（如果尚未存在）。

- [ ] **Step 2: 语法检查**

Run: `uv run python -m py_compile utils.py`
Expected: 无输出（编译通过）

- [ ] **Step 3: 提交**

```bash
git add utils.py
git commit -m "feat: 添加 check_signature() 签名文件检查函数"
```

---

### Task 2: `download_video()` 添加签名文件检查

**Files:**
- Modify: `fetcher/video.py`

- [ ] **Step 1: 在 `fetcher/video.py` 顶部添加 import**

在现有 import 区添加：

```python
from utils import sanitize_filename, check_signature
```

替换原来的：

```python
from utils import sanitize_filename
```

- [ ] **Step 2: 在 `download_video()` 函数中，`output_dir.mkdir(parents=True, exist_ok=True)` 之后、`v = video.Video(...)` 之前，添加签名文件检查**

```python
    sig_map = {
        "full": ("video.mp4", "audio.wav"),
        "audio-only": ("audio.wav",),
        "video-only": ("video.m4v",),
        "subtitle-only": ("subtitles.srt",),
        "none": ("info.json",),
    }
    sigs = sig_map.get(video_mode, ())
    if sigs and check_signature(output_dir, *sigs):
        console.print(f"[yellow]已存在，跳过[/yellow]")
        await store.mark("video", bvid, "done", str(output_dir))
        return True
```

这段代码插入在 `output_dir.mkdir(parents=True, exist_ok=True)` 之后，`v = video.Video(bvid=bvid, credential=credential)` 之前。

- [ ] **Step 3: 语法检查**

Run: `uv run python -m py_compile fetcher/video.py`
Expected: 无输出（编译通过）

- [ ] **Step 4: 提交**

```bash
git add fetcher/video.py
git commit -m "feat: download_video 入口签名文件检查，避免重复下载"
```

---

### Task 3: `download_audio()` 添加签名文件检查

**Files:**
- Modify: `fetcher/audio.py`

- [ ] **Step 1: 修改 import**

将：

```python
from utils import sanitize_filename
```

改为：

```python
from utils import sanitize_filename, check_signature
```

- [ ] **Step 2: 在 `download_audio()` 函数中，`output_dir.mkdir(parents=True, exist_ok=True)` 之后、`a = audio.Audio(auid=auid, credential=credential)` 之前，添加**

```python
    if check_signature(output_dir, "audio.wav"):
        console.print(f"[yellow]已存在，跳过[/yellow]")
        await store.mark("audio", str(auid), "done", str(output_dir))
        return True
```

- [ ] **Step 3: 语法检查**

Run: `uv run python -m py_compile fetcher/audio.py`
Expected: 无输出（编译通过）

- [ ] **Step 4: 提交**

```bash
git add fetcher/audio.py
git commit -m "feat: download_audio 入口签名文件检查，避免重复下载"
```

---

### Task 4: `download_article()` 添加签名文件检查

**Files:**
- Modify: `fetcher/article.py`

- [ ] **Step 1: 修改 import**

将：

```python
from utils import sanitize_filename
```

改为：

```python
from utils import sanitize_filename, check_signature
```

- [ ] **Step 2: 在 `download_article()` 函数中，`output_dir.mkdir(parents=True, exist_ok=True)` 之后、`a = article.Article(cvid=cvid, credential=credential)` 之前，添加**

```python
    if check_signature(output_dir, "article.md", "info.json"):
        console.print(f"[yellow]已存在，跳过[/yellow]")
        await store.mark("article", str(cvid), "done", str(output_dir))
        return True
```

注意：article 使用 `any` 语义——`article.md` 或 `info.json` 任一存在即跳过。无正文时只保存 info.json，有正文时两者都有。

- [ ] **Step 3: 语法检查**

Run: `uv run python -m py_compile fetcher/article.py`
Expected: 无输出（编译通过）

- [ ] **Step 4: 提交**

```bash
git add fetcher/article.py
git commit -m "feat: download_article 入口签名文件检查，避免重复下载"
```

---

### Task 5: `download_dynamic()` 添加签名文件检查

**Files:**
- Modify: `fetcher/dynamic.py`

- [ ] **Step 1: 修改 import**

将：

```python
from utils import sanitize_filename
```

改为：

```python
from utils import sanitize_filename, check_signature
```

- [ ] **Step 2: 在 `download_dynamic()` 函数中，`output_dir.mkdir(parents=True, exist_ok=True)` 之后、`json_path = output_dir / "dynamic.json"` 之前，添加**

```python
    if check_signature(output_dir, "dynamic.json"):
        console.print(f"[yellow]已存在，跳过[/yellow]")
        await store.mark("dynamic", dynamic_id, "done", str(output_dir))
        return True
```

- [ ] **Step 3: 语法检查**

Run: `uv run python -m py_compile fetcher/dynamic.py`
Expected: 无输出（编译通过）

- [ ] **Step 4: 提交**

```bash
git add fetcher/dynamic.py
git commit -m "feat: download_dynamic 入口签名文件检查，避免重复下载"
```

---

### Task 6: 全量编译验证

**Files:** 无修改

- [ ] **Step 1: 对所有 Python 文件执行 py_compile**

Run: `uv run python -m py_compile utils.py; uv run python -m py_compile fetcher/video.py; uv run python -m py_compile fetcher/audio.py; uv run python -m py_compile fetcher/article.py; uv run python -m py_compile fetcher/dynamic.py; uv run python -m py_compile main.py; uv run python -m py_compile store.py; uv run python -m py_compile downloader.py; uv run python -m py_compile ffmpeg_utils.py`
Expected: 全部无输出（编译通过）

- [ ] **Step 2: 推送所有提交**

```bash
git push origin master
```
