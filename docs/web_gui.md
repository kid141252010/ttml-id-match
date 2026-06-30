# Web GUI 运行与部署指南

本文档介绍如何本地运行 Web 图形化界面（GUI）以及将其一键部署到 Vercel 平台的具体步骤。

## 项目架构

项目包含一个前后端分离的 Web 服务骨架：
- **后端**：基于 FastAPI，入口为 `server.main:app`。
- **前端**：基于 Vue 3 + Vite + Naive UI，代码位于 `web/` 目录。
- **联调代理**：前端开发阶段默认通过 Vite 代理将 `/api` 请求转发到后端的 `http://127.0.0.1:8000`。

---

## 本地开发与运行

### 1. 安装依赖

在仓库根目录下安装 Python 后端依赖，并进入 `web` 目录安装前端 Node.js 依赖：

```powershell
# 安装后端依赖
python -m pip install -r requirements.txt

# 安装前端依赖
cd web
npm install
```

### 2. 启动后端

使用 `uvicorn` 启动 FastAPI 后端服务（默认监听 8000 端口，开启热重载）：

```powershell
uvicorn server.main:app --host 127.0.0.1 --port 8000 --reload
```

### 3. 启动前端

在另一个终端中，进入 `web` 目录启动 Vite 开发服务器：

```powershell
cd web
npm run dev
```

启动后在浏览器打开 `http://127.0.0.1:5173` 即可使用。

### 4. 上传限制说明

- 上传 **TTML-only** 文件时，该 TTML 文件中需要已经包含 `musicName` 元数据，否则后端会沿用 CLI 规则拒绝搜索；
- 上传同名音频和 TTML 时，会走音频标签读取路径。

### 5. 纯前端 Mock 模式

如果只需要预览或调试前端 UI 界面，而不想运行 Python 后端，可以开启 Mock API 模式：

```powershell
cd web
# Windows PowerShell
$env:VITE_USE_MOCK_API="1"
npm run dev

# Windows CMD
set VITE_USE_MOCK_API=1
npm run dev

# Linux / macOS
VITE_USE_MOCK_API=1 npm run dev
```

---

## Vercel 部署

仓库根目录包含了用于 Vercel 平台的配置文件 `vercel.json` 以及构建无服务器函数（Serverless Function）的入口文件 `api/index.py`。

Vercel 会自动构建 `web/` 中的静态前端，并将所有的 `/api/*` 请求重写到 FastAPI Python Function。

### 一键部署按钮

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/kid141252010/ttml-id-match&env=ID_MATCH_STORAGE_BACKEND,BLOB_READ_WRITE_TOKEN,KV_REST_API_URL,KV_REST_API_TOKEN,APPLE_MUSIC_BEARER_TOKEN,SPOTIFY_CLIENT_ID,SPOTIFY_CLIENT_SECRET&envDescription=Set%20ID_MATCH_STORAGE_BACKEND%3Dvercel%20and%20configure%20Vercel%20Blob%20plus%20Redis%2FKV%20REST%20credentials.%20Apple%20Music%20and%20Spotify%20credentials%20are%20optional.)

### 环境变量配置

在 Vercel 部署时，**必须**将存储后端配置为持久化存储（Vercel KV 和 Vercel Blob），否则由于 Serverless 的无状态特性，会话和文件将会丢失。

请在 Vercel 控制面板中为项目添加以下环境变量：

```text
ID_MATCH_STORAGE_BACKEND=vercel
BLOB_READ_WRITE_TOKEN=your_vercel_blob_token
KV_REST_API_URL=your_vercel_kv_rest_url
KV_REST_API_TOKEN=your_vercel_kv_rest_token
```

> [!NOTE]
> 如果您没有 Spotify 凭据，Spotify 搜索会自动跳过，不影响 Apple Music、QQ 音乐和网易云音乐的使用。

有关 Vercel 部署更详尽的系统配置及集成步骤，请参阅已有的 [docs/deployment/vercel.md](deployment/vercel.md)。
