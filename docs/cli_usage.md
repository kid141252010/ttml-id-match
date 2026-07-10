# CLI 命令行工具使用指南

`python -m ttml_metadata` 是本项目的命令行工具入口，底层与 Web 流程共享相同的平台注册表、依赖调度器、匹配逻辑和写入规划。

---

## 常用命令示例

### 1. 批量处理目录

- **仅预览匹配结果（不修改文件）**：
  ```powershell
  python -m ttml_metadata D:\lyrics --dry-run
  ```
- **执行实际匹配并写入文件**：
  ```powershell
  python -m ttml_metadata D:\lyrics
  ```

### 2. 处理单首歌曲（指定 TTML 与音频）

- **仅预览单首歌曲匹配结果**：
  ```powershell
  python -m ttml_metadata --ttml lyrics.ttml --dry-run
  ```
- **实际匹配并写入单首歌曲**：
  ```powershell
  python -m ttml_metadata --ttml lyrics.ttml --audio recording.flac
  ```

### 3. 生成机器可读预览

输出 JSON 格式的检索候选结果，方便被其他脚本解析：
```powershell
python -m ttml_metadata D:\lyrics --dry-run --json
```

---

## 进阶用法：指定候选 ID 写入

如果需要干预匹配结果，传入指定的音乐平台 ID，可以生成一个 JSON 格式的选择文件（结构与 Web API v2 相同）：

`selections.json` 示例：
```json
{
  "selections": [
    {
      "pair_id": "pair-xxx",
      "sources": {
        "qq_music": ["123456"],
        "apple_music": ["6768201779"]
      }
    }
  ]
}
```

通过 `--selection-file` 参数传入该文件来应用指定的候选 ID：
```powershell
python -m ttml_metadata D:\lyrics --selection-file selections.json
```

---

## 工作机制说明

1. **同名音频匹配**：命令行工具会自动扫描目录下同名的 `.ttml` 与音频文件（如 `.flac`）进行配对。如果存在多个同名但不同格式的音频，优先选择唯一的 `.flac`。如果同名音频配对存在歧义（例如同名有多个不同非 FLAC 的音频），程序将拒绝处理并给出警告。
2. **容错机制**：某一个文件的解析或检索失败只会作为警告输出，不影响目录中其他文件的处理。
3. **安全写入**：在应用修改写入时，程序会首先计算文件哈希校验匹配，随后采用原子写入并自动在同目录下备份一份 `.bak` 原文件，确保数据安全。
