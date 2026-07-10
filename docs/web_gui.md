# Web GUI 运行指南

本文档介绍如何本地运行和开发 Web 图形化界面（GUI），并说明其核心工作流和上传规则。

---

## 1. 项目架构

Web 服务采用前后端分离的架构：
- **后端**：基于 FastAPI，程序入口为 `server.main:app`。
- **前端**：基于 Vue 3 + Vite + Naive UI，代码位于 `web/` 目录。
- **代理联调**：Vite 开发服务器会自动将以 `/api/v2` 开头的请求转发至后端的 `http://127.0.0.1:8000`。
- **接口契约**：使用 OpenAPI 进行接口定义。通过 `openapi/v2.json` 生成前端的接口文件 `web/src/api/generated.ts`，再通过网关适配器（Gateway Adapter）转换为前端领域模型。

---

## 2. 本地开发与运行

### 安装依赖

首先，在仓库根目录下安装 Python 后端依赖；然后进入 `web` 目录安装 Node.js 前端依赖：

```powershell
# 安装 Python 后端依赖
python -m pip install -r requirements.txt

# 安装前端 Node 依赖
cd web
npm install
```

### 启动服务

本地运行需要同时启动后端与前端开发服务器：

1. **启动后端服务**：
   ```powershell
   uvicorn server.main:app --host 127.0.0.1 --port 8000 --reload
   ```

2. **启动前端服务**（在另一个终端中运行）：
   ```powershell
   cd web
   npm run dev
   ```

启动后，在浏览器中打开 `http://127.0.0.1:5173` 即可开始使用。

---

## 3. 上传与文件配对规则

- **纯 TTML 模式 (TTML-only)**：若只上传 TTML 文件，则该 TTML 文件必须已经包含 `musicName` 元数据，否则后端会拒绝搜索。
- **音频+TTML 模式**：若同时上传同名音频与 TTML 文件，后端将优先从音频文件中读取 ID3/FLAC 等元数据标签作为检索基准。

---

## 4. 工作流约束

- **修改预览**：修改候选选择会触发防抖（Debounce）请求，获取当前选择的变更方案（ChangePlan）；界面同时展示元数据/语言摘要和最终将写入的完整 TTML，并自动忽略较旧的过时响应。
- **提交限制**：只有在最新变更方案（ChangePlan）成功计算出来后，“应用 (Apply)”按钮才会启用。
- **会话销毁**：点击“新会话”时，程序会首先删除远端存储（如 Vercel Blob）中的 Session 数据，然后清空前端本地状态。

---

## 5. 生产部署

生产环境下建议部署至 Vercel 平台，配合使用 Vercel Blob 和 Vercel KV 存储。

有关 Vercel 的一键部署、手动部署以及环境变量的详细配置步骤，请参阅专用文档：
- **[Vercel 部署教程](deployment/vercel.md)**
