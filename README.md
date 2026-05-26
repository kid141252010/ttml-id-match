# TTML 元数据快速填充脚本

这个仓库提供一个 Python CLI 脚本，用音频文件里的标签或 TTML 里已有的基础信息快速填充 TTML 歌词文件中的 AMLL 元数据。

当前脚本入口：

```powershell
python fill_ttml_metadata.py --help
```

## 功能

脚本会从音频元数据读取并填入以下 TTML 元数据：

```xml
<amll:meta key="musicName" value="..."/>
<amll:meta key="artists" value="..."/>
<amll:meta key="album" value="..."/>
<amll:meta key="qqMusicId" value="..."/>
<amll:meta key="ncmMusicId" value="..."/>
<amll:meta key="spotifyId" value="..."/>
<amll:meta key="isrc" value="..."/>
<amll:meta key="appleMusicId" value="..."/>
```

默认行为：

- 追加缺失的元数据值，不覆盖已有真实值，重复值会跳过。
- `value="*"` 或空值会被视为占位符并替换。
- 写入前自动生成 `.bak` 备份。
- 处理主语言为 `xml:lang="zh-Hant"` 的 TTML 时，会先自动改为 `zh-Hans`，并把歌词正文转换为简体。
- 批量模式按同名文件配对音频和 TTML；同名 `.flac` 和 `.m4a` 同时存在时优先使用 `.flac`。没有同名音频时，会尝试从 TTML 已有 `musicName`、`artists`、`album`、`appleMusicId`、`isrc` 填充 Apple Music、QQ 音乐、网易云音乐和 Spotify ID。
- 多艺术家会拆成多个 `artists` 元数据节点。
- `ITUNESCATALOGID` 若明显不是歌曲 ID，例如示例中的 `1`，不会直接写入。
- 会在 `cn`、`us`、`kr`、`jp`、`tw` 五个区域执行 Apple Music 匹配；已有 `ITUNESCATALOGID` 或 TTML `appleMusicId` 仍会保留并继续搜索其它区域候选。
- 会用歌名搜索 QQ 音乐，按歌名、歌手、专辑匹配候选，并把选中结果的 songid 和 mid 分别写入 `qqMusicId`。
- 会在 QQ 音乐候选确认后查找网易云音乐：先用歌名搜索，再用确认后的 QQ 歌手和专辑补充网易云歌手专辑回查，并把选中结果的歌曲 ID 写入 `ncmMusicId`。
- 如果配置了 Spotify 凭据，会用官方 Web API 的 Client Credentials Flow 获取 token，并在 `US`、`KR`、`JP`、`TW` 四个市场搜索 track；普通搜索不足时会用艺人专辑、发行日期和时长做保守 fallback。每个市场取匹配度最高的候选，`spotifyId` 和 Spotify ISRC 按值去重写入；缺少凭据时自动跳过，不影响其它来源。

## 环境要求

- Python 3.10 或更新版本。
- 网络访问 Apple Music、QQ 音乐、网易云音乐公开 API 和 Spotify Web API，用于查找歌曲 ID。
- Python 依赖：`mutagen`、`opencc-python-reimplemented`。

安装依赖：

```powershell
python -m pip install -r requirements.txt
```

## Spotify 凭据

Spotify 搜索默认启用，但需要本地凭据。复制 `.env.example` 为 `.env`，填入 Spotify Developer 后台创建应用得到的 Client ID 和 Client Secret：

```text
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
```

脚本会先读取当前目录的 `.env`，再用系统环境变量覆盖同名值。`.env` 已在 `.gitignore` 中屏蔽，仓库只提交 `.env.example`。如果缺少任一变量，运行时会输出 `缺少 SPOTIFY_CLIENT_ID 或 SPOTIFY_CLIENT_SECRET，跳过 Spotify 搜索`，Apple Music、QQ 音乐和网易云音乐流程会继续执行。

## 快速开始

批量处理 `example` 目录：

```powershell
python fill_ttml_metadata.py example
```

脚本会寻找同名音频和 TTML，例如：

```text
example\2. Disease (Apple Music Live).flac
example\2. Disease (Apple Music Live).ttml
```

匹配成功后会直接更新 TTML，并生成备份：

```text
example\2. Disease (Apple Music Live).ttml.bak
```

## 推荐工作流

先 dry-run 看脚本准备写什么：

