# 匹配机制与写入结构说明

本文档详细介绍内置适配器（Adapter）的匹配检索算法以及 TTML 文件的 XML 写入逻辑。在 v2 中，接口候选仅向前端/HTTP 暴露排名、推荐状态与解释证据（Evidence），内部使用的数值评分不进入公共契约。

---

## 1. Apple Music 区域检索规则

程序会固定按以下顺序依次查询 5 个区域，以获取最完整的多语言/本地化元数据：
1. **`cn`** (中国大陆)
2. **`us`** (美国)
3. **`kr`** (韩国)
4. **`jp`** (日本)
5. **`tw`** (中国台湾)

检索为全局扫视，即使用户在音频标签中提供了已有的 Apple Music 歌曲 ID，也会按上述区域顺序查满，以确保能够补充不同区域的本地化歌名、歌手、专辑及 ISRC 信息。

---

## 2. 元数据匹配机制

### 音频标签读取

从音频文件（如 `.flac`、`.m4a`）中大小写不敏感地读取以下标签：
- **歌名**：`title`
- **歌手**：`artist`、`artists`
- **专辑**：`album`
- **ISRC**：`ISRC`
- **Apple Music 歌曲 ID**：`ITUNESCATALOGID`
- **Apple Music 专辑 ID**：`ITUNESPLAYLISTID`
- **曲目号**：`track`、`tracknumber`
- **碟号**：`disc`、`discnumber`

### 多艺术家拆分

支持主流的多艺术家格式（如 `A, B, C & D`）。只要检测到分隔符（包括 `,`、`;`·`、`·`&`·`＆`），就会将其拆分为独立的 `artists` 元数据节点。

例如，`Sān-Z & HOYO-MiX` 写入 TTML 时会生成：
```xml
<amll:meta key="artists" value="Sān-Z"/>
<amll:meta key="artists" value="HOYO-MiX"/>
```

### Apple Music 检索流程

1. **ID 直接匹配**：若音频标签中存在有效的 `ITUNESCATALOGID`，验证为合法 ID 后将直接采信。在纯 TTML 模式（无音频）下，也会读取已有的 `appleMusicId` 和 `isrc`。
2. **专辑回查**：若标签包含 `ITUNESPLAYLISTID`（专辑 ID），程序会通过 `https://music.apple.com/{store}/album/{ID}` 遍历 5 个区域读取专辑的曲目列表。
   - 优先通过**碟号+曲目号**匹配。
   - 缺失曲目号时，通过规范化曲名和时长进行辅助匹配。
   - 请求优先采用配置的 `APPLE_MUSIC_BEARER_TOKEN`，如无则自动从网页端抓取临时 Token。
3. **普通搜索**：若以上方式均未匹配，则通过歌名在 Apple Music 进行关键词检索。候选结果按 ISRC、歌名、歌手、专辑、发行日期及时间长度进行综合排序。
4. **艺人专辑降级检索 (Artist-Album Fallback)**：如果普通搜索效果不佳，且音频包含完整的歌手、发行日期及时间信息，程序会转而搜索该歌手，分页读取其名下专辑/单曲列表（最多 10 页，每页 50 张），寻找发行日期吻合、歌手匹配且时间误差在 1 秒以内的曲目。
5. **伴奏过滤**：若原始歌名不含伴奏标记，则检索到的带 `Instrumental`、`伴奏`、`インスト`、`반주` 等标记的候选会被降权且不会被自动推荐。

### QQ 音乐检索流程

1. **关键词**：仅使用歌名（音频的 `title` 标签或 TTML 已有的 `musicName`）。
2. **接口**：请求 QQ 音乐移动端搜索接口，筛选出同时包含 `songid` 和 `mid` 的 `item_song` 候选。
3. **权重排序**：匹配优先级为：歌名匹配度（最高） > 歌手匹配度 > 专辑匹配度。精确匹配优于包含匹配，多歌手会逐一进行相似度比对。

### 网易云音乐检索流程

