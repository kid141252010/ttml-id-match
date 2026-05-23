# TTML 元数据快速填充脚本

这个仓库提供一个 Python CLI 脚本，用音频文件里的标签快速填充 TTML 歌词文件中的 AMLL 元数据。

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
<amll:meta key="isrc" value="..."/>
<amll:meta key="appleMusicId" value="..."/>
```

默认行为：

- 追加缺失的元数据值，不覆盖已有真实值，重复值会跳过。
- `value="*"` 或空值会被视为占位符并替换。
- 写入前自动生成 `.bak` 备份。
- 批量模式按同名文件配对音频和 TTML；同名 `.flac` 和 `.m4a` 同时存在时优先使用 `.flac`。
- 多艺术家会拆成多个 `artists` 元数据节点。
- `ITUNESCATALOGID` 若明显不是歌曲 ID，例如示例中的 `1`，不会直接写入。
- 会用 `ITUNESPLAYLISTID` 作为 Apple Music 专辑 ID，在 `cn`、`tw`、`jp`、`kr`、`us` 五个区域查找曲目元数据。

## 环境要求

- Python 3.10 或更新版本。
- 网络访问 Apple Music，用于从专辑 ID 查找歌曲 ID。
- Python 依赖：`mutagen`。

安装依赖：

```powershell
python -m pip install -r requirements.txt
```

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

交互脚本会先直接显示 Python 的 dry-run 输出，不会立刻修改文件；只有在预览成功后输入 `Y` 才会真实写入。真实写入仍由 Python 脚本生成 `.bak` 备份。脚本不会自动安装依赖，如果缺少 `mutagen`，请先按上面的环境要求执行 `python -m pip install -r requirements.txt`。

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

## Apple Music 区域

默认区域查找顺序：

1. `cn`
2. `tw`
3. `jp`
4. `kr`
5. `us`

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

音频标签中的 `ITUNESCATALOGID` 只有看起来像有效歌曲 ID 时才直接写入。

多地区元数据查找使用 `ITUNESPLAYLISTID` 作为专辑 ID：

```text
https://music.apple.com/{store}/album/{ITUNESPLAYLISTID}
```

脚本参考 `Ame (Apple Music).user.js` 的做法，从 Apple Music 页面提取 Bearer token，然后请求：

```text
https://amp-api.music.apple.com/v1/catalog/{store}/albums/{albumId}
```

脚本会在 `cn`、`tw`、`jp`、`kr`、`us` 五个区域分别读取专辑曲目。匹配曲目时优先使用碟号和曲目号；缺少曲目号时会使用规范化曲名和时长辅助匹配。

示例验证：

```text
Disease (Apple Music Live) -> 6768201779
```

## 写入结构

脚本只会修改已有 `<metadata>...</metadata>` 内部的目标 `amll:meta` 节点，不会重排或重写 TTML 其它内容，也不会补充 `<head>` 或 `<metadata>`。如果根节点缺少 AMLL 命名空间声明，脚本会在根 `<tt>` 上补充 `xmlns:amll="http://www.example.com/ns/amll"`。

如果文件缺少 `<metadata>`，脚本会报错并跳过该文件。

新增的 `amll:meta` 会插入到已有 `<metadata>` 内；有 `<iTunesMetadata>` 时插到它之前，否则插到 `</metadata>` 前：

```xml
<metadata>
  <ttm:agent type="person" xml:id="v1"/>
  <amll:meta key="musicName" value="..."/>
  <amll:meta key="artists" value="..."/>
  <amll:meta key="album" value="..."/>
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
  appleMusicId: 6768201779, 6768201780
  appleMusicSources: album:cn:track, album:tw:track, album:jp:track, album:kr:track, album:us:track
  added: musicName = Disease (Apple Music Live), Disease - Apple Music Live
  added: artists = Lady Gaga
  added: album = Apple Music Live: MAYHEM Requiem, Apple Music Live: MAYHEM
  added: appleMusicId = 6768201779, 6768201780
  added: isrc = USUM72603828
```

如果文件已经填过所有真实值，再运行会显示 `unchanged` 和 `skipped`：

```text
[unchanged] 2. Disease (Apple Music Live).ttml
  audio: 2. Disease (Apple Music Live).flac
  appleMusicId: 6768201779, 6768201780
  appleMusicSources: album:cn:track, album:tw:track, album:jp:track, album:kr:track, album:us:track
  skipped: musicName = Disease (Apple Music Live)
  skipped: artists = Lady Gaga
  skipped: album = Apple Music Live: MAYHEM Requiem
  skipped: appleMusicId = 6768201779, 6768201780
  skipped: isrc = USUM72603828
```

## 常见问题

### 批量运行时某个 TTML 被跳过

批量模式只按同名 stem 自动配对。

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

下面这组不会自动匹配：

```text
song-audio.flac
song-lyrics.ttml
```

这种情况请使用 `--audio` 和 `--ttml` 单首模式。

### 找不到 Apple Music ID

常见原因：

- 音频缺少 `ITUNESPLAYLISTID`。
- 固定查询的 `cn`、`tw`、`jp`、`kr`、`us` 区域都没有该专辑。
- 专辑页存在，但曲名或曲目号和音频标签不一致。
- 当前网络无法访问 Apple Music。

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