```powershell
python fill_ttml_metadata.py example --dry-run
```

确认输出无误后再真实写入：

```powershell
python fill_ttml_metadata.py example
```

## Windows 交互脚本

仓库根目录提供 `fill_metadata.bat`，适合在 Windows 上双击或从 CMD/PowerShell 里运行。

双击运行时，脚本会提示你输入要处理的目录。也可以直接带目录参数运行：

```cmd
fill_metadata.bat "D:\lyrics"
```

PowerShell 里运行：

```powershell
.\fill_metadata.bat "D:\lyrics"
```

如果想直接运行 PowerShell 脚本：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\fill_metadata.ps1 -TargetDir "D:\lyrics"
```

交互脚本会先直接显示 Python 的 dry-run 输出，不会立刻修改文件；只有在预览成功后输入 `Y` 才会进入真实写入。真实写入阶段会分别汇总 Apple Music、QQ 音乐、网易云音乐和 Spotify 最佳候选：输入 `Y` 接受全部最佳结果，输入 `N` 则逐首从 5 个候选里选择；Apple Music 和 Spotify 会按区域/市场分别选择。真实写入仍由 Python 脚本生成 `.bak` 备份。脚本不会自动安装依赖，如果缺少 Python 依赖，请先按上面的环境要求执行 `python -m pip install -r requirements.txt`。

## 单首文件处理

如果音频和 TTML 文件名不一致，或只想处理一首歌，可以显式指定文件：

```powershell
python fill_ttml_metadata.py `
  --audio "example\2. Disease (Apple Music Live).flac" `
  --ttml "example\2. Disease (Apple Music Live).ttml"
```

单首 dry-run：

```powershell
python fill_ttml_metadata.py `
  --audio "example\2. Disease (Apple Music Live).flac" `
  --ttml "example\2. Disease (Apple Music Live).ttml" `
  --dry-run
```

如果没有音频，也可以只指定 TTML。脚本会读取 TTML 已有的 `musicName`、`artists`、`album`，然后复用同一套 QQ 音乐、网易云音乐和 Spotify 搜索流程：

```powershell
python fill_ttml_metadata.py `
  --ttml "example\lyrics-only.ttml" `
  --dry-run
```

## Apple Music 区域

默认区域查找顺序：

1. `cn`
2. `us`
3. `kr`
4. `jp`
5. `tw`

脚本固定查询这五个区域，不再提供单区域、兜底区域或交互输入区域参数。

## 匹配规则

### 音频标签读取

脚本会大小写不敏感读取这些标签：

- 歌名：`title`
- 艺术家：`artist`、`artists`
- 专辑：`album`
- ISRC：`ISRC`
- Apple Music 歌曲 ID 候选：`ITUNESCATALOGID`
- Apple Music 专辑 ID：`ITUNESPLAYLISTID`
- 曲目号：`track`、`tracknumber`
- 碟号：`disc`、`discnumber`

### 多艺术家拆分

脚本支持 Apple Music 常见艺术家格式：

```text
A, B, C & D
```

会写成多个节点：

```xml
<amll:meta key="artists" value="A"/>
<amll:meta key="artists" value="B"/>
<amll:meta key="artists" value="C"/>
<amll:meta key="artists" value="D"/>
```

单个乐队名如 `Florence & The Machine` 不会因为 `&` 被拆开。

### Apple Music 元数据查找

音频标签中的 `ITUNESCATALOGID` 只有看起来像有效歌曲 ID 时才直接写入；TTML-only 模式下也会读取已有 `amll:meta key="appleMusicId"` 和 `isrc`。已有 Apple Music 歌曲 ID 不会让查询提前结束，脚本仍会按 `cn`、`us`、`kr`、`jp`、`tw` 搜索其它区域候选，用来补充本地化歌名、歌手、专辑和 ISRC。

音频有 `ITUNESPLAYLISTID` 时，脚本会先把它作为专辑 ID 查曲目：

```text
https://music.apple.com/{store}/album/{ITUNESPLAYLISTID}
```

脚本参考 `Ame (Apple Music).user.js` 的做法，从 Apple Music 页面提取 Bearer token，不需要 Apple Developer Token。专辑曲目读取请求：

```text
https://amp-api.music.apple.com/v1/catalog/{store}/albums/{albumId}
```