1. **执行时机**：在 QQ 音乐候选匹配完成后执行（可作为依赖输入）。
2. **繁简归一**：匹配比对时会将繁简体文本统一转换为简体进行判定，写入 TTML 时仍保留 API 返回 of 原始字符。
3. **检索接口**：在全局并发预算内，轮询以下三个公开 API（只获取单曲类别的第 1 页前 100 条候选）：
   - `https://music163.xuanmou.com.cn/cloudsearch?keywords={歌名}&limit=100&offset=0&type=1`
   - `https://neteasecloudmusicapi-main-api.vercel.app/cloudsearch?keywords={歌名}&limit=100&offset=0&type=1`
   - `https://api-enhanced-six-beta.vercel.app/cloudsearch?keywords={歌名}&limit=100&offset=0&type=1`
4. **关联回查**：如果已匹配的 QQ 音乐候选提供了歌手和专辑信息，程序会回查网易云的歌手专辑和专辑详情接口，合并检索结果并去重。
5. **权重排序**：与 QQ 音乐匹配逻辑相同（歌名 > 歌手 > 专辑）。

### Spotify 检索流程

1. **前置条件**：需提供 `SPOTIFY_CLIENT_ID` 和 `SPOTIFY_CLIENT_SECRET`。
2. **检索步骤**：
   - 第一阶段：优先根据 `isrc:{ISRC}` 精准搜索。
   - 第二阶段：使用宽泛关键词 `{歌名} {歌手} {专辑}` 及 `{歌名}` 搜索。
   - 第三阶段（Fallback）：使用三要素字段限制搜索：`track:{歌名} artist:{歌手} album:{专辑}`。
3. **检索市场**：固定在 `US`、`KR`、`JP`、`TW` 四个区域市场进行检索。
4. **艺人专辑降级检索 (Artist-Album Fallback)**：普通检索候选不足且音频元数据完整时，检索该艺人最近 30 张专辑，匹配发行日期一致、歌手吻合且时长误差在 2 秒内的曲目。同样过滤伴奏结果。

---

## 3. XML 写入规范

为了防止因 XML 序列化器重构产生无谓的格式变动，程序**不**使用标准 XML 树序列化重写整个文件，而是采用文本标记精确定位与修改。

1. **语言规范化**：如果根节点声明为繁体中文（`xml:lang="zh-Hant"`），程序会将其更改为简体 `zh-Hans`，并将 `<body>` 内部的歌词文本转换为简体，同时自动剥离冗余的 `zh-Hans` 翻译层与 `zh-Latn-pinyin` 拼音音译层（注：`<head>/<metadata>` 内的数据和属性不进行简繁转换）。
2. **命名空间**：如果根节点缺失 AMLL 命名空间，会自动在根 `<tt>` 上补充：`xmlns:amll="http://www.example.com/ns/amll"`。
3. **节点插入**：程序仅修改已有的 `<metadata>`。若缺失该节点将直接报错。写入时，新增的 `amll:meta` 标签会按如下结构优先插入到已有的 `<iTunesMetadata>` 之前，如无则插在 `</metadata>` 之前。
4. **去重新增**：保留用户人工编辑过的真实值，对于新检索出的未出现值，则作为同一个 key 的新节点追加（如支持多歌手或多语言歌名）。

### 写入示例

```xml
<metadata>
  <ttm:agent type="person" xml:id="v1"/>
  <amll:meta key="musicName" value="Disease"/>
  <amll:meta key="artists" value="Lady Gaga"/>
  <amll:meta key="album" value="Disease - Single"/>
  <amll:meta key="qqMusicId" value="123456"/>
  <amll:meta key="ncmMusicId" value="456789"/>
  <amll:meta key="spotifyId" value="33e05cb33dd34eddb7d1d3b809dd44e1"/>
  <amll:meta key="appleMusicId" value="6768201779"/>
  <amll:meta key="isrc" value="USUM72603828"/>
  <iTunesMetadata>...</iTunesMetadata>
</metadata>
```

---

## 4. 命令行输出日志规范

### 典型 预览 (dry-run) 日志

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

### 已填充文件未发生变化的日志

如果文件中的各平台 ID 及元数据已是最新且完全一致，运行将显示 `unchanged` 并展示跳过写入的项目：

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
