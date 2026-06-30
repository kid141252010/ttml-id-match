# CLI 进阶使用指南

本文档介绍 CLI 脚本的进阶使用方法，包括单首歌曲处理以及 Windows 环境下的交互脚本。

## 单首文件处理

如果音频和 TTML 文件名不一致，或只想处理一首歌，可以显式指定文件：

```powershell
python fill_ttml_metadata.py `
  --audio "example\2. Disease (Apple Music Live).flac" `
  --ttml "example\2. Disease (Apple Music Live).ttml"
```

单首 dry-run：

```powershell
python fill_ttml_metadata.py `
  --audio "example\2. Disease (Apple Music Live).flac" `
  --ttml "example\2. Disease (Apple Music Live).ttml" `
  --dry-run
```

如果没有音频，也可以只指定 TTML。脚本会读取 TTML 已有的 `musicName`、`artists`、`album`，然后复用同一套 QQ 音乐、网易云音乐和 Spotify 搜索流程：

```powershell
python fill_ttml_metadata.py `
  --ttml "example\lyrics-only.ttml" `
  --dry-run
```

## Windows 交互脚本

仓库根目录提供 `fill_metadata.bat`，适合在 Windows 上双击或从 CMD/PowerShell 里运行。

双击运行时，脚本会提示你输入要处理的目录。也可以直接带目录参数运行：

```cmd
fill_metadata.bat "D:\lyrics"
```

PowerShell 里运行：

```powershell
.\fill_metadata.bat "D:\lyrics"
```

如果想直接运行 PowerShell 脚本：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\fill_metadata.ps1 -TargetDir "D:\lyrics"
```

交互脚本的工作流程与特点：
1. **预览与确认**：交互脚本会先直接显示 Python 的 dry-run 输出，不会立刻修改文件；只有在预览成功后输入 `Y` 才会进入真实写入。
2. **并发控制**：默认会把 `-SearchWorkers 3` 传给 Python 脚本；需要串行搜索时可传 `-SearchWorkers 1`。
3. **交互选择候选**：真实写入阶段会分别汇总 Apple Music、QQ 音乐、网易云音乐和 Spotify 最佳候选：输入 `Y` 接受全部最佳结果，输入 `N` 则逐首从 5 个候选里选择；Apple Music 和 Spotify 会按区域/市场分别选择。
4. **备份机制**：真实写入仍由 Python 脚本生成 `.bak` 备份。
5. **依赖说明**：脚本不会自动安装依赖，如果缺少 Python 依赖，请先执行 `python -m pip install -r requirements.txt` 安装。