脚本会在 `cn`、`us`、`kr`、`jp`、`tw` 五个区域分别读取专辑曲目。匹配曲目时优先使用碟号和曲目号；缺少曲目号时会使用规范化曲名和时长辅助匹配。

无论是否有专辑 ID，只要有歌名，脚本还会请求 Apple Music catalog search：

```text
https://amp-api.music.apple.com/v1/catalog/{store}/search?types=songs&term=...
```

普通搜索候选按 ISRC、歌名、歌手、专辑、发行日期和时长排序。普通搜索标题明显弱、且音频里同时有歌手、发行日期和时长时，脚本会再搜索 Apple Music artist，分页读取该艺人的 album/single 列表；分页最多读取 10 页、每页 50 张，达到上限会输出 `lookup warning:`。fallback 只接受发行日期匹配、歌手匹配且时长误差不超过 1 秒的曲目。

源歌名不含伴奏标记时，带 `Instrumental`、`伴奏`、`インスト`、`반주` 等标记的 Apple Music 候选会大幅降权且不会自动选中；源歌名本身带伴奏标记时允许匹配。TTML-only 没有音频发行日期和时长，因此不会启用 artist-album fallback，只保留普通搜索候选和手动选择。

dry-run 会展示五区最佳 Apple Music 候选但不询问。真实写入时会先汇总 Apple Music 最佳候选；输入 `Y` 接受全部区域最佳结果，输入 `N` 则按 `CN`、`US`、`KR`、`JP`、`TW` 每区最多展示 5 首候选。选中候选会写入 `appleMusicId`；同一个 Apple Music ID 只写一次，但不同区域返回的歌名、艺术家名、专辑名和 ISRC 会按现有去重规则追加到 `musicName`、`artists`、`album`、`isrc`。

示例验证：

```text
Disease (Apple Music Live) -> 6768201779
```

### QQ 音乐元数据查找

QQ 音乐查找只使用歌名作为搜索关键词。音频模式下歌名来自音频标签；TTML-only 模式下歌名来自已有 `amll:meta key="musicName"`。脚本请求 QQ 音乐移动端搜索接口，读取 `item_song` 候选，要求候选同时具备 songid 和 mid。

匹配时会综合歌名、歌手和专辑：歌名权重最高，歌手其次，专辑再次。TTML-only 模式下歌手和专辑来自已有 `artists`、`album` 元数据。精确匹配优先于包含匹配；包含匹配可处理 `JOLIN蔡依林` 包含 `蔡依林` 这类别名。多歌手会逐个比较。

dry-run 会展示每首歌的最佳 QQ 候选但不询问。真实写入时先汇总所有最佳候选；输入 `Y` 会接受所有最佳候选，输入 `N` 会逐首展示最佳候选加 4 个备选供选择。

### 网易云音乐元数据查找

网易云音乐查找会在 QQ 音乐候选确认后执行。脚本先用歌名搜索网易云，再用已确认的 QQ 音乐候选补充歌名、歌手和专辑线索；如果有歌手和专辑，会额外走网易云歌手专辑回查。匹配前会把繁体和简体文本统一到简体比较，因此 `浪費眼淚` 和 `浪费眼泪` 会被视为同一歌名，但写入 TTML 时仍保留原始返回文本。

脚本按网易云 API 文档的搜索参数并发请求以下公开 API，固定使用单曲搜索第一页最多 100 条候选。优先使用最快返回且能解析出候选的结果；最快响应失败或没有候选时，会继续等待其它 API。直接歌名搜索会同时尝试原歌名和繁简归一后的歌名：

```text
https://music163.xuanmou.com.cn/cloudsearch?keywords={歌名}&limit=100&offset=0&type=1
https://neteasecloudmusicapi-main-api.vercel.app/cloudsearch?keywords={歌名}&limit=100&offset=0&type=1
https://api-enhanced-six-beta.vercel.app/cloudsearch?keywords={歌名}&limit=100&offset=0&type=1
```

如果已确认的 QQ 候选提供歌手和专辑，脚本还会请求：

```text
{网易云 API}/cloudsearch?keywords={歌手}&limit=10&offset=0&type=100
{网易云 API}/artist/album?id={歌手id}&limit=50
{网易云 API}/album?id={专辑id}
```

