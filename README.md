# TTML 元数据快速填充脚本

这个仓库提供一个 Python CLI 脚本和 Web 服务，用于使用音频文件中的标签或 TTML 歌词文件中已有的基础信息，快速检索并填充 TTML 文件中的 AMLL 元数据。

目前脚本支持从各大音乐平台（Apple Music、QQ 音乐、网易云音乐、Spotify）检索并写入以下 AMLL 元数据：

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

---

## 快速开始

### 1. 环境准备

- Python 3.10 或更新版本。
- 确保您的网络能够访问 Apple Music、QQ 音乐、网易云音乐的公开 API 以及 Spotify API（如需检索 Spotify 元数据）。

安装 Python 依赖库：
```powershell
python -m pip install -r requirements.txt
```

### 2. 音乐服务凭据配置

复制 `.env.example` 为 `.env` 并按需填写凭据：
```text
APPLE_MUSIC_BEARER_TOKEN=your_apple_music_token_here
SPOTIFY_CLIENT_ID=your_spotify_client_id_here
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret_here
```
*(注：如果缺少凭据，对应平台在检索时会被自动跳过)*

### 3. 一键运行

对 `example` 目录下的音频和 TTML 文件进行匹配 dry-run（仅预览，不写入文件）：
```powershell
python fill_ttml_metadata.py example --dry-run
```

确认预览输出的匹配结果无误后，执行真实写入（写入前脚本会自动生成 `.bak` 备份文件）：
```powershell
python fill_ttml_metadata.py example
```

### 并发和限流

批量搜索默认会并行处理多个文件，并在 Apple Music 区域、Spotify 市场和网易云查询内部做受控并行。遇到上游限流或网络不稳定时，可先在 `.env` 中把对应的 `TTML_APPLE_MUSIC_WORKERS`、`TTML_SPOTIFY_MARKET_WORKERS` 或 `TTML_NCM_QUERY_WORKERS` 降为 `1`；仍不稳定时再使用 `--search-workers 1` 降低外层批量并发。完整说明见 [配置指南](docs/configuration.md)。

---

## 文档指南

为保持主文档清爽，我们将更详尽的内容整理到了以下文档中：

- ⚙️ **[配置指南 (docs/configuration.md)](docs/configuration.md)**：包含代理配置、并发数控制、存储后端及前端 API 的详细配置参数。
- 💻 **[CLI 进阶使用 (docs/cli_usage.md)](docs/cli_usage.md)**：包含单首音频/TTML 歌曲的处理、Windows 交互脚本（批处理/PowerShell）的使用说明。
- 🧠 **[匹配规则与写入结构 (docs/matching_rules.md)](docs/matching_rules.md)**：包含详细的音频标签读取逻辑、多艺术家拆分标准、各平台的匹配与排序权重、XML 结构变更及 dry-run 典型输出说明。
- 🌐 **[Web GUI 运行与部署 (docs/web_gui.md)](docs/web_gui.md)**：本地运行 Vue 3 + FastAPI Web 界面的步骤及 Mock API 调试模式。
- 🚀 **[Vercel 部署教程 (docs/deployment/vercel.md)](docs/deployment/vercel.md)**：将 Web GUI 前后端一键部署并在 Vercel 上使用 KV 和 Blob 持久化的完整教程。
- ❓ **[常见问题与故障排查 (docs/faq.md)](docs/faq.md)**：汇总了匹配项跳过、各平台 ID 无法找回的常见原因及排查方案。

---

## 测试

运行项目的单元测试：
```powershell
python -B -m unittest discover -s tests
```

---

## 许可协议

此仓库使用 **GNU Affero General Public License v3.0 (AGPLv3)** 授权。详情见 [LICENSE](LICENSE) 文件。
