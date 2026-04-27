# Output Schema Reference

Complete field-level documentation for all JSON output files produced by bilibili-user-clone.

## Root `info.json` — User Profile

From `User.get_user_info()`:

| Field | Type | Description |
|-------|------|-------------|
| `mid` | int | User UID |
| `name` | str | Nickname |
| `sex` | str | Gender |
| `face` | str | Avatar URL |
| `sign` | str | Signature |
| `rank` | int | Sort rank |
| `level` | int | Level (0-6) |
| `jointime` | int | Registration time (0 for non-public) |
| `moral` | int | Moral value |
| `silence` | int | Ban status (0=normal) |
| `coins` | float | Coins (0 for non-public) |
| `fans_badge` | bool | Has fan badge |
| `fans_medal` | object | Fan medal info, `medal.level`, `medal.medal_name` |
| `official` | object | Verification, `role`(0=none/1=personal/2=org), `title` |
| `vip` | object | VIP info, `type`(0=none/1=month/2=year), `status`(1=active), `label.text` |
| `pendant` | object | Avatar pendant, `pid`, `name`, `image` |
| `nameplate` | object | Nameplate badge, `nid`, `name`, `image`, `level`(rarity) |
| `birthday` | str | Birthday (MM-DD) |
| `school` | object | School, `name` |
| `profession` | object | Profession, `name`, `department`, `title`, `is_show` |
| `tags` | array/null | User tags |
| `series` | object | Series info, `user_upgrade_status` |
| `is_senior_member` | int | Hardcore member |
| `mcn_info` | object/null | MCN info |
| `is_followed` | bool | Followed by current user |
| `top_photo` | str | Space banner URL |
| `live_room` | object | Live room info (see below) |
| `elec` | object | Charging info, `show_info.total` |
| `contract` | object | Contract info, `is_display` |
| `attestation` | object | Attestation, `type`, `common_info.title`, `common_info.prefix` |

### `live_room` sub-object

| Field | Type | Description |
|-------|------|-------------|
| `roomStatus` | int | Room status (1=active) |
| `liveStatus` | int | Live status (1=streaming) |
| `url` | str | Room URL |
| `title` | str | Room title |
| `cover` | str | Room cover |
| `roomid` | int | Room ID |
| `roundStatus` | int | Round status |
| `broadcast_type` | int | Broadcast type |
| `watched_show` | object | Viewers, `num`, `text_small`, `text_large` |

---

## Video `info.json`

From `Video.get_info()`:

| Field | Type | Description |
|-------|------|-------------|
| `bvid` | str | BV ID |
| `aid` | int | AV ID |
| `videos` | int | Page count |
| `tid` / `tid_v2` | int | Category ID (old/new) |
| `tname` / `tname_v2` | str | Category name (may be empty) |
| `copyright` | int | 1=original, 2=repost |
| `pic` | str | Cover URL |
| `title` | str | Title |
| `pubdate` | int | Publish timestamp |
| `ctime` | int | Create timestamp |
| `desc` | str | Description (plain text) |
| `desc_v2` | array | Rich text desc, each: `raw_text`, `type`(1=text/2=mention), `biz_id` |
| `state` | int | Status (0=normal) |
| `duration` | int | Duration (seconds) |
| `cid` | int | First page CID |
| `dimension` | object | `width`, `height`, `rotate` |

### `rights` sub-object

| Field | Type | Description |
|-------|------|-------------|
| `download` | int | Allow download (1=yes) |
| `no_reprint` | int | No repost (1=prohibited) |
| `is_cooperation` | int | Collab video |
| `is_stein_gate` | int | Interactive video |
| `is_360` | int | 360° video |
| `hd5` | int | HD |
| `pay` / `ugc_pay` | int | Paid content |

### `stat` sub-object (analytics)

| Field | Type | Description |
|-------|------|-------------|
| `view` | int | Views |
| `danmaku` | int | Danmaku count |
| `reply` | int | Comments |
| `favorite` | int | Favorites |
| `coin` | int | Coins |
| `share` | int | Shares |
| `like` | int | Likes |
| `his_rank` | int | Historical best rank |

### `pages[]` sub-object

| Field | Type | Description |
|-------|------|-------------|
| `cid` | int | CID |
| `page` | int | Page number (from 1) |
| `part` | str | Page title |
| `duration` | int | Duration (seconds) |

### `subtitle` sub-object

| Field | Type | Description |
|-------|------|-------------|
| `allow_submit` | bool | Allow subtitle submission |
| `list[]` | array | Subtitle list |

`subtitle.list[]` each item:

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Subtitle ID |
| `lan` | str | Language code, e.g. `ai-zh`, `ai-en` |
| `lan_doc` | str | Display name, e.g. "中文" |
| `subtitle_url` | str | Subtitle file URL |
| `type` | int | 1=AI, 0=manual |

---

## Audio `info.json`