脚本读取 `result.songs` 和专辑详情里的歌曲候选，解析歌曲 ID、歌名、别名、歌手和专辑，并按歌曲 ID 去重。匹配权重与 QQ 音乐一致：歌名最高，歌手其次，专辑再次。脚本会先对合并后的候选整体排序，再只把匹配度最高的 5 条作为手动备选展示。选中候选后只把网易云歌曲 ID 写入 `ncmMusicId`；候选里的新歌名、别名、歌手和专辑会按现有去重规则追加到对应元数据。

dry-run 会展示每首歌的最佳网易云候选但不询问。真实写入时会在 QQ 音乐确认之后单独确认网易云候选；输入 `Y` 会接受所有最佳候选，输入 `N` 会逐首展示最佳候选加 4 个备选供选择。

### Spotify 元数据查找

Spotify 查找使用官方 Web API：

```text
POST https://accounts.spotify.com/api/token
GET https://api.spotify.com/v1/search?q={query}&type=track&market={market}&limit=20
GET https://api.spotify.com/v1/search?q={artist}&type=artist&market={market}&limit=10
GET https://api.spotify.com/v1/artists/{artistId}/albums?include_groups=album,single&market={market}&limit=10&offset={offset}
GET https://api.spotify.com/v1/albums/{albumId}?market={market}
```

token 请求使用 Client Credentials Flow：`SPOTIFY_CLIENT_ID:SPOTIFY_CLIENT_SECRET` 组成 Basic Auth，body 为 `grant_type=client_credentials`。脚本会优先用 `isrc:{ISRC}` 搜索，然后用宽松普通关键词 `{歌名} {歌手...} {专辑}` 和 `{歌名}` 扩展候选池；如果候选仍不足，再 fallback 到三要素限定搜索：`track:{歌名} artist:{歌手...} album:{专辑}`。如果缺少歌手或专辑，会省略对应限定，但至少要求有歌名。

脚本固定搜索 `US`、`KR`、`JP`、`TW` 四个市场。每个市场独立保留排序后的候选，并各取最佳候选组成默认写入集合；排序按 ISRC、歌名、歌手、专辑匹配度计算，歌名、歌手、专辑都支持包含匹配。普通 track search 没有候选或候选分数明显不足时，如果音频里同时有歌名、歌手、发行日期和时长，脚本会再搜索匹配艺人，读取该艺人最多 30 张最近 album/single，并按发行日期一致、艺人匹配、时长默认 2 秒内接近来接受曲目；这允许 HOYO-MiX 等多地区发行返回不同语言歌名和不同 ISRC。源歌名不是伴奏时，脚本会排除 `Instrumental`、`伴奏`、`インスト`、`반주` 等伴奏候选，避免混入伴奏版。TTML-only 没有音频日期和时长，不启用这个 fallback。

dry-run 会自动展示并选择四区最佳候选但不询问。真实写入时会汇总 Spotify 四区最佳候选；输入 `Y` 接受全部市场最佳结果，输入 `N` 则按市场分别从 5 个候选里选择。选中的候选会写入 `spotifyId`；同一个 track id 只写一次 ID，但不同市场返回的歌名、艺术家名、专辑名和 ISRC 会按现有去重规则追加到 `musicName`、`artists`、`album`、`isrc`。

## 写入结构

脚本不会用 XML 序列化器重写整份 TTML，避免命名空间前缀和属性顺序产生无关变化。

如果根 `<tt>` 是 `xml:lang="zh-Hant"`，脚本会先把根语言改为 `zh-Hans`，只转换 `<body>` 内的主歌词文本节点，并删除 `zh-Hans` replacement 翻译层和 `zh-Latn-pinyin` 音译层。`<head>/<metadata>` 里的文字和 `amll:meta value="..."` 属性不会被繁简转换。

除上述语言规范化外，脚本只会修改已有 `<metadata>...</metadata>` 内部的目标 `amll:meta` 节点，不会补充 `<head>` 或 `<metadata>`。如果根节点缺少 AMLL 命名空间声明，脚本会在根 `<tt>` 上补充 `xmlns:amll="http://www.example.com/ns/amll"`。

如果文件缺少 `<metadata>`，脚本会报错并跳过该文件。

新增的 `amll:meta` 会插入到已有 `<metadata>` 内；有 `<iTunesMetadata>` 时插到它之前，否则插到 `</metadata>` 前：

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

如果 TTML 已经有人工整理过的多语言歌名、艺术家别名或其他真实值，默认会保留，不会替换。新查到且尚未出现过的值会追加到同一个 key 下。

