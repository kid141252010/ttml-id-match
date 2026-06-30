# 匹配机制与写入结构说明

本文档详细介绍了脚本在处理音频标签、进行多平台（Apple Music、QQ音乐、网易云音乐、Spotify）元数据检索时的匹配算法、排序规则以及写入 XML 的具体结构。

## Apple Music 查询区域

默认区域查找顺序如下，脚本固定查询这五个区域以获取本地化信息：
1. `cn`
2. `us`
3. `kr`
4. `jp`
5. `tw`

脚本不再提供单区域、兜底区域或交互输入区域参数。已有 Apple Music 歌曲 ID 不会让查询提前结束，仍会按以上顺序搜索以补充本地化歌名、歌手、专辑和 ISRC。

---

## 匹配规则

### 音频标签读取

脚本会大小写不敏感读取以下音频标签：
- **歌名**：`title`
- **艺术家**：`artist`、`artists`
- **专辑**：`album`
- **ISRC**：`ISRC`
- **Apple Music 歌曲 ID 候选**：`ITUNESCATALOGID`
- **Apple Music 专辑 ID**：`ITUNESPLAYLISTID`
- **曲目号**：`track`、`tracknumber`
- **碟号**：`disc`、`discnumber`

### 多艺术家拆分

脚本支持 Apple Music 常见艺术家格式（如 `A, B, C & D`），会将其写成多个独立的 `artists` 节点。

`,`、`;`、`、`、`&`、`＆` 都按多艺术家分隔符处理。例如 `Sān-Z & HOYO-MiX` 会写成 `Sān-Z` 和 `HOYO-MiX` 两个 `artists` 节点。

```xml
<amll:meta key="artists" value="Sān-Z"/>
<amll:meta key="artists" value="HOYO-MiX"/>
```

### Apple Music 元数据查找

- **ID 校验**：音频标签中的 `ITUNESCATALOGID` 只有在看起来像有效歌曲 ID 时才直接写入。TTML-only 模式下也会读取已有 `amll:meta key="appleMusicId"` 和 `isrc`。
- **专辑匹配**：当音频存在 `ITUNESPLAYLISTID` 时，脚本会将其作为专辑 ID 查询曲目：
  `https://music.apple.com/{store}/album/{ITUNESPLAYLISTID}`
  程序会优先使用 `APPLE_MUSIC_BEARER_TOKEN`，未配置时从网页端自动提取临时 Token。在 5 个区域读取专辑曲目时，优先使用碟号和曲目号匹配；缺少曲目号时，使用规范化曲名和时长辅助匹配。
- **普通搜索**：只要有歌名，脚本就会请求 Apple Music 搜索接口。普通搜索候选按 ISRC、歌名、歌手、专辑、发行日期和时长排序。
- **艺人专辑降级检索 (Artist-Album Fallback)**：普通搜索标题匹配明显较弱且音频里包含歌手、发行日期和时长时，脚本会搜索对应艺人，分页读取该艺人的 album/single 列表（最多 10 页、每页 50 张）进行发行日期匹配、歌手匹配且时长误差不超过 1 秒的保守匹配。
- **伴奏过滤**：源歌名不含伴奏标记时，带 `Instrumental`、`伴奏`、`インスト`、`반주` 等标记的候选会大幅降权且不会自动选中。

### QQ 音乐元数据查找

- **检索关键词**：仅使用歌名作为检索词（音频标签中的 `title` 或 TTML 中已有的 `musicName`）。
- **接口**：请求 QQ 音乐移动端搜索接口，读取 `item_song` 候选，要求同时具备 `songid` 和 `mid`。
- **匹配权重**：综合歌名、歌手和专辑匹配度，其中歌名权重最高，歌手其次，专辑再次。精确匹配优先于包含匹配（如可处理包含关系）。多歌手会逐个比较。

### 网易云音乐元数据查找

- **检索顺序**：在 QQ 音乐候选确认后执行。
- **繁简归一**：匹配前会将繁体和简体文本统一转换为简体进行比较，但最终写入时仍保留 API 返回的原始文本。
- **并发检索接口**：使用歌名并发请求以下 3 个公开 API 接口竞速，固定读取单曲搜索第一页的前 100 条候选：
  - `https://music163.xuanmou.com.cn/cloudsearch?keywords={歌名}&limit=100&offset=0&type=1`
  - `https://neteasecloudmusicapi-main-api.vercel.app/cloudsearch?keywords={歌名}&limit=100&offset=0&type=1`
  - `https://api-enhanced-six-beta.vercel.app/cloudsearch?keywords={歌名}&limit=100&offset=0&type=1`
- **歌手专辑回查**：如果确认后的 QQ 候选提供了歌手和专辑，脚本会额外请求网易云的歌手、歌手专辑和专辑详情接口获取候选并去重。
- **匹配权重**：与 QQ 音乐一致（歌名 > 歌手 > 专辑）。

### Spotify 元数据查找

