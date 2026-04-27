---
name: bilibili-user-clone-skill
description: Clone all public content (videos, audios, articles, dynamics) from a Bilibili user. Use when downloading or archiving a B站 user's complete content, resuming interrupted downloads, filtering by time range or content type, or converting Bilibili content to local structured data. Triggers on "clone bilibili user", "download bilibili videos", "backup bilibili content", "bilibili archive", "B站用户内容克隆", "bilibili clone".
---

# Bilibili User Clone

Clone all public content from a Bilibili user to local structured data with SQLite-based resume.

Based on [bilibili-api](https://github.com/Nemo2011/bilibili-api) and [bilibili-cli](https://github.com/public-clis/bilibili-cli).

## Quick Start

```bash
uv sync

python main.py clone 946974                           # All content, video full mode
python main.py clone 946974 --hours 48                 # Recent 48h only
python main.py clone 946974 --types video,dynamic --video-mode subtitle-only
python main.py clone 946974 --types audio,article
```

## CLI

| Parameter | Default | Description |
|-----------|---------|-------------|
| `UID` | required | Target user UID |
| `--output` | `./output` | Output directory |
| `--types` | `video,audio,article,dynamic` | Comma-separated content types |
| `--video-mode` | `full` | `full` / `video-only` / `audio-only` / `subtitle-only` / `none` |
| `--interval` | `3` | Request interval (seconds) |
| `--retry` | `3` | API retry count |
| `--hours` | no limit | Only download content within N hours |

Video modes: `full` (mux via ffmpeg), `video-only` (`.m4v`), `audio-only` (`.m4a`), `subtitle-only` (`.srt`, Chinese priority), `none` (metadata only).

## Output Structure

```
output/<UID>/
├── info.json                         # User profile snapshot
├── videos/<BV号> - <title>/
│   ├── video.mp4|video.m4v|audio.m4a|subtitles.srt
│   └── info.json
├── audios/AU<号> - <title>/
│   ├── audio.m4a
│   └── info.json
├── articles/cv<号> - <title>/
│   ├── article.md                    # HTML→Markdown converted
│   ├── images/img_N.ext              # Embedded images downloaded
│   └── info.json
└── dynamics/<dynamic_id>/
    ├── dynamic.json                  # Full raw API response
    ├── info.json                     # Summary: id + type + embedded IDs
    └── images/                       # Extracted images
```

For complete field-level documentation of all JSON files, see [references/output_schema.md](references/output_schema.md).

## Architecture

```
Auth (saved cred → QR login)
  → save user info
  → enumerate by type (paginate + --hours filter + skip done)
  → download per item (media + metadata → mark status)
  → SQLite resume store
```

### Rate Limiting

- Sequential downloads, 3s interval between items
- Every 50 items: staggered pause (5s→10s→15s→20s cycle)
- 412 / network error: exponential backoff `5×2^attempt` (cap 300s)

### Resume

SQLite DB at `~/.bilibili-cli/downloads.db`, PK `(uid, content_type, content_id)`:
- `done` → skipped on re-run
- `failed` → retried on re-run

### Auth

Shared credential with bilibili-cli at `~/.bilibili-cli/credential.json` (7-day TTL). Falls back to QR code login.

## Module Map

| Module | Role |
|--------|------|
| `main.py` | Click CLI, async pipeline, download report |
| `config.py` | Constants: intervals, retries, paths, valid types/modes |
| `auth.py` | 3-tier auth: saved cred → QR; auto-refresh buvid3/buvid4 |
| `store.py` | `DownloadStore` — SQLite is_done/mark |
| `downloader.py` | Async file downloader, `//` URL fix, Cookie/Referer, 412 backoff |
| `utils.py` | `sanitize_filename()` — strip illegal chars, truncate to 200 |
| `article_converter.py` | HTML→Markdown async converter with local image download |
| `fetcher/enumerator.py` | 4 enumerate fns, `_retry_api()`, `--hours` early-termination |
| `fetcher/video.py` | 5 video modes; ffmpeg mux for `full` |
| `fetcher/audio.py` | CDN URL extraction from `get_download_url()` |
| `fetcher/article.py` | `get_detail()` → article_converter → `.md` |
| `fetcher/dynamic.py` | Raw JSON + image extraction + embedded content ID dispatch |

## Known SDK Quirks (bilibili-api ≥16.0)

- `get_audios` returns `data` as list (not dict)
- `get_articles` uses `articles` key
- `get_dynamics_new`: `items`/`id_str`/`type`/`offset`/`has_more` at top level
- `Audio.get_download_url()` returns flat dict with `cdns` at top level
- `Article.get_detail()` (not `get_all()`)
- `pub_ts` in dynamics is **string** — cast with `int()`
- ffmpeg-python: use `ffmpeg.output(v_input, a_input, path)` not chained `.input().input().output()`

## Dependencies

Python 3.13+, bilibili-api-python ≥16.0, aiohttp, ffmpeg-python, beautifulsoup4, click, rich, qrcode, aiosqlite. System: ffmpeg (only for `full` video mode).
