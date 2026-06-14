# Vercel 部署教程

本文档维护 TTML ID Match 的 Vercel 部署流程。当前仓库使用 Vite 静态前端 + FastAPI Python Function：`web/` 构建成静态站点，`/api/*` 由 `api/index.py` 转发到 `server.main:app`。

Vercel Python Runtime 会从 `requirements.txt` 等依赖文件识别 Python 应用，并在 `api/` 等目录查找暴露顶层 `app` 的 ASGI/WSGI 入口；本仓库的 `api/index.py` 正是这个入口。Vercel Blob 适合运行时上传和下载文件；本项目用它保存用户上传和生成的 TTML 结果。Deploy Button 支持在创建项目时要求用户填写环境变量，但敏感变量不应设置默认值。

参考文档：

- [Vercel Python Runtime](https://vercel.com/docs/functions/runtimes/python)
- [Vercel Blob](https://vercel.com/docs/vercel-blob)
- [Deploy Button](https://vercel.com/docs/deploy-button)

## 1. 准备 Vercel 资源

1. 在 Vercel 创建或选择一个项目。
2. 创建一个 Vercel Blob store，建议选择离函数区域和主要用户更近的 region。Blob store 创建后访问模式和区域不能随意当作运行时配置切换。
3. 创建 Redis/KV 存储。项目支持两组变量：
   - `KV_REST_API_URL` + `KV_REST_API_TOKEN`
   - `UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN`
4. 可选：准备 Spotify Developer 凭据。如果不配置，Spotify 搜索会跳过。

## 2. 配置环境变量

在 Vercel Project Settings -> Environment Variables 中配置：

```text
ID_MATCH_STORAGE_BACKEND=vercel
BLOB_READ_WRITE_TOKEN=...
KV_REST_API_URL=...
KV_REST_API_TOKEN=...
```

或使用 Redis / Upstash 变量：

```text
ID_MATCH_STORAGE_BACKEND=vercel
BLOB_READ_WRITE_TOKEN=...
UPSTASH_REDIS_REST_URL=...
UPSTASH_REDIS_REST_TOKEN=...
```

可选变量：

```text
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
TTML_PROXY_APPLE_MUSIC=
TTML_PROXY_QQ_MUSIC=
TTML_PROXY_NCM_MUSIC=
TTML_PROXY_SPOTIFY=
```

不要把 `BLOB_READ_WRITE_TOKEN`、Redis/KV token、Spotify secret 写进仓库。

### 环境变量用途与获取过程

#### `ID_MATCH_STORAGE_BACKEND`

用途：选择后端会话存储方式。本地开发可以用 `local`；部署到 Vercel 必须用 `vercel`。

获取方式：这是本项目自己的配置，不需要向第三方申请。直接在 Vercel 环境变量里新增：

```text
ID_MATCH_STORAGE_BACKEND=vercel
```

为什么必须设置：Vercel Function 的本地临时目录不能当作长期会话存储。使用 `local` 时，上传文件和结果只在当前函数实例里，冷启动、换实例或重新部署后可能出现 `session not found`。

#### `BLOB_READ_WRITE_TOKEN`

用途：允许后端读写 Vercel Blob。本项目会把用户上传文件、处理后的 TTML、下载 zip 写入 Blob。Vercel Blob 的私有存储读取需要认证，写入/删除也需要有效 token。

获取过程：

1. 打开 [Vercel Dashboard](https://vercel.com/dashboard)。
2. 进入当前项目，打开 Storage。
3. 选择 Create Database / Create Store，然后选择 Blob。
4. 创建 Blob Store。访问模式建议选 private，因为上传内容可能包含用户自己的歌词和音频元数据；区域选择靠近你的 Vercel Function 和主要用户的位置。Blob store 的访问模式和 region 创建后不应当作运行时配置随意切换。
5. 创建后把 Blob Store 连接到当前项目。Vercel 通常会自动给项目注入 `BLOB_READ_WRITE_TOKEN`。
6. 如果没有自动注入，进入 Blob Store 的设置或连接信息，复制 Read Write Token，手动添加到 Project Settings -> Environment Variables：

```text
BLOB_READ_WRITE_TOKEN=vercel_blob_rw_...
```

填完后重新部署。Vercel 环境变量变更只对新的 deployment 生效，不会修改已经部署出去的旧版本。

#### `KV_REST_API_URL` / `KV_REST_API_TOKEN`

用途：保存会话索引。Blob 保存文件本体，但后端还需要知道某个 session 对应哪些上传文件、哪些输出文件、Blob path 是什么。这个小型 JSON 索引用 Redis/KV REST 保存。

获取过程：

1. 打开 Vercel Dashboard -> 当前项目 -> Storage。
2. 创建 Redis / KV / Upstash Redis 存储，并连接到当前项目。
3. 在连接后的环境变量列表里查找 REST API 变量。
4. 如果你看到的是旧式 KV 变量，把它们填入：

```text
KV_REST_API_URL=https://...
KV_REST_API_TOKEN=...
```

5. 如果 Vercel 自动注入到了项目环境变量里，不需要重复手填；确认 Production 环境可用即可。
6. 填完后重新部署。

#### `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN`

用途：和 `KV_REST_API_URL` / `KV_REST_API_TOKEN` 相同，也是保存会话索引。本项目支持这组变量是为了兼容新的 Vercel Redis / Upstash Redis 集成命名。

获取过程：

1. 打开 Vercel Dashboard -> 当前项目 -> Storage 或 Marketplace。
2. 创建或连接 Upstash Redis / Vercel Redis。
3. 在资源的 `.env` 或连接说明里复制 REST URL 和 REST Token。
4. 添加到 Project Settings -> Environment Variables：

```text
UPSTASH_REDIS_REST_URL=https://...
UPSTASH_REDIS_REST_TOKEN=...
```

二选一即可：如果已经有 `KV_REST_API_URL` / `KV_REST_API_TOKEN`，不需要再填 `UPSTASH_REDIS_REST_*`。代码会先读 `KV_REST_API_*`，没有时再读 `UPSTASH_REDIS_REST_*`。

#### `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`

用途：启用 Spotify Web API 搜索，用于补充 `spotifyId` 和 Spotify 返回的 ISRC、地区候选。没有这两个变量时，Spotify 搜索会跳过，Apple Music、QQ 音乐和网易云音乐仍会继续。

获取过程：

1. 打开 [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)。
2. 登录 Spotify 账号。
3. 点击 Create app。
4. 填写 App name、description。Redirect URI 对本项目的 Client Credentials Flow 不重要，但 Spotify 创建应用时可能要求填写；可以填你的站点地址或 `http://localhost:5173/callback`。
5. 创建后进入应用 Settings。
6. 复制 Client ID。
7. 点击 View client secret，复制 Client Secret。
8. 添加到 Vercel Project Settings -> Environment Variables：

```text
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
```

不要把 Client Secret 提交到仓库。

#### `TTML_PROXY_*`

用途：控制后端访问上游音乐服务时是否走代理。通常 Vercel 部署先不填；只有在某个来源访问不稳定、超时或被上游限制时再配置。

变量优先级：

1. 来源级代理，例如 `TTML_PROXY_SPOTIFY`
2. 全局代理 `TTML_PROXY_ALL`
3. 标准环境变量 `HTTPS_PROXY`
4. 标准环境变量 `HTTP_PROXY`

可用变量：

```text
TTML_PROXY_ALL=
TTML_PROXY_APPLE_MUSIC=
TTML_PROXY_QQ_MUSIC=
TTML_PROXY_NCM_MUSIC=
TTML_PROXY_SPOTIFY=
```

示例：

```text
TTML_PROXY_SPOTIFY=http://username:password@proxy.example.com:8080
TTML_PROXY_APPLE_MUSIC=http://proxy.example.com:8080
```

获取方式：代理地址来自你自己的代理服务商或自建出口，不由 Vercel 提供。不要把只能在本机访问的 `127.0.0.1:7890` 填到 Vercel 生产环境；Vercel Function 里访问不到你本机的 localhost。

### 在 Vercel 里添加变量

1. 打开 Vercel Dashboard。
2. 进入项目。
3. 打开 Settings -> Environment Variables。
4. 分别填写 Key 和 Value。
5. Environment 至少选择 Production；如果要在预览部署里测试，也选择 Preview。
6. 保存后重新部署。环境变量修改不会影响已经生成的旧 deployment。

最小可用配置：

```text
ID_MATCH_STORAGE_BACKEND=vercel
BLOB_READ_WRITE_TOKEN=...
UPSTASH_REDIS_REST_URL=...
UPSTASH_REDIS_REST_TOKEN=...
```

或：

```text
ID_MATCH_STORAGE_BACKEND=vercel
BLOB_READ_WRITE_TOKEN=...
KV_REST_API_URL=...
KV_REST_API_TOKEN=...
```

## 3. 一键部署

README 中的按钮会打开 Vercel Project creation flow，并要求填写必要环境变量：

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/kid141252010/ttml-id-match&env=ID_MATCH_STORAGE_BACKEND,BLOB_READ_WRITE_TOKEN,KV_REST_API_URL,KV_REST_API_TOKEN,SPOTIFY_CLIENT_ID,SPOTIFY_CLIENT_SECRET&envDescription=Set%20ID_MATCH_STORAGE_BACKEND%3Dvercel%20and%20configure%20Vercel%20Blob%20plus%20Redis%2FKV%20REST%20credentials.)

如果你从 fork 部署，把按钮里的 `repository-url` 换成自己的仓库地址，或者直接在 Vercel Dashboard 里导入 Git 仓库。

## 4. 手动部署

本地确认构建：

```powershell
python -m pip install -r requirements.txt
npm --prefix web install
npm --prefix web run build
```

安装并登录 Vercel CLI 后部署：

```powershell
npm i -g vercel
vercel login
vercel link
vercel env pull .env.vercel
vercel deploy
```

生产部署：

```powershell
vercel deploy --prod
```

仓库的 `vercel.json` 已设置：

```json
{
  "installCommand": "python -m pip install -r requirements.txt && npm --prefix web install",
  "buildCommand": "npm --prefix web run build",
  "outputDirectory": "web/dist",
  "rewrites": [
    { "source": "/api/(.*)", "destination": "/api/index.py" },
    { "source": "/(.*)", "destination": "/index.html" }
  ]
}
```

## 5. 部署后验收

部署完成后先检查 API：

```powershell
Invoke-RestMethod https://your-project.vercel.app/api/health
```

预期返回：

```json
{"status":"ok"}
```

再在页面上传一个最小 TTML-only 文件。TTML 必须已有 `musicName`，否则后端会沿用 CLI 规则拒绝搜索。可用示例：

```xml
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:amll="http://www.example.com/ns/amll">
  <head>
    <metadata>
      <amll:meta key="musicName" value="Song"/>
      <amll:meta key="artists" value="Artist"/>
      <amll:meta key="album" value="Album"/>
    </metadata>
  </head>
  <body/>
</tt>
```

验收标准：

- `/api/health` 返回 `200`。
- 上传后能识别 TTML 组。
- 预览接口能返回候选或明确的上游 warning。
- 应用后能下载单个 TTML 或 zip。
- Vercel Blob 中能看到 `id-match/<session_id>/uploads/` 和 `outputs/` 对象。

## 6. 常见问题

### `session not found`

优先检查 `ID_MATCH_STORAGE_BACKEND` 是否为 `vercel`。如果误用 `local`，会话只存在当前函数实例的临时目录里，冷启动后会丢失。

### 上传后预览失败

TTML-only 模式必须已有 `musicName`。这是 CLI 语义，不会从文件名猜歌名。

### Spotify 搜索为空

确认 `SPOTIFY_CLIENT_ID` 和 `SPOTIFY_CLIENT_SECRET` 已配置到 Vercel 的 Production 环境。如果不需要 Spotify，可以忽略这个 warning。

### 上游访问超时或被限流

按来源配置代理，或降低批量并发。代理变量见 [配置说明](../configuration.md)。

### 构建失败

先确认 Vercel Build Logs 中 Python 和 Node 依赖安装阶段是否通过。本仓库依赖 `requirements.txt` 和 `web/package-lock.json`；如果锁文件或依赖版本变化，先在本地跑：

```powershell
python -B -m unittest discover -s tests
npm --prefix web run build
```
