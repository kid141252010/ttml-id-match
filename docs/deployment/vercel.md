# Vercel 部署教程

本文档介绍如何将 TTML ID Match v2 部署到 Vercel 平台。

---

## 1. 部署架构

项目采用前后端一体化部署方案：
- **前端**：静态站点，构建后输出至 `web/dist`。
- **后端**：基于 Vercel Python Runtime 的 Serverless 函数，入口为 `api/index.py`，负责将 `/api/*` 请求转发至 FastAPI 服务。
- **存储**：由于 Serverless 函数是无状态且存在冷启动，因此用户上传的临时文件、快照和最终生成的 TTML 结果必须持久化到 **Vercel Blob**，会话状态索引则保存在 **Vercel KV (Redis)** 中。

---

## 2. 准备 Vercel 资源

在开始部署前，请确保您已在 Vercel 控制台中准备好以下资源：
1. **Vercel 项目**：在 Vercel 中新建或关联一个 Git 项目。
2. **Vercel Blob Store**：用于存放用户上传的文件及处理结果。
3. **Vercel KV (Redis)**：用于存储会话映射关系。支持以下两组环境变量配置之一（二选一）：
   - `KV_REST_API_URL` 与 `KV_REST_API_TOKEN`
   - `UPSTASH_REDIS_REST_URL` 与 `UPSTASH_REDIS_REST_TOKEN`
4. **音乐平台凭据（可选）**：用于 Apple Music 和 Spotify 的高级检索接口认证。

---

## 3. 环境变量配置

请在 Vercel 项目设置中的 **Environment Variables** 页面配置以下变量。

### 核心存储配置 (必填)

| 变量名 | 说明 | 获取方式 |
| :--- | :--- | :--- |
| `ID_MATCH_STORAGE_BACKEND` | 存储后端类型，必须显式设置为 `vercel`。 | 手动填入 `vercel` |
| `BLOB_READ_WRITE_TOKEN` | 读写 Vercel Blob 的鉴权 Token。 | 创建 Blob 后 Vercel 会自动注入，或从 Blob 连接详情中手动复制。 |
| `KV_REST_API_URL` <br> `KV_REST_API_TOKEN` | 用于存储会话索引的 Redis REST 接口与 Token。 | 创建 KV (Redis) 后由 Vercel 自动注入，或从资源连接面板中复制。 |

> [!NOTE]
> 如果您使用的是 Upstash 自建 Redis，可以用 `UPSTASH_REDIS_REST_URL` 和 `UPSTASH_REDIS_REST_TOKEN` 代替 `KV_REST_API_*` 变量。

### 音乐平台与代理配置 (可选)

| 变量名 | 说明 | 备注 |
| :--- | :--- | :--- |
| `APPLE_MUSIC_BEARER_TOKEN` | Apple Music Catalog API 的 Bearer Token。 | 未配置时，程序会尝试自动提取网页端临时 Token。 |
| `SPOTIFY_CLIENT_ID` <br> `SPOTIFY_CLIENT_SECRET` | Spotify API 客户端凭证。 | 未配置时，会自动跳过 Spotify 的检索。 |
| `TTML_PROXY_ALL` | 全局外网代理地址。 | 格式如 `http://username:password@proxy.example.com:8080`。 |
| `TTML_PROXY_SPOTIFY` <br> `TTML_PROXY_APPLE_MUSIC` | 针对特定平台的专属代理地址。 | 优先级高于全局代理。请勿填写 `127.0.0.1` 本地代理。 |

---

## 4. 部署步骤

### 方式 A: 一键部署 (推荐)

点击下方按钮，即可自动克隆本仓库并在 Vercel 中引导创建项目，创建时需按照提示输入上述核心环境变量。

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/kid141252010/ttml-id-match&env=ID_MATCH_STORAGE_BACKEND,BLOB_READ_WRITE_TOKEN,KV_REST_API_URL,KV_REST_API_TOKEN,APPLE_MUSIC_BEARER_TOKEN,SPOTIFY_CLIENT_ID,SPOTIFY_CLIENT_SECRET&envDescription=Set%20ID_MATCH_STORAGE_BACKEND%3Dvercel%20and%20configure%20Vercel%20Blob%20plus%20Redis%2FKV%20REST%20credentials.%20Apple%20Music%20and%20Spotify%20credentials%20are%20optional.)

---

### 方式 B: 本地命令行部署

如果您需要手动编译或自定义修改后进行部署，可使用 Vercel CLI。

1. **本地环境验证**：
   ```powershell
   python -m pip install -r requirements.txt
   npm --prefix web ci
   npm --prefix web run build
   ```
2. **安装并登录 Vercel CLI**：
   ```powershell
   npm install -g vercel
   vercel login
   ```
3. **关联并部署项目**：
   ```powershell
   # 关联 Vercel 项目
   vercel link
   # 拉取 Vercel 上的环境变量到本地 .env.vercel 文件中
   vercel env pull .env.vercel
   # 部署开发版本预览
   vercel deploy
   # 部署并发布到生产环境
   vercel deploy --prod
   ```

项目已通过 `vercel.json` 预配置好了构建与重写命令，Vercel 会自动读取并执行前端静态资源的打包和 API 的重定向。

---

## 5. 部署后验证

部署完成后，建议进行基础冒烟测试以确认各项资源配置正确：

1. **创建一次性测试会话**：
   在终端中调用正式 v2 会话接口：
   ```bash
   curl -X POST https://<your-project-domain>.vercel.app/api/v2/sessions
   ```
   若返回以下结构，说明后端 Serverless 函数和会话存储运行正常：
   ```json
   {"session_id":"<32-character-id>"}
   ```

2. **验证文件上传与匹配**：
   打开您的 Vercel 站点域名，在界面中上传一个只包含基本元数据的简单 `.ttml` 文件（例如必须声明 `musicName`）。
   - 验证页面是否能成功列出待匹配的单曲。
   - 检查控制台网络请求，确认预览接口能成功获取并渲染各平台的 Candidate 匹配列表。
   - 尝试修改某项匹配推荐，点击 **Apply (应用)** 并完成生成文件的下载。
   - 进入 Vercel 控制台的 **Blob 存储区**，确认已成功创建 `sessions/<session_id>/`、`snapshots/` 及 `outputs/` 下的文件对象，且 **KV** 数据库中写入了对应的会话键值对。

---

## 6. 常见部署问题

- **报错 `session not found`**：
  请务必检查环境变量中 `ID_MATCH_STORAGE_BACKEND` 是否已正确填入为 `vercel`。若忘记配置或误设为 `local`，会话数据仅能保存在临时且极易随冷启动而销毁的 Serverless 节点本地目录中。
- **构建过程中报错**：
  请检查 Vercel Build Logs 中的依赖安装日志。本仓库完全依赖 `requirements.txt` 和 `web/package-lock.json`。如遇构建超时或版本冲突，请先在本地终端运行单元测试与构建测试以排查原因。
