# bilibili-user-clone

克隆B站用户的所有内容：视频、音频区、专栏、动态。

基于 [bilibili-api](https://github.com/Nemo2011/bilibili-api) 和 [bilibili-cli](https://github.com/public-clis/bilibili-cli)。

本项目完成**数据收集**这一步，将用户的全部公开内容下载到本地，并以结构化的目录和 JSON 文件保存。输出的文件结构设计为便于下游数据处理（数据分析、内容检索、AI 训练等）。

## 快速开始

### 安装

推荐使用 [uv](https://docs.astral.sh/uv/) 管理项目：

```bash
# 安装 uv（如果还没有）
# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# 克隆项目并安装依赖
git clone https://github.com/SuperCatQR/bilibili_user_clone.git
cd bilibili_user_clone
uv sync
```

也可以使用 pip：

```bash
pip install -e .
```

需要系统已安装 [ffmpeg](https://ffmpeg.org/)（`full` 模式合流、`audio-only` 模式转码 WAV 均需要；`video-only` 模式不需要）。

### 认证

首次运行会自动启动 QR 码登录，用B站手机 APP 扫码即可。凭据保存在 `~/.bilibili-cli/credential.json`，7 天内免重新登录。

### 使用

```bash
# 克隆用户全部内容（视频full模式）
uv run main.py clone 946974

# 只下载最近 48 小时发布的内容
uv run main.py clone 946974 --hours 48

# 只下载视频的字幕和动态
uv run main.py clone 946974 --types video,dynamic --video-mode subtitle-only

# 只下载音频和专栏
uv run main.py clone 946974 --types audio,article

# 视频(仅字幕) + 动态
uv run main.py clone 946974 --types video,dynamic --video-mode subtitle-only
```

## CLI 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `UID` | 必填 | 目标用户 UID |
| `--output` | `./output` | 输出目录 |
| `--types` | `video,audio,article,dynamic` | 内容类型，逗号分隔 |
| `--video-mode` | `full` | 视频下载模式（见下表） |
| `--interval` | `3` | 请求间隔（秒） |
| `--retry` | `3` | API 请求重试次数 |
| `--hours` | 不限 | 只下载指定小时内发布的内容 |

## 视频模式

| 模式 | 产出文件 | 说明 |
|------|----------|------|
| `full` | `video.mp4` | 视频音频合流（默认，需要 ffmpeg） |
| `video-only` | `video.m4v` | 仅视频轨 |
| `audio-only` | `audio.wav` | 仅音频轨（PCM 16bit 16kHz 单声道，需要 ffmpeg） |
| `subtitle-only` | `subtitles.srt` | 仅字幕（中文优先） |
| `none` | — | 跳过视频下载，仅保存 `info.json` |

`--types` 不含 `video` 时，`--video-mode` 无效。

## 实现逻辑

### 整体流水线

```
认证(QR/已保存凭据)
  -> 保存用户资料(info.json)
  -> 逐类型枚举(分页遍历 + 时间过滤 + 断点跳过)
  -> 逐项下载(媒体文件 + 元数据)
  -> 记录状态到 SQLite
```

### 关键设计

- **顺序请求**：所有下载逐项串行，间隔 3 秒，每 50 项阶梯暂停（5s→10s→15s→20s 循环），避免触发 412 限速
- **指数退避**：遇到 412 或网络异常时，等待时间按 `5 x 2^attempt` 秒递增（上限 300 秒）
- **SQLite 断点续传**：每项下载完成后记录状态（`done`/`failed`/`skipped`），中断后重新运行自动跳过已完成项
- **时间过滤提前终止**：`--hours` 参数在枚举阶段生效，遇到整页内容都早于截止时间时直接终止分页，不会遍历全部历史
- **凭证复用**：凭据保存在 `~/.bilibili-cli/credential.json`，支持多项目共享

## 模块说明

| 文件 | 功能 |
|------|------|
| `main.py` | CLI 入口，Click 命令定义，编排完整的枚举->下载流水线，输出下载报告 |
| `config.py` | 全局常量：请求间隔、重试策略、文件名长度、凭证路径、数据库路径、合法类型/模式 |
| `auth.py` | 三级认证：已保存凭据 -> QR 码登录；凭据 7 天过期自动重新登录；自动补充 buvid3/buvid4 |
| `store.py` | `DownloadStore` 类，SQLite 断点续传：`is_done()` 查询、`mark()` 记录状态，主键 `(uid, content_type, content_id)` |
| `downloader.py` | 异步文件下载器，自动补全 `//` 前缀 URL，携带 Cookie/Referer，412 指数退避 |
| `utils.py` | `sanitize_filename()`：去除非法字符、压缩空白、截断超长文件名 |
| `article_converter.py` | HTML -> Markdown 异步转换器，递归处理标题/段落/代码块/列表/图片等元素，图片下载到本地 `images/` 目录 |
| `fetcher/enumerator.py` | 四个枚举函数，分页遍历 API 返回 `DownloadItem` 列表，支持 `--hours` 时间过滤和 `_retry_api()` 重试 |
| `fetcher/video.py` | 视频下载，5 种模式：full（ffmpeg 合流）、video-only、audio-only、subtitle-only（SRT 格式）、none |
| `fetcher/audio.py` | 音频下载，从 `get_download_url()` 响应中提取 CDN 地址 |
| `fetcher/article.py` | 专栏下载，调用 `get_detail()` 获取内容，经 `article_converter` 转为 Markdown |
| `fetcher/dynamic.py` | 动态下载，保存原始 JSON + 提取图片 + 识别嵌入式内容（关联视频/专栏/音频的 ID） |

## 输出目录结构与文件详解

### 总览

```
output/<UID>/
+-- info.json                    # 用户资料快照
+-- videos/
|   +-- <BV号> - <标题>/
|       +-- video.mp4            # full 模式（视频音频合流）
|       +-- audio.wav            # full / audio-only 模式（PCM 16bit 16kHz 单声道）
|       +-- subtitles.srt        # subtitle-only 模式
|       +-- info.json            # 视频元数据（所有模式均输出）
+-- audios/
|   +-- AU<号> - <标题>/
|       +-- audio.wav             # 音频文件（PCM 16bit WAV）
|       +-- info.json            # 音频元数据
+-- articles/
|   +-- cv<号> - <标题>/
|       +-- article.md           # 转换后的 Markdown 正文
|       +-- images/              # 文章内嵌图片
|       |   +-- img_1.jpg
|       |   +-- img_2.png
|       |   +-- ...
|       +-- info.json            # 专栏元数据（含原始 HTML）
+-- dynamics/
    +-- <动态ID>/
        +-- dynamic.json         # 动态完整原始数据
        +-- info.json            # 动态摘要（ID + 类型 + 关联内容）
        +-- images/              # 动态中的图片（部分类型）
            +-- img_1.jpg
            +-- ...
```

> 目录名中的 `<BV号> - <标题>` 等经过 `sanitize_filename()` 处理，去除 `\ / : * ? \" < > |` 等非法字符，截断至 200 字符。

---

### 根级 `info.json` -- 用户资料

来自 `User.get_user_info()` 的完整响应，关键字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `mid` | int | 用户 UID |
| `name` | str | 昵称 |
| `sex` | str | 性别 |
| `face` | str | 头像 URL |
| `sign` | str | 签名 |
| `rank` | int | 排序等级 |
| `level` | int | 等级（0-6） |
| `jointime` | int | 注册时间（非公开用户为 0） |
| `moral` | int | 节操值 |
| `silence` | int | 封禁状态（0=正常） |
| `coins` | float | 硬币数（非公开用户为 0） |
| `fans_badge` | bool | 是否有粉丝勋章 |
| `fans_medal` | object | 粉丝勋章信息，含 `medal.level`、`medal.medal_name` |
| `official` | object | 认证信息，`role`(0=无/1=个人/2=机构)、`title`(认证描述) |
| `vip` | object | 大会员信息，`type`(0=无/1=月/2=年)、`status`(1=有效)、`label.text`(标签文字) |
| `pendant` | object | 头像挂件，`pid`、`name`、`image` |
| `nameplate` | object | 勋章，`nid`、`name`(如"2020百大UP主")、`image`、`level`(稀有度) |
| `birthday` | str | 生日（月-日） |
| `school` | object | 学校，`name` |
| `profession` | object | 职业，`name`、`department`、`title`、`is_show` |
| `tags` | array/null | 用户标签 |
| `series` | object | 系列信息，`user_upgrade_status` |
| `is_senior_member` | int | 是否硬核会员 |
| `mcn_info` | object/null | MCN 机构信息 |
| `is_followed` | bool | 当前登录用户是否关注了该用户 |
| `top_photo` | str | 空间头图路径 |
| `live_room` | object | 直播间信息，见下方 |
| `elec` | object | 充电信息，`show_info.total`(充电人数) |
| `contract` | object | 契约信息，`is_display` |
| `attestation` | object | 认证详情，`type`、`common_info.title`、`common_info.prefix` |
| `theme` | object/null | 主题 |
| `name_render` | object/null | 昵称渲染 |
| `certificate_show` | bool | 是否展示证书 |
| `user_honour_info` | object | 荣誉信息，`tags` |

**`live_room` 子对象：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `roomStatus` | int | 房间状态（1=启用） |
| `liveStatus` | int | 直播状态（1=直播中） |
| `url` | str | 直播间 URL |
| `title` | str | 直播间标题 |
| `cover` | str | 直播间封面 |
| `roomid` | int | 房间号 |
| `roundStatus` | int | 轮播状态 |
| `broadcast_type` | int | 广播类型 |
| `watched_show` | object | 观看人数信息，`num`(数值)、`text_small`(缩写)、`text_large`(完整文字) |

---

### 视频目录 -- `videos/<BV号> - <标题>/`

不同 `--video-mode` 下的产出文件：

| video-mode | 产出文件 | 说明 |
|------------|----------|------|
| `full` | `info.json` + `video.mp4` | 视频音频合流（需要 ffmpeg） |
| `video-only` | `info.json` + `video.m4v` | 仅视频轨 |
| `audio-only` | `info.json` + `audio.wav` | 仅音频轨，转WAV |
| `subtitle-only` | `info.json` + `subtitles.srt` | 仅字幕（中文优先，无中文取首个） |
| `none` | `info.json` | 仅元数据 |

#### `info.json` -- 视频元数据

来自 `Video.get_info()` 的完整响应：

| 字段 | 类型 | 说明 |
|------|------|------|
| `bvid` | str | BV 号 |
| `aid` | int | AV 号 |
| `videos` | int | 视频分P数 |
| `tid` | int | 分区 ID（旧版） |
| `tid_v2` | int | 分区 ID（新版） |
| `tname` | str | 分区名（旧版，可能为空） |
| `tname_v2` | str | 分区名（新版，可能为空） |
| `copyright` | int | 版权声明（1=自制/2=转载） |
| `pic` | str | 封面图 URL |
| `title` | str | 标题 |
| `pubdate` | int | 发布时间，Unix 时间戳（秒） |
| `ctime` | int | 创建时间，Unix 时间戳（秒） |
| `desc` | str | 视频简介纯文本 |
| `desc_v2` | array | 简介富文本，每项含 `raw_text`(文本)、`type`(1=纯文本/2=@提及)、`biz_id` |
| `state` | int | 状态（0=正常） |
| `duration` | int | 时长（秒） |
| `mission_id` | int | 活动 ID |
| `dynamic` | str | 同步到动态的文案 |
| `cid` | int | 第一个分P的 CID |
| `dimension` | object | 视频尺寸，`width`、`height`、`rotate` |
| `premiere` | object/null | 首播信息 |
| `teenage_mode` | int | 青少年模式 |
| `is_chargeable_season` | bool | 是否付费番剧 |
| `is_story` | bool | 是否为故事模式 |
| `is_upower_exclusive` | bool | 是否大会员专享 |
| `is_upower_play` | bool | 是否大会员可播放 |
| `is_upower_preview` | bool | 是否大会员试看 |
| `is_upower_exclusive_with_qa` | bool | 是否大会员专享含问答 |
| `enable_vt` | int | 是否启用虚拟字幕 |
| `vt_display` | str | 虚拟字幕显示设置 |
| `no_cache` | bool | 禁止缓存 |
| `is_season_display` | bool | 是否以番剧形式展示 |
| `is_story_play` | int | 故事播放模式 |
| `is_view_self` | bool | 是否为观看自己的视频 |
| `need_jump_bv` | bool | 是否需要跳转 BV |
| `disable_show_up_info` | bool | 禁止显示 UP 主信息 |
| `like_icon` | str | 点赞图标 |

**`rights` 子对象 -- 视频权益：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `bp` | int | BP 权限 |
| `elec` | int | 充电 |
| `download` | int | 允许下载（1=允许） |
| `movie` | int | 电影 |
| `pay` | int | 付费 |
| `hd5` | int | 高清 |
| `no_reprint` | int | 禁止转载（1=禁止） |
| `autoplay` | int | 自动播放 |
| `ugc_pay` | int | UGC 付费 |
| `is_cooperation` | int | 是否合作视频 |
| `ugc_pay_preview` | int | UGC 付费预览 |
| `no_background` | int | 无背景 |
| `clean_mode` | int | 洁净模式 |
| `is_stein_gate` | int | 是否互动视频 |
| `is_360` | int | 是否 360 度视频 |
| `no_share` | int | 禁止分享 |
| `arc_pay` | int | 付费 |
| `free_watch` | int | 免费观看 |

**`owner` 子对象 -- UP 主信息：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `mid` | int | UID |
| `name` | str | 昵称 |
| `face` | str | 头像 URL |

**`stat` 子对象 -- 互动数据（数据分析核心字段）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `aid` | int | AV 号 |
| `view` | int | 播放量 |
| `danmaku` | int | 弹幕数 |
| `reply` | int | 评论数 |
| `favorite` | int | 收藏数 |
| `coin` | int | 投币数 |
| `share` | int | 分享数 |
| `now_rank` | int | 当前排名（0=未上榜） |
| `his_rank` | int | 历史最高排名 |
| `like` | int | 点赞数 |
| `dislike` | int | 踩数 |
| `evaluation` | str | 评分 |
| `vt` | int | 虚拟标记 |

**`pages` 子对象数组 -- 分P信息：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `cid` | int | CID |
| `page` | int | 分P序号（从 1 开始） |
| `from` | str | 来源（如 `vupload`） |
| `part` | str | 分P标题 |
| `duration` | int | 时长（秒） |
| `vid` | str | VID |
| `weblink` | str | 外链 |
| `dimension` | object | 视频尺寸，`width`、`height`、`rotate` |
| `first_frame` | str | 首帧图 URL |
| `ctime` | int | 创建时间 |

**`subtitle` 子对象 -- 字幕信息：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `allow_submit` | bool | 是否允许提交字幕 |
| `list` | array | 字幕列表，每项见下方 |

`subtitle.list[]` 每项：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 字幕 ID |
| `id_str` | str | 字幕 ID（字符串） |
| `lan` | str | 语言代码，如 `ai-zh`(AI中文)、`ai-en`(AI英文)、`ai-ja`(AI日文) |
| `lan_doc` | str | 语言显示名，如"中文"、"English" |
| `is_lock` | bool | 是否锁定 |
| `subtitle_url` | str | 字幕文件 URL |
| `type` | int | 类型（1=AI/0=人工） |
| `ai_type` | int | AI 类型（0=旧版AI/1=新版AI） |
| `ai_status` | int | AI 状态（2=已生成） |
| `subtitle_height` | int/null | 字幕高度 |
| `author` | object | 字幕作者信息，`mid`、`name` 等 |

**`honor_reply` 子对象 -- 荣誉信息：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `honor` | array | 荣誉列表，每项含 `aid`、`type`、`desc`(如"第369期每周必看")、`weekly_recommend_num` |

**`user_garb` 子对象 -- 用户装扮：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `url_image_ani_cut` | str | 装扮动图 URL |

#### `subtitles.srt` -- SRT 字幕文件

标准 SRT 格式，每段包含：

```
1                                    # 序号（从1递增）
00:00:00,040 --> 00:00:02,240        # 时间轴（时:分:秒,毫秒）
你现在看到的是我们公司的两个办公楼  # 字幕文本

2
00:00:02,240 --> 00:00:03,480
我们之前都在那边办公
```

字幕语言选择优先级：中文（`zh` 开头） > 第一个可用字幕。
---

### 音频目录 -- `audios/AU<号> - <标题>/`

#### `info.json` -- 音频元数据

来自 `Audio.get_info()` 的完整响应：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 音频 AU 号 |
| `title` | str | 标题 |
| `cover` | str | 封面图 URL |
| `intro` | str | 简介 |
| `ctime` | int | 创建时间，Unix 时间戳（秒） |
| `passtime` | int | 发布时间，Unix 时间戳（秒） |
| `duration` | int | 时长（秒） |
| `up_id` | int | UP 主 UID |
| `up_name` | str | UP 主昵称 |
| `lyric` | str | 歌词 URL |
| `statistic` | object | 互动数据，`play`(播放)、`collect`(收藏)、`comment`(评论)、`share`(分享) |
| `coin` | object | 投币信息，`coins`(总数)、`is_liked`(是否投过) |
| `tags` | array | 标签列表，每项含 `id`、`name`、`type` |
| `type` | int | 类型（1=原创/2=翻唱） |

#### `audio.wav` -- 音频文件

从 `Audio.get_download_url()` 返回的 CDN 地址下载原始音频，再通过 ffmpeg 转码为 PCM 16bit 16kHz 单声道 WAV 格式（无压缩、无切分）。

---

### 专栏目录 -- `articles/cv<号> - <标题>/`

#### `info.json` -- 专栏元数据

来自 `Article.get_detail()` 的完整响应：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 专栏 cv 号 |
| `title` | str | 标题 |
| `summary` | str | 摘要 |
| `content` | str | 原始 HTML 正文 |
| `banner_url` | str | 头图 URL |
| `category` | object | 分类，`id`、`name`、`parent_id`、`parent_name` |
| `tags` | array | 标签列表，每项含 `tid`、`name` |
| `author` | object | 作者信息，`mid`、`name`、`face`、`vip` |
| `stat` | object | 互动数据，`view`(阅读)、`favorite`(收藏)、`like`(点赞)、`reply`(评论)、`coin`(投币)、`share`(分享) |
| `ctime` | int | 创建时间，Unix 时间戳（秒） |
| `publish_time` | int | 发布时间，Unix 时间戳（秒） |
| `words` | int | 字数 |
| `origin_image_urls` | array | 原始图片 URL 列表 |
| `image_urls` | array | 图片 URL 列表 |
| `media` | object | 关联媒体信息 |
| `list` | object/null | 所属文集信息，`id`、`name` |

#### `article.md` -- Markdown 正文

由 `article_converter.py` 从 `info.json` 中的 `content`（HTML）转换而来。转换规则：

| HTML 元素 | Markdown 输出 |
|-----------|---------------|
| `h1`-`h6` | `# 标题` ~ `###### 标题` |
| `p` | 段落（前后空行） |
| `blockquote` | `> 引用` |
| `pre`/`code` | ` ```代码块``` `（保留语言标记） |
| `code`（行内） | `` `代码` `` |
| `strong`/`b` | `**粗体**` |
| `em`/`i` | `*斜体*` |
| `ul`/`li` | `- 列表项` |
| `ol`/`li` | `1. 列表项` |
| `img` | `![alt](images/img_N.ext)`（图片下载到本地） |
| `a` | `[文本](href)` |
| `figure` | 递归处理子元素 |
| `figcaption` | `*说明文字*` |
| `br` | 换行 |

#### `images/` -- 文章图片

文章 HTML 中所有 `<img>` 标签的图片下载到本地，命名为 `img_1.jpg`、`img_2.png` 等（保留原始扩展名）。`article.md` 中的图片引用为相对路径 `images/img_N.ext`。

---

### 动态目录 -- `dynamics/<动态ID>/`

#### `dynamic.json` -- 动态完整原始数据

来自 `User.get_dynamics_new()` API 的完整动态对象，结构复杂，主要包含：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id_str` | str | 动态 ID |
| `type` | str | 动态类型（见下方类型表） |
| `visible` | bool | 是否可见 |
| `basic` | object | 基本信息，`rid_str`(关联资源ID)、`comment_type`、`comment_id_str` |
| `modules` | object | 模块数据，见下方 |

**`modules` 子对象 -- 动态内容模块：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `module_author` | object | 作者信息，`mid`、`name`、`avatar`、`pub_ts`(发布时间戳)、`pub_time`(发布时间文本) |
| `module_dynamic` | object | 动态内容，含 `desc`(文字描述，`text`字段)、`major`(主要关联内容) |
| `module_tag` | object/null | 标签信息 |
| `module_stat` | object | 互动数据，`comment`(评论数)、`forward`(转发数)、`like`(点赞数) |
| `module_interaction` | object | 互动信息 |

`modules.module_dynamic.major` 根据动态类型包含不同的子对象：

| 动态类型 | major 子对象 | 关键字段 |
|----------|-------------|----------|
| `DYNAMIC_TYPE_AV` | `archive` | `bvid`、`aid`、`title`、`desc`、`cover`、`duration_text` |
| `DYNAMIC_TYPE_ARTICLE` | `article` | `id`(cvid)、`title`、`desc`、`cover` |
| `DYNAMIC_TYPE_MUSIC` | `music` | `id`(auid)、`title`、`cover`、`type` |
| `DYNAMIC_TYPE_DRAW` | `draw` | `items`(图片列表，每项含 `src`、`width`、`height`) |
| `DYNAMIC_TYPE_LIVE_RCMD` | `live_rcmd` | 直播间信息 |

**`orig` 字段 -- 转发动态的原始动态：**

`DYNAMIC_TYPE_FORWARD` 类型的动态在顶层包含 `orig` 字段，结构同完整动态对象。

#### `info.json` -- 动态摘要

由程序生成的精简摘要，便于快速检索和关联：

| 字段 | 类型 | 说明 |
|------|------|------|
| `dynamic_id` | str | 动态 ID |
| `type` | str | 动态类型，如 `DYNAMIC_TYPE_AV`、`DYNAMIC_TYPE_DRAW` 等 |
| `embedded` | object | 关联的其他内容 ID，见下方 |

`embedded` 子对象根据动态类型包含不同的关联 ID：

| 动态类型 | embedded 字段 | 说明 |
|----------|---------------|------|
| `DYNAMIC_TYPE_AV` | `bvid`、`aid` | 关联视频的 BV 号和 AV 号 |
| `DYNAMIC_TYPE_ARTICLE` | `cvid` | 关联专栏的 cv 号 |
| `DYNAMIC_TYPE_MUSIC` | `auid` | 关联音频的 AU 号 |

> `embedded` 字段可用于将动态与已下载的视频/专栏/音频关联，实现跨类型数据打通。

#### `images/` -- 动态图片

仅以下动态类型会下载图片：

| 动态类型 | 图片来源 |
|----------|----------|
| `DYNAMIC_TYPE_DRAW` | 图文动态的所有图片 |
| `DYNAMIC_TYPE_AV` | 视频封面图 |
| `DYNAMIC_TYPE_LIVE_RCMD` | 直播间信息中的图片 |
| `DYNAMIC_TYPE_FORWARD` | 被转发原始动态中的图片（如有） |

图片命名规则：`img_1.jpg`、`img_2.png` 等（保留原始扩展名）。如果正文中包含图片 URL 也会一并提取下载（上限 10 张）。

#### 动态类型汇总

| 类型常量 | 含义 | 典型内容 |
|----------|------|----------|
| `DYNAMIC_TYPE_AV` | 视频动态 | 发布视频时自动生成的动态 |
| `DYNAMIC_TYPE_ARTICLE` | 专栏动态 | 发布专栏时自动生成的动态 |
| `DYNAMIC_TYPE_MUSIC` | 音频动态 | 发布音频时自动生成的动态 |
| `DYNAMIC_TYPE_DRAW` | 图文动态 | 纯图片+文字的动态 |
| `DYNAMIC_TYPE_FORWARD` | 转发动态 | 转发其他动态，`orig` 含原始动态 |
| `DYNAMIC_TYPE_LIVE_RCMD` | 直播推荐 | 直播开播通知 |
| `DYNAMIC_TYPE_WORD` | 纯文字动态 | 仅文字内容 |
| `DYNAMIC_TYPE_COMMON_SQUARE` | 通用动态 | 其他类型 |
---

## 断点续传

下载记录保存在 SQLite 数据库中，中断后重新运行会自动跳过已完成的内容。

- **数据库路径**：`.bilibili-clone/downloads.db`（可通过环境变量 `BILIBILI_CLONE_DB_DIR` 修改）
- **表结构**：`downloads` 表，主键 `(uid, content_type, content_id)`

| 字段 | 类型 | 说明 |
|------|------|------|
| `uid` | INTEGER | 用户 UID |
| `content_type` | TEXT | 内容类型：`video`/`audio`/`article`/`dynamic` |
| `content_id` | TEXT | 内容 ID：BV 号/AU 号/cv 号/动态 ID |
| `status` | TEXT | 下载状态：`done`(成功)/`failed`(失败)/`skipped`(跳过) |
| `output_dir` | TEXT | 输出目录路径 |
| `created_at` | TIMESTAMP | 记录创建时间 |

### 判断逻辑

- 枚举阶段调用 `store.is_done()` 检查 `status IN ('done', 'skipped')` 的记录，跳过已完成或跳过的项
- 下载完成后调用 `store.mark()` 写入状态（`done`/`failed`/`skipped`）
- 重复运行时，已 `done` 或 `skipped` 的项不会重复下载；`failed` 的项会重新尝试

### 凭证存储

- **凭证路径**：`~/.bilibili-cli/credential.json`
- **有效期**：7 天（`CREDENTIAL_TTL_DAYS`），过期后自动触发 QR 码重新登录
- **存储字段**：`sessdata`、`bili_jct`、`buvid3`、`buvid4`、`dedeuserid`、`ac_time_value`、`saved_at`