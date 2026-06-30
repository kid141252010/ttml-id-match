# 配置说明

本文档说明 CLI 与 Web 后端共用的运行配置。CLI 本地使用时可以只配置音乐服务凭据；Web 部署到 Vercel 时还需要配置持久化存储。

## Apple Music

Apple Music catalog API 需要 Web bearer token。程序会优先读取 `APPLE_MUSIC_BEARER_TOKEN`，没有配置时再尝试从 Apple Music 页面脚本里提取临时 token。

```text
APPLE_MUSIC_BEARER_TOKEN=
```

该变量可以包含 `Bearer ` 前缀，也可以只填 token 本体；程序发送请求时会统一生成 `Authorization: Bearer <token>`。不要把真实 token 提交到仓库。

## Spotify

Spotify 搜索使用官方 Web API 的 Client Credentials Flow。缺少任一变量时，程序会跳过 Spotify，不影响 Apple Music、QQ 音乐和网易云音乐。

```text
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
```

本地开发可以复制 `.env.example` 为 `.env`。程序会先读取当前目录的 `.env`，再用系统环境变量覆盖同名值。

## 代理

所有上游搜索都支持代理。来源级代理优先于全局代理；未设置来源级代理时，程序会依次退回到 `TTML_PROXY_ALL`、`HTTPS_PROXY`、`HTTP_PROXY`。

```text
TTML_PROXY_ALL=
TTML_PROXY_APPLE_MUSIC=
TTML_PROXY_QQ_MUSIC=
TTML_PROXY_NCM_MUSIC=
TTML_PROXY_SPOTIFY=
```

常见用法：

```text
TTML_PROXY_APPLE_MUSIC=http://127.0.0.1:7890
TTML_PROXY_SPOTIFY=http://127.0.0.1:7890
```

代理值为空、`none`、`off`、`false`、`0` 时会被视为未配置。

## 批量并发

CLI 批量搜索默认使用 3 个外层 worker：

```powershell
python fill_ttml_metadata.py example --dry-run --search-workers 3
```

如果上游服务限流，降低到 1：

```powershell
python fill_ttml_metadata.py example --dry-run --search-workers 1
```

Web 后端默认沿用相同并发值。单个工作项内部会并行搜索 Apple Music、QQ 音乐和 Spotify；网易云音乐会在 QQ 音乐候选确认后执行。

除外层批量 worker 外，部分来源还有内部请求并发，用于并行互不依赖的区域、市场或查询：

```text
TTML_APPLE_MUSIC_WORKERS=3
TTML_SPOTIFY_MARKET_WORKERS=2
TTML_NCM_QUERY_WORKERS=2
```

- `TTML_APPLE_MUSIC_WORKERS` 控制 Apple Music storefront 并行数，默认 3。
- `TTML_SPOTIFY_MARKET_WORKERS` 控制 Spotify market 并行数，默认 2。
- `TTML_NCM_QUERY_WORKERS` 控制网易云单个 API base 内的歌名、歌手和专辑详情查询并行数，默认 2。

总请求压力约等于外层 `--search-workers` 乘以各来源内部 worker。遇到 429、超时或上游不稳定时，先把对应来源变量降到 `1`；如果仍然限流，再把 CLI 的 `--search-workers` 或 Web 后端的 `search_workers` 降到 `1`。

## Web 存储后端

本地开发默认使用磁盘临时目录：

```text
ID_MATCH_STORAGE_BACKEND=local
```

Vercel 部署必须改成持久化后端：

```text
ID_MATCH_STORAGE_BACKEND=vercel
BLOB_READ_WRITE_TOKEN=
KV_REST_API_URL=
KV_REST_API_TOKEN=
```

如果使用 Vercel Redis / Upstash 的新变量名，也可以配置：

```text
UPSTASH_REDIS_REST_URL=
UPSTASH_REDIS_REST_TOKEN=
```

`vercel` 后端会把上传文件和结果文件写入 Vercel Blob，把会话索引写入 Redis/KV REST。不要在 Vercel 上使用 `local`，否则 Serverless 函数冷启动或换实例后会丢会话。

## 前端 API 地址

前端默认请求同源 `/api`。本地开发时由 Vite 代理到后端：

```text
http://127.0.0.1:5173 -> /api -> http://127.0.0.1:8000
```

如果前后端分开部署，设置：

```text
VITE_API_BASE=https://your-api.example.com/api
```

只看界面时可启用 mock：

```powershell
$env:VITE_USE_MOCK_API="1"
npm --prefix web run dev
```