- **凭据要求**：必须在 `.env` 或系统环境变量中配置 `SPOTIFY_CLIENT_ID` 和 `SPOTIFY_CLIENT_SECRET`，通过 Client Credentials Flow 获取 Token。
- **检索逻辑**：
  1. 优先使用 `isrc:{ISRC}` 搜索；
  2. 使用宽松关键词 `{歌名} {歌手} {专辑}` 和 `{歌名}` 搜索；
  3. 若候选不足，fallback 到三要素限定搜索：`track:{歌名} artist:{歌手} album:{专辑}`。
- **市场范围**：固定搜索 `US`、`KR`、`JP`、`TW` 四个市场。
- **艺人专辑降级检索 (Artist-Album Fallback)**：普通检索候选不足且音频包含完整元数据时，会搜索艺人并读取其最近 30 张专辑/单曲，匹配发行日期一致、艺人匹配且时长在 2 秒内接近的曲目。同样会过滤伴奏候选。

---

## XML 写入结构

为了避免命名空间前缀和属性顺序产生无关变化，脚本**不会**使用 XML 序列化器重写整份 TTML。

1. **语言规范化**：如果根 `<tt>` 是 `xml:lang="zh-Hant"`，脚本会先将其改为 `zh-Hans`，将 `<body>` 内的主歌词文本节点转为简体，并删除 `zh-Hans` replacement 翻译层和 `zh-Latn-pinyin` 音译层。`<head>/<metadata>` 里的文字和属性不会被转换。
2. **命名空间声明**：若根节点缺少 AMLL 命名空间声明，脚本会在根 `<tt>` 上补充 `xmlns:amll="http://www.example.com/ns/amll"`。
3. **结构要求**：脚本只修改已有 `<metadata>` 内部的 `amll:meta` 节点。若文件缺少 `<metadata>`，脚本会报错并跳过。
4. **插入位置**：新增的 `amll:meta` 会插入到已有 `<metadata>` 内；有 `<iTunesMetadata>` 时插到它之前，否则插到 `</metadata>` 之前：

```xml
<metadata>
  <ttm:agent type="person" xml:id="v1"/>
  <amll:meta key="musicName" value="..."/>
  <amll:meta key="artists" value="..."/>
  <amll:meta key="album" value="..."/>
  <amll:meta key="qqMusicId" value="..."/>
  <amll:meta key="ncmMusicId" value="..."/>
  <amll:meta key="spotifyId" value="..."/>
  <amll:meta key="appleMusicId" value="..."/>
  <amll:meta key="isrc" value="..."/>
  <iTunesMetadata>...</iTunesMetadata>
</metadata>
```

5. **去重规则**：已有人工整理的真实值默认保留，新查到的未出现值会追加到同一个 key 下（例如多语言歌名或歌手别名）。

---

## 输出说明

### 典型 dry-run 输出

```text
[dry-run] 2. Disease (Apple Music Live).ttml
  audio: 2. Disease (Apple Music Live).flac
  appleMusicBest: CN: Disease (Apple Music Live) - Lady Gaga - Apple Music Live: MAYHEM Requiem [6768201779], US: Disease - Lady Gaga - Apple Music Live: MAYHEM Requiem [6768201780]
  appleMusicId: 6768201779, 6768201780
  appleMusicSources: album:cn:track, album:us:track, album:kr:track, album:jp:track, album:tw:track
  qqMusicBest: Disease - Lady Gaga - Apple Music Live: MAYHEM Requiem [123456, 001abc]
  qqMusicId: 123456, 001abc
  ncmMusicBest: Disease - Lady Gaga - Apple Music Live: MAYHEM Requiem [456789]
  ncmMusicId: 456789
  spotifyBest: US: Disease - Lady Gaga - Apple Music Live: MAYHEM Requiem [33e05cb33dd34eddb7d1d3b809dd44e1]
  spotifyId: 33e05cb33dd34eddb7d1d3b809dd44e1
  added: musicName = Disease (Apple Music Live), Disease - Apple Music Live
  added: artists = Lady Gaga
  added: album = Apple Music Live: MAYHEM Requiem, Apple Music Live: MAYHEM
  added: qqMusicId = 123456, 001abc
  added: ncmMusicId = 456789
  added: spotifyId = 33e05cb33dd34eddb7d1d3b809dd44e1
  added: appleMusicId = 6768201779, 6768201780
  added: isrc = USUM72603828
```

### 已填充文件的 unchanged 输出

如果文件已经填过所有真实值，再运行会显示 `unchanged` 和 `skipped`：

```text
[unchanged] 2. Disease (Apple Music Live).ttml
  audio: 2. Disease (Apple Music Live).flac
  ...
  skipped: musicName = Disease (Apple Music Live)
  skipped: artists = Lady Gaga
  skipped: album = Apple Music Live: MAYHEM Requiem
  skipped: qqMusicId = 123456, 001abc
  skipped: ncmMusicId = 456789
  skipped: spotifyId = 33e05cb33dd34eddb7d1d3b809dd44e1
  skipped: appleMusicId = 6768201779, 6768201780
  skipped: isrc = USUM72603828
```
