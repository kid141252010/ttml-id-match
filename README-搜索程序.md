# 音乐元数据搜索 · ttml-id-match

> **本项目是基于 [kid141252010/ttml-id-match](https://github.com/kid141252010/ttml-id-match) 的改造版。**
> 原项目是命令行的元数据自动填充工具；本改造把它**可视化**了 —— 增加本地 Web 界面（搜索、地区多选、
> 一键复制、导出 JSON）并打包成免安装的独立程序，同时新增了 Apple 官方 ISRC、多地区搜索与多区自动回退等能力。
> 感谢原作者的工作。原项目文档见 [README-原版.md](README-原版.md)。

按 **歌名 / 艺人 / 专辑** 搜索各平台音乐，拿到 **Track ID、官方 ISRC、专辑名、时长、发行日** 等元数据，
方便填写 TTML（`amll:meta`）等场景。

**全部数据源免密钥**（Apple Music / Deezer / MusicBrainz）· **多地区** · **自带独立可执行文件**（无需安装 Python）。

---

## ✨ 功能

| 功能 | 说明 |
|---|---|
| **多地区搜索** | TW / HK / JP / KR / CN / US / CA / GB / SG / MY / DE / FR / AU |
| **官方 ISRC** | Apple Music 网页接口（`amp-api`）直接返回官方 ISRC，**免密钥** |
| **多区自动回退** | 目标区搜不到时自动依次尝试 `us / jp / tw / hk`，**保证有结果** |
| **多数据源** | Apple Music · Deezer · MusicBrainz ·（Spotify —— **尚未开发完成**，见下） |
| **一键复制** | 复制 ID / 歌名 / ISRC / 专辑 / 整行 |
| **导出 JSON** | 结果一键导出 |
| **命令行工具** | `apple_isrc.py`（Apple 官方 ISRC）· `spotify_markets.py`（Spotify 多地区） |

---

## 🚀 快速开始（免安装）

1. 到 [**Releases**](../../releases) 下载 `音乐元数据搜索程序.zip`
2. 解压
3. 双击 **`音乐元数据搜索.exe`** → 浏览器自动打开 `http://127.0.0.1:8765/`
4. 输入歌名 → 选数据源与地区 → 搜索 → 点按钮复制所需字段

> 无需安装 Python、无需任何密钥。
> 首次启动若提示地址被占用，请先关闭已在运行的实例。

---

## 🧑‍💻 从源码运行

```bash
pip install pyinstaller        # 仅打包时需要
python serve_search.py         # 启动本地服务（默认 http://127.0.0.1:8765/）
```

浏览器打开提示的地址即可。源码方式同样无需任何密钥。

### 命令行

```bash
# Apple 官方 ISRC（免密钥）
python apple_isrc.py 535824738 tw          # → ISRC: TWK970300503
python apple_isrc.py --json 535824738
python apple_isrc.py --batch ids.txt

# Spotify 多地区（需自备 Client ID，见下）
python spotify_markets.py --query "晴天 周杰伦" --markets TW,HK,JP,KR,US
python spotify_markets.py --isrc TWK970300503 --markets TW,JP,US
python spotify_markets.py --id 3n3Pam7vgaVa1iaRUc9Lp --markets TW,JP
```

### 一键重建 + 测试 + 打包

```bash
python 一键重建打包.py
```
（自动：结束占用进程 → 清理 → PyInstaller 重建 → 启动 → 测试多个地区 → 打包 zip）

---

## 🌍 地区说明

| 地区 | 备注 |
|---|---|
| TW / HK | 华语内容覆盖最全 ✔ |
| JP / KR | 日/韩语内容；建议用**当地语言**关键词，命中率更高 |
| CN | 需使用 `amp-api`（本项目已内置）；未上架内容仍搜不到（版权原因） |
| US / CA / GB | 欧美内容；自动回退的主要来源 |

> 同一首歌在不同 storefront 的**歌名/艺人名可能被本地化**（例：周杰伦在 KR 区显示为 `주걸륜`，
> 《晴天》在 JP 区显示为 `Sunny Day`）。因此搜不到时建议换用当地语言关键词，或依赖**自动回退**。

---

## 🔑 关于 Spotify（⚠️ 尚未开发完成，暂不可用）

> **Spotify 部分还没开发好，目前请忽略它。** 页面上保留了一个「Spotify 多地区查询」面板与「登录 Spotify」按钮，
> 但授权链路尚未跑通（Spotify 官方 API 需要自行注册 App，且对账号有额外要求），**现在点了不会成功**，不影响其它功能。
> 后续完成后再更新此处说明。

<details>
<summary>（占位：完成后的使用说明）</summary>

Spotify 官方 API 需要自备 **Client ID**（[Dashboard](https://developer.spotify.com/dashboard)）。
本项目计划采用 **PKCE** 授权，**不需要 Client secret**：

1. 在 Dashboard 创建 App
2. **Redirect URI** 填：`http://127.0.0.1:8765/callback`（一字不差）
3. 勾选 **Web API**
4. 复制 **Client ID** → 写入 `.env`：

```ini
SPOTIFY_CLIENT_ID=你的ClientID
```

5. 重启程序 → 页面上点「登录 Spotify」授权一次

</details>

> **不想等 Spotify 也完全不影响使用** —— Apple / Deezer / MusicBrainz 全部免密钥可用。
> 也可以只拿 **ISRC**，在 Spotify 搜索框粘贴 `isrc:XXXXXXXXXXXX` 直接定位歌曲（无需 API）。

---

## ⚙️ 配置（`.env`，全部可选）

```ini
# 可选：Spotify（不填则只搜索免密钥数据源）
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=

# 可选：自定义回调地址
SPOTIFY_REDIRECT=http://127.0.0.1:8765/callback
```

> `.env` 与 `.spotify-token.json` **已加入 `.gitignore`，请勿提交。**

---

## 📁 目录结构

```
ttml-id-match/
├─ serve_search.py           # 本地服务 + 网页 API（Apple/Deezer/MusicBrainz/Spotify）
├─ search_metadata.html      # 网页界面（单文件）
├─ search_metadata.py        # 命令行版搜索
├─ apple_isrc.py             # Apple 官方 ISRC（免密钥）
├─ spotify_markets.py        # Spotify 多地区查询（ISRC 跨区 / Track Relinking）
├─ 一键重建打包.py            # 一键：重建 + 测试 + 打包
├─ fill_ttml_metadata.py     # TTML 元数据填充辅助
├─ ttml_metadata/            # 元数据处理模块
├─ docs/                     # 文档
└─ .env.example
```

---

## 🧱 实现要点

- **Apple 官方 ISRC**：从 `music.apple.com` 前端 JS 中提取长期 JWT，调用 `amp-api.music.apple.com/v1/catalog/{storefront}/search` 与 `/songs/{id}`，返回结果自带 `isrc`。
- **多区回退**：目标 storefront 无结果时依次尝试 `us / jp / tw / hk`。
- **Track Relinking**：`spotify_markets.py` 通过 `market` 参数与 `linked_from` 判断可播性与重定向。
- **限速友好**：Spotify 429 读取 `Retry-After` 退避；MusicBrainz 限制 1 请求/秒。

---

## ⚠️ 免责声明

- 本项目仅调用各平台的**公开接口**，用于个人整理音乐元数据。
- 数据版权归各平台与版权方所有，请勿用于商业用途或大规模抓取。
- 接口可能随平台调整而变化，若失效请提交 Issue。

---

## 📄 许可

如无特别说明，遵循仓库原有许可。
打包产物由 [PyInstaller](https://pyinstaller.org/) 生成。