## 输出说明

典型 dry-run 输出：

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

如果文件已经填过所有真实值，再运行会显示 `unchanged` 和 `skipped`：

```text
[unchanged] 2. Disease (Apple Music Live).ttml
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
  skipped: musicName = Disease (Apple Music Live)
  skipped: artists = Lady Gaga
  skipped: album = Apple Music Live: MAYHEM Requiem
  skipped: qqMusicId = 123456, 001abc
  skipped: ncmMusicId = 456789
  skipped: spotifyId = 33e05cb33dd34eddb7d1d3b809dd44e1
  skipped: appleMusicId = 6768201779, 6768201780
  skipped: isrc = USUM72603828
```

## 常见问题

### 批量运行时某个 TTML 被跳过

批量模式优先按同名 stem 自动配对音频和 TTML。

例如下面这组会匹配：

```text
song.flac
song.ttml
```

如果同目录下同时存在同名 `.flac` 和 `.m4a`，批量模式会优先使用 `.flac`：

```text
song.flac
song.m4a
song.ttml
```

下面这组不会自动匹配音频，但会进入 TTML-only 模式；如果 TTML 里已有有效 `musicName`，脚本仍会尝试查找 Apple Music、QQ 音乐、网易云音乐和 Spotify ID。TTML-only 会读取已有 `musicName`、`artists`、`album`、`appleMusicId`、`isrc`，但因为没有音频发行日期和时长，不启用 Apple Music 或 Spotify 的 artist-album fallback：

```text
song-audio.flac
song-lyrics.ttml
```

如果想强制指定音频，请使用 `--audio` 和 `--ttml` 单首模式。

### 找不到 Apple Music ID

常见原因：

- 音频缺少 `ITUNESPLAYLISTID`，且普通 song search 没有合适候选。
- 固定查询的 `cn`、`us`、`kr`、`jp`、`tw` 区域都没有该专辑或歌曲。
- 专辑页存在，但曲名、曲目号、发行日期、歌手或时长和音频标签不一致。
- 最佳候选是伴奏版，但源歌名不含伴奏标记。
- 当前网络无法访问 Apple Music。

### 找不到 QQ 音乐 ID

常见原因：

- 音频或 TTML 缺少歌名，无法发起 QQ 音乐搜索。
- QQ 音乐搜索结果没有同时带 songid 和 mid 的候选。
- 歌名、歌手或专辑标签太不一致，最佳候选需要在真实写入时输入 `N` 手动选择。
- 当前网络无法访问 QQ 音乐搜索接口。

### 找不到网易云音乐 ID

常见原因：

- 音频或 TTML 缺少歌名，无法发起网易云音乐搜索。
- 缺少 `opencc-python-reimplemented`，无法进行繁简归一匹配；请重新执行 `python -m pip install -r requirements.txt`。
- 三个公开网易云 API 都不可用、超时或返回结构变化。
- 网易云音乐搜索结果、歌手专辑列表或专辑详情没有带歌曲 ID 的候选。
- 歌名、歌手或专辑标签太不一致，最佳候选需要在真实写入时输入 `N` 手动选择。

### 找不到 Spotify ID

常见原因：

- 没有在 `.env` 或系统环境变量里配置 `SPOTIFY_CLIENT_ID` 和 `SPOTIFY_CLIENT_SECRET`。
- 音频或 TTML 缺少歌名，无法发起 Spotify 搜索。
- `US`、`KR`、`JP`、`TW` 四个市场都没有带 track id 的候选。
- 当前网络无法访问 Spotify token endpoint 或 Search API。

### 不想覆盖原文件

使用 `--dry-run` 只预览，不写入：

```powershell
python fill_ttml_metadata.py example --dry-run
```

真实写入时脚本总会先生成 `.bak` 备份。

## 测试

运行单元测试：

```powershell
python -B -m unittest discover -s tests
```

运行示例 dry-run：

```powershell
python -B fill_ttml_metadata.py example --dry-run
```

## 许可协议

此仓库使用 **GNU Affero General Public License v3.0（AGPLv3）** 授权。

如果你分发、修改、部署或通过网络提供本仓库代码产生的服务，需要遵守 AGPLv3 的源代码开放要求。仓库的 [LICENSE](https://github.com/kid141252010/ttml-id-match/blob/main/LICENSE) 文件已包含 AGPLv3 全文。
