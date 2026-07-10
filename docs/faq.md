# v2 常见问题

## 为什么 preview job 是 `completed_with_errors`？

一个或多个来源返回了 warning，但 snapshot 仍成功发布。已成功的来源候选可以继续 review 和 apply；warning 会保存在对应 `SourceResult` 与 job errors 中。

## 为什么出现 `snapshot_conflict`（409）？

上传内容在 preview 后发生了变化、snapshot 已删除，或请求引用了不存在的 snapshot。v2 会在写入任何输出前拒绝整批 apply。重新上传或重新 preview 后再提交选择。

## 为什么出现 `job_busy`（409）？

另一个实例持有同一 preview job 的短期 lease。客户端应稍后重试 step；同一 pair 不会被两个实例重复推进。

## 为什么 TTML 没有进入 preview？

- 同 stem 存在多个音频且不能由“唯一 FLAC”规则消歧时，会产生 `ambiguous_audio` 并阻止 preview。
- TTML 本身无法解析时，仅该 pair 失败；其他 pair 继续。
- TTML-only 文件应已有 `musicName`，否则各来源通常只能返回缺少查询依据的 warning。

## 如何强制配对不同文件名的音频与 TTML？

CLI 可显式指定：

```powershell
python -m ttml_metadata --ttml lyrics.ttml --audio recording.flac
```

Web 的自动 PairingPlan 不猜测不同 stem 的对应关系。

## 为什么没有 Spotify 候选？

确认 `SPOTIFY_CLIENT_ID` 与 `SPOTIFY_CLIENT_SECRET` 都已配置。缺少凭据只会产生来源 warning，不影响其他来源。

## 网络超时或 429 如何处理？

共享 HTTP transport 会统一重试。可配置来源代理，并降低全局/来源并发：

```text
TTML_SEARCH_WORKERS=2
TTML_SOURCE_SPOTIFY_WORKERS=1
TTML_PROXY_SPOTIFY=http://proxy.example.com:8080
```

## 写入会覆盖原文件吗？

真实 apply 在写入前校验 preview SHA-256，先创建 `.bak`，再通过同目录临时文件原子替换。仅预览可使用：

```powershell
python -m ttml_metadata D:\lyrics --dry-run
```
