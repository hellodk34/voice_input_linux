# Qwen-Audio 语音输入

> 按快捷键，说话，说完自动识别并上屏 —— 基于 Qwen-Audio 3.0 流式语音识别（ASR）的 Linux 语音输入工具。

我绑定的快捷键是 Alt+Z

![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.8+-blue)
![Platform](https://img.shields.io/badge/platform-Linux-9cf)
![Model](https://img.shields.io/badge/model-qwen--audio--3.0--asr--flash-green)

纯本地脚本，不上传录音文件（音频以 Base64 直传 API），识别文本后自动粘贴到当前光标处。支持 Wayland 与 X11、主流发行版与桌面环境。

## 功能特性

- 🎙️ 一键录音：按快捷键开始，说完静音自动结束、识别上屏；录音中再按一次快捷键手动结束
- 🔀 两种识别模式：`batch`（整段识别、一次性上屏）、`stream`（实时流式、边说边上屏）
- 🖥️ 自动探测 Wayland/X11，自动选择可用的剪贴板与上屏工具
- 📦 一键安装：`setup.sh` 自动识别发行版并装好全部依赖
- 🔒 隐私友好：仅把当前一段语音发给 API，不保存任何本地历史

## 演示

### batch 模式动图演示

![demo](./demo.webp)

### stream 模式视频演示（带音频）

https://github.com/user-attachments/assets/6b3b4b36-176a-4b33-94be-f2708e1386c3

## 快速开始

```bash
git clone https://github.com/hellodk34/voice_input_linux
cd voice_input_linux
./detect.sh     # 只读探测环境，给出安装建议
./setup.sh      # 一键安装系统依赖 + Python 依赖
```

然后：

1. 申请 API Key 并填入 `~/.config/qwen-voice-input/config.ini` 的 `api_key`（该文件首次运行自动生成）：
   - 千问AI平台：platform.qianwenai.com
   - 阿里云百炼平台：bailian.console.aliyun.com
2. 绑定全局快捷键，命令统一填：

```
<你的路径>/voice_input_linux/.venv/bin/python <你的路径>/voice_input_linux/voice_input.py
```

| 桌面环境 | 设置位置 |
|---|---|
| GNOME | 设置 → 键盘 → 查看及自定义快捷键 → 自定义快捷键 |
| KDE Plasma | 系统设置 → 快捷键 → 自定义快捷键 → 新建 → 命令 |
| XFCE | 设置 → 键盘 → 应用程序快捷键 |
| Cinnamon | 系统设置 → 键盘 → 快捷键 |
| Sway / i3 | `~/.config/sway/config` 或 `~/.config/i3/config`：`bindsym $mod+v exec ...` |

3. 使用：把光标放到要输入的地方，按一次快捷键开始说话；说完静音 2 秒自动识别上屏，想提前结束就再按一次快捷键。

## 麦克风底噪检测（推荐）

首次使用或更换麦克风后，建议先跑一次底噪检测，得到适合你麦克风的 `vad_threshold` / `batch_silence_timeout` 推荐值：

```bash
./detect_mic.sh
```

脚本会引导你在系统设置里选好默认麦克风，然后测 3 秒底噪并打印推荐配置（只打印、不改配置），你按提示改 `config.ini` 即可。详细说明见 [DESIGN.md](./DESIGN.md)。

## 两种识别模式

| 模式 | 值 | 说明 |
|---|---|---|
| 整段识别 | `batch`（默认） | 录完一整段 → 整段识别 → 一次性上屏，稳定 |
| 实时流式 | `stream` | 边说边出字，延迟更低 |

改 `config.ini` 里的 `work_mode` 即可切换。其余配置项一般保持默认即可——尤其是 `stream_*` 开头的一系列配置（服务端断句、语言提示等），模板默认值已按常见场景调好，通常无需修改。

## 更多

- 完整配置项、工作原理、兼容性、故障排查：[DESIGN.md](./DESIGN.md)
- 更新日志：[CHANGELOG.md](./CHANGELOG.md)

## 项目结构

```
.
├── voice_input.py      # 主程序（录音、VAD、ASR、上屏）
├── overlay.py          # stream 模式中间结果的 GTK 浮动预览层
├── setup.sh            # 多发行版一键安装脚本
├── detect.sh           # 环境探测脚本
├── detect_mic.sh       # 麦克风底噪检测，推荐 vad_threshold 等配置
├── README.md
├── DESIGN.md           # 设计与细节
├── CHANGELOG.md        # 更新日志
└── LICENSE
```

## License

[MIT](./LICENSE)