From `Audio.get_info()`:

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | AU ID |
| `title` | str | Title |
| `cover` | str | Cover URL |
| `intro` | str | Description |
| `ctime` | int | Create timestamp |
| `passtime` | int | Publish timestamp |
| `duration` | int | Duration (seconds) |
| `up_id` | int | UP master UID |
| `up_name` | str | UP master name |
| `lyric` | str | Lyrics URL |
| `statistic` | object | `play`, `collect`, `comment`, `share` |
| `coin` | object | `coins`, `is_liked` |
| `tags` | array | Each: `id`, `name`, `type` |
| `type` | int | 1=original, 2=cover |

---

## Article `info.json`

From `Article.get_detail()`:

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | cv ID |
| `title` | str | Title |
| `summary` | str | Summary |
| `content` | str | Raw HTML body |
| `banner_url` | str | Banner URL |
| `category` | object | `id`, `name`, `parent_id`, `parent_name` |
| `tags` | array | Each: `tid`, `name` |
| `author` | object | `mid`, `name`, `face`, `vip` |
| `stat` | object | `view`, `favorite`, `like`, `reply`, `coin`, `share` |
| `ctime` | int | Create timestamp |
| `publish_time` | int | Publish timestamp |
| `words` | int | Word count |
| `origin_image_urls` | array | Original image URLs |
| `image_urls` | array | Image URLs |
| `media` | object | Related media |
| `list` | object/null | Series info, `id`, `name` |

---

## Dynamic `info.json` (generated summary)

| Field | Type | Description |
|-------|------|-------------|
| `dynamic_id` | str | Dynamic ID |
| `type` | str | Dynamic type constant |
| `embedded` | object | Cross-referenced content IDs |

`embedded` varies by type:

| Type | Fields | Description |
|------|--------|-------------|
| `DYNAMIC_TYPE_AV` | `bvid`, `aid` | Linked video |
| `DYNAMIC_TYPE_ARTICLE` | `cvid` | Linked article |
| `DYNAMIC_TYPE_MUSIC` | `auid` | Linked audio |

## Dynamic `dynamic.json` (raw API)

From `User.get_dynamics_new()`. Key fields:

| Field | Type | Description |
|-------|------|-------------|
| `id_str` | str | Dynamic ID |
| `type` | str | Dynamic type |
| `visible` | bool | Visibility |
| `basic` | object | `rid_str`, `comment_type`, `comment_id_str` |
| `modules.module_author` | object | `mid`, `name`, `pub_ts` |
| `modules.module_dynamic.desc` | object | `text` |
| `modules.module_dynamic.major` | object | Varies by type (see below) |
| `modules.module_stat` | object | `comment`, `forward`, `like` |

`major` sub-objects by type:

| Type | Key | Key Fields |
|------|-----|------------|
| `DYNAMIC_TYPE_AV` | `archive` | `bvid`, `aid`, `title`, `cover` |
| `DYNAMIC_TYPE_ARTICLE` | `article` | `id`(cvid), `title`, `cover` |
| `DYNAMIC_TYPE_MUSIC` | `music` | `id`(auid), `title`, `cover` |
| `DYNAMIC_TYPE_DRAW` | `draw` | `items[].src`, `width`, `height` |
| `DYNAMIC_TYPE_LIVE_RCMD` | `live_rcmd` | Live room info |

`DYNAMIC_TYPE_FORWARD` contains `orig` field with the original dynamic object.

## Dynamic types

| Constant | Meaning |
|----------|---------|
| `DYNAMIC_TYPE_AV` | Video dynamic |
| `DYNAMIC_TYPE_ARTICLE` | Article dynamic |
| `DYNAMIC_TYPE_MUSIC` | Audio dynamic |
| `DYNAMIC_TYPE_DRAW` | Image-text dynamic |
| `DYNAMIC_TYPE_FORWARD` | Repost (has `orig`) |
| `DYNAMIC_TYPE_LIVE_RCMD` | Live stream notification |
| `DYNAMIC_TYPE_WORD` | Text-only dynamic |
| `DYNAMIC_TYPE_COMMON_SQUARE` | General |

## SQLite `downloads` table

| Column | Type | Description |
|--------|------|-------------|
| `uid` | INTEGER | User UID |
| `content_type` | TEXT | `video`/`audio`/`article`/`dynamic` |
| `content_id` | TEXT | BV/AU/cv/dynamic ID |
| `status` | TEXT | `done`/`failed`/`skipped` |
| `output_dir` | TEXT | Output path |
| `created_at` | TIMESTAMP | Record time |

PK: `(uid, content_type, content_id)`

## Credential `credential.json`

| Field | Type | Description |
|--------|------|-------------|
| `sessdata` | str | Session cookie |
| `bili_jct` | str | CSRF token |
| `buvid3` | str | Device cookie |
| `buvid4` | str | Device cookie v4 |
| `dedeuserid` | str | User ID cookie |
| `ac_time_value` | str | WBI token |
| `saved_at` | float | Save timestamp (Unix) |
