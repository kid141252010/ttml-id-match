# 常见问题与故障排查

本文档列出了在使用 TTML 元数据快速填充脚本和 Web 服务时可能遇到的常见问题及其解决方法。

## 1. 批量运行时某个 TTML 被跳过

- **配对机制**：批量模式优先按同名 stem 自动配对音频和 TTML。
  - 例如，以下结构可以自动配对：
    ```text
    song.flac
    song.ttml
    ```
  - 如果同目录下同时存在同名 `.flac` 和 `.m4a`，批量模式会优先使用 `.flac`：
    ```text
    song.flac
    song.m4a
    song.ttml
    ```
- **TTML-only 模式限制**：如果文件名不匹配（例如 `song-audio.flac` 和 `song-lyrics.ttml`），音频将不会被自动读取，脚本会转为 **TTML-only 模式**。
  - 在 TTML-only 模式下，只要 TTML 中已有有效的 `musicName`，脚本仍会尝试搜索并匹配。
  - 但由于没有音频中的发行日期和时长等信息，脚本不会启用 Apple Music 或 Spotify 的艺人-专辑降级检索 (Artist-Album Fallback)。
- **解决方法**：如果需要强制配对非同名音频与 TTML，请使用 `--audio` 和 `--ttml` 单首模式：
  ```powershell
  python fill_ttml_metadata.py --audio "path/to/audio.flac" --ttml "path/to/lyrics.ttml"
  ```

## 2. 找不到 Apple Music ID

常见原因：
- 音频标签缺少 `ITUNESPLAYLISTID`（专辑ID），且普通歌名搜索（song search）未返回合适候选。
- 目标歌曲未在脚本固定查询的五个区域（`cn`、`us`、`kr`、`jp`、`tw`）上架。
- Apple Music 上的歌曲元数据（歌名、曲目号、发行日期、歌手或时长）与音频文件中的标签信息相差过大。
- 搜索出的最佳候选为伴奏版（带有 `Instrumental` 等标记），但您的源歌名中并不包含伴奏标记，导致脚本将其过滤。
- 未配置 `APPLE_MUSIC_BEARER_TOKEN`，且此时 Apple Music 页面已不再暴露可提取的临时 Web Token，或您配置的 Token 已经过期。
- 当前局域网/网络环境无法正常访问 Apple Music 服务（需要检查代理配置）。

## 3. 找不到 QQ 音乐 ID

常见原因：
- 音频或 TTML 文件中均缺少歌名（`musicName`/`title`），无法发起搜索。
- QQ 音乐搜索结果中没有同时包含 `songid` 和 `mid` 的候选。
- 音频文件或 TTML 里的歌名、歌手或专辑等标签与 QQ 音乐库的名称差异过大（例如多出一个特殊字符）。此时若使用真实写入模式，建议输入 `N` 进行手动交互选择。
- 当前网络无法正常访问 QQ 音乐的移动端搜索接口。

## 4. 找不到网易云音乐 ID

常见原因：
- 音频或 TTML 文件中均缺少歌名，无法发起搜索。
- 本地 Python 环境缺少 `opencc-python-reimplemented` 依赖，无法进行繁简归一匹配。请运行以下命令重新安装依赖：
  ```powershell
  python -m pip install -r requirements.txt
  ```
- 用于检索的三个公开网易云 API 均不可用、超时或返回的结构发生了变化。
- 网易云音乐搜索结果、歌手专辑列表或专辑详情里没有带有效歌曲 ID 的候选。
- 标签差异过大。同样建议在真实写入时输入 `N` 进行手动交互选择。

## 5. 找不到 Spotify ID

常见原因：
- 未在 `.env` 或系统环境变量中配置 `SPOTIFY_CLIENT_ID` 和 `SPOTIFY_CLIENT_SECRET`。
- 音频或 TTML 文件中均缺少歌名，无法发起检索。
- 目标歌曲未在 `US`、`KR`、`JP`、`TW` 四个市场发行。
- 当前网络无法访问 Spotify Token 验证或搜索 API。

## 6. 不想覆盖原 TTML 文件

- **备份机制**：真实写入时，脚本在修改前总会先生成一份 `.bak` 备份文件。
- **只预览不写入**：如果仅想查看匹配结果而不做任何修改，请使用 `--dry-run` 参数：
  ```powershell
  python fill_ttml_metadata.py example --dry-run
  ```
