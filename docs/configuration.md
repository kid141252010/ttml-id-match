# v2 配置指南

本文档介绍项目支持的环境变量和配置项，适用于本地开发和生产环境部署。

---

## 1. 音乐平台凭据 (Provider Credentials)

```text
APPLE_MUSIC_BEARER_TOKEN=
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
```

- **Spotify**：如果缺少 `SPOTIFY_CLIENT_ID` 或 `SPOTIFY_CLIENT_SECRET`，程序会自动跳过 Spotify 的检索并产生警告。
- **Apple Music**：如果未配置 Token，程序会尝试在网页端自动寻找并提取临时 Web Token。

---

## 2. 网络与代理设置 (HTTP Transport & Proxy)

后端所有平台的客户端共享 `httpx.Client` 连接池。超时、重试以及代理设置可以通过以下变量进行集中管理：

```text
TTML_HTTP_TIMEOUT_SECONDS=20
TTML_HTTP_ATTEMPTS=3
TTML_PROXY_ALL=
TTML_PROXY_APPLE_MUSIC=
TTML_PROXY_QQ_MUSIC=
TTML_PROXY_NCM_MUSIC=
TTML_PROXY_SPOTIFY=
```

- **`TTML_HTTP_TIMEOUT_SECONDS`**：请求超时时间（秒，默认 20 秒）。
- **`TTML_HTTP_ATTEMPTS`**：网络请求最大重试次数（默认 3 次）。
- **代理优先级**：各平台专属代理（如 `TTML_PROXY_SPOTIFY`）优先级最高，其次是全局代理 `TTML_PROXY_ALL`，最后是标准环境变量 `HTTPS_PROXY` / `HTTP_PROXY`。

---

## 3. 并发控制 (Concurrency Settings)

可通过限制线程/协程并发数来保护平台接口不被限流。

```text
TTML_SEARCH_WORKERS=3
TTML_SOURCE_APPLE_MUSIC_WORKERS=1
TTML_SOURCE_QQ_MUSIC_WORKERS=2
TTML_SOURCE_SPOTIFY_WORKERS=1
TTML_SOURCE_NCM_MUSIC_WORKERS=1
```

- **`TTML_SEARCH_WORKERS`**（或命令行中的 `--search-workers`）：全局查询的总并发数限制。
- **平台专属 Workers**：为各平台设置的最大并发资源锁（Semaphore），防止单个平台耗尽全局并发预算。
- **并发调度机制**：在 v2 中，为确保稳定性，去除了多层级并发池。依赖项会串行执行（例如 QQ 音乐检索完成后，才会启动依赖它的网易云音乐适配器进行关联检索）。

---

## 4. 存储后端 (Storage Backend)

根据运行环境选择本地文件存储或云端存储：

### 本地开发 (Local Storage)

```text
ID_MATCH_STORAGE_BACKEND=local
ID_MATCH_V2_ROOT=.codex-tmp/id-match-v2
ID_MATCH_WORK_ROOT=
```

- **`ID_MATCH_STORAGE_BACKEND`**：设为 `local` 以使用本地文件系统。
- **`ID_MATCH_V2_ROOT`**：用于保存本地会话快照、上传内容和输出结果的临时根目录。

### Vercel Serverless 部署 (Vercel Blob & Redis)

当环境变量中存在 `VERCEL` 时，程序默认切换到 `vercel` 后端，但建议显式配置以下参数：

```text
ID_MATCH_STORAGE_BACKEND=vercel
BLOB_READ_WRITE_TOKEN=
KV_REST_API_URL=
KV_REST_API_TOKEN=
```

- **`BLOB_READ_WRITE_TOKEN`**：Vercel Blob 的读写凭据，用于存取上传的音频、TTML 和处理生成的结果。
- **`KV_REST_API_URL` & `KV_REST_API_TOKEN`**（或 Upstash Redis 的 `UPSTASH_REDIS_REST_URL` & `UPSTASH_REDIS_REST_TOKEN`）：用于高速存取会话索引。由于 Serverless 是无状态的，必须使用此数据库来保存会话映射状态。

---

## 5. API 安全与资源治理

网页端创建会话时会获得一次性 `session_token`。后续上传、预览、Apply、删除和下载请求都必须携带 `Authorization: Bearer <session_token>`；服务端只保存令牌摘要。

```text
ID_MATCH_RATE_LIMIT_REQUESTS=240
ID_MATCH_RATE_LIMIT_WINDOW_SECONDS=60
ID_MATCH_SESSION_CREATE_LIMIT=10
ID_MATCH_SESSION_CREATE_WINDOW_SECONDS=3600
ID_MATCH_TRUST_PROXY_HEADERS=
ID_MATCH_MAX_FILES=40
ID_MATCH_MAX_PAIRS=20
ID_MATCH_MAX_FILE_BYTES=67108864
ID_MATCH_MAX_SESSION_BYTES=268435456
ID_MATCH_MAX_PREVIEW_JOBS=5
ID_MATCH_MAX_APPLIES=5
ID_MATCH_SESSION_TTL_SECONDS=86400
ID_MATCH_GC_GRACE_SECONDS=86400
CRON_SECRET=
```

- 通用限流默认为每 IP 每分钟 240 次请求，创建会话额外限制为每 IP 每小时 10 次。Vercel 后端使用 Redis 原子计数，本地后端使用进程内计数。
- `ID_MATCH_TRUST_PROXY_HEADERS` 在 Vercel 自动启用，本地默认关闭；只有部署在可信反向代理之后才应手动启用。
- 单会话默认最多 40 个文件、20 个 TTML pair、256 MiB，总计最多 5 次预览和 5 次 Apply；单文件上限为 64 MiB。
- 会话自创建起固定保留 24 小时，不因访问续期。Redis 会保留额外 24 小时 GC 宽限期，以便 Cron 删除 Blob 对象后再移除索引。
- Vercel 部署必须设置高熵 `CRON_SECRET`。`vercel.json` 中的每小时任务会用该值访问清理接口。

---

## 6. 前端接口配置 (Frontend Configuration)

前端网络交互基准路径默认为 `/api/v2`。如需使用独立的后端 API 部署：

```text
VITE_API_BASE=https://api.example.com/api/v2
```

如果在后端修改了 API 数据契约，请在前端重新生成 DTO 类型定义文件：

```powershell
npm --prefix web run openapi:generate
```
