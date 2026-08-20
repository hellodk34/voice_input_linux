# Changelog

本项目的所有重要变更都记录在这里。

## [v1.0.1] - 2026-08-19

新增 `work_mode=stream` 实时流式识别（边说边上屏）。

主要变化：

- 新增 `stream` 模式：边录边把音频经 WebSocket 推给 `qwen-audio-3.0-asr-flash-streaming`，实时刷新中间结果并按句上屏。
- 新增 GTK 悬浮预览层 `overlay.py`，用于展示流式中间结果。
- 配置项调整：`silence_timeout` 更名为 `batch_silence_timeout`（仅 `batch` 模式生效），并新增 `stream_*` 系列配置项。

升级建议（本次配置有键名变更，推荐重新生成配置文件）：

1. `git pull` 拉取最新代码；
2. 删除旧配置文件 `~/.config/qwen-voice-input/config.ini`；
3. 重新运行一次程序，自动生成最新配置文件；
4. 重新填入你的 API Key 后即可使用。

## [v1.0.0] - 2026-08-18

首个版本，实现 `batch` 模式：按快捷键录音 → 整段识别 → 一次性上屏。
