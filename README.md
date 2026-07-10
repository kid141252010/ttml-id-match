# TTML ID Match v2

TTML ID Match 是一个用于快速填充 TTML 歌词元数据（Metadata）的工具。它能够读取音频标签或已有的 TTML 元数据，自动检索并匹配音乐平台的歌曲 ID，支持生成并应用确定的写入方案。

项目提供命令行（CLI）工具和 Web API 服务，两者共享底层的匹配引擎（`MatchingEngine`）、配对方案（`PairingPlan`）和 TTML 写入规划器（`TtmlPlanner`）。

目前已内置支持以下音乐平台：
- Apple Music
- QQ 音乐
- 网易云音乐
- Spotify

---

## 核心特性

- **确定性写入**：预览、选择变更方案（ChangePlan）与最终应用写入，使用完全一致的文件内容与 SHA-256 哈希校验。
- **无状态设计**：应用写入阶段仅读取不可变的快照，不调用任何音乐平台 API，确保执行速度与稳定性。
- **容错与鲁棒性**：单个音频或平台接口失败仅作为警告输出，不影响其他文件和平台的处理。
- **智能配对**：支持 Unicode NFKC 规范化、去前后空格、大小写折叠；支持同名 Stem 下的唯一 FLAC 优先匹配规则。
- **弹性存储**：会话任务支持版本化状态和租约机制，可通过 Redis (KV) + Vercel Blob 在 Serverless 架构（如 Vercel）中稳定运行。
- **全新 API 契约**：仅开放 `/api/v2` 接口，支持松耦合的源映射架构，方便后续扩展更多平台而无需修改核心数据表结构。

---

## 安装与配置

### 1. 安装依赖

```powershell
python -m pip install -r requirements.txt
npm --prefix web ci
```

### 2. 配置平台凭据（可选）

在环境变量或 `.env` 文件中配置以下内容以启用对应平台的高级搜索：

```text
APPLE_MUSIC_BEARER_TOKEN=
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
```

---

## 命令行使用 (CLI)

`python -m ttml_metadata` 是标准的命令行入口。

- **仅预览（不修改文件）**：
  ```powershell
  python -m ttml_metadata example --dry-run
  ```

- **应用匹配并写入文件**（自动生成 `.bak` 备份并执行原子写入）：
  ```powershell
  python -m ttml_metadata example
  ```

- **生成机器可读的预览（JSON 格式）**：
  ```powershell
  python -m ttml_metadata example --dry-run --json
  ```

- **使用指定的选择文件应用修改**：
  ```powershell
  python -m ttml_metadata example --selection-file selections.json
  ```

> [!NOTE]
> `fill_ttml_metadata.py` 仍可作为可执行脚本直接运行，但推荐使用 `python -m ttml_metadata`。

---

## 网页端开发 (Web Development)

1. **启动后端服务**：
   ```powershell
   uvicorn server.main:app --host 127.0.0.1 --port 8000 --reload
   ```

2. **启动前端服务**：
   ```powershell
   npm --prefix web run dev
   ```

启动后可在浏览器中访问 `http://127.0.0.1:5173`。前端通过 `/api/v2` 接口与后端通信，数据传输对象（DTO）由 `openapi/v2.json` 自动生成。

---

## 验证与测试

```powershell
# 运行后端单元测试
python -m unittest discover -s tests

# 检查 OpenAPI 规范的一致性
npm --prefix web run openapi:check

# 运行前端测试
npm --prefix web test

# 构建前端静态文件
npm --prefix web run build

# 运行端到端 (E2E) 测试
npm --prefix web run test:e2e
```

端到端测试会模拟完整的用户流程：上传测试文件 -> 预览匹配候选 -> 修改选择并验证实时变更方案 -> 应用写入 -> 下载并校验生成的 TTML 文件。

---

## 相关文档

- [配置说明](docs/configuration.md)
- [CLI 使用指南](docs/cli_usage.md)
- [Web GUI 指南](docs/web_gui.md)
- [Vercel 部署教程](docs/deployment/vercel.md)
- [架构决策记录 (ADR)](docs/adr/0001-v2-deterministic-snapshot-workflow.md)
- [领域词汇表](docs/domain-glossary.md)
- [匹配与写入规则](docs/matching_rules.md)

---

## 开源协议

本项目采用 [AGPLv3](LICENSE) 协议开源。
