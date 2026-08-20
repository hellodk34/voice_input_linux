# 设计与细节

本文档说明 Qwen-Audio 语音输入的实现原理、完整配置项、兼容性与故障排查。快速上手请回到 [README](./README.md)。

## 工作原理

```
  ┌─────────┐  快捷键   ┌──────────────┐   派生   ┌──────────────────┐
  │ 主进程   │ ───────▶ │  start_recording │ ─────▶ │  --listen 子进程   │
  └─────────┘           └──────────────┘          └────────┬─────────┘
       ▲  再按一次(手动停止)                                  │ PortAudio 录音
       └───────────── touch stop 文件 ◀────────────────────┼──┘
                                                           ▼
                                             batch: 静音≥batch_silence_timeout / 手动 / 超时
                                             stream: 手动 / 服务端无结果 / 超时
                                                           │
                                                           ▼
                                       ┌────────────────────────────┐
                                       │  Base64 / WebSocket → ASR  │
                                       └────────────────────────────┘
                                                           │ output.text
                                                           ▼
                                       ┌────────────────────────────┐
                                       │  剪贴板 + 模拟 Ctrl+V 上屏   │
                                       │  (wl-copy/xclip  + ydotool/ │
                                       │   wtype/xdotool)           │
                                       └────────────────────────────┘
```

`--listen` 子进程由 `sys.executable` 派生，因此只要入口是 `.venv/bin/python`，整个链路都使用虚拟环境里的依赖，与系统 Python 无关。

## 完整配置

配置文件：`~/.config/qwen-voice-input/config.ini`（首次运行自动生成；若运行后提示"配置文件缺少选项"，请删除后重新运行以生成最新配置）。

```ini
; Qwen-Audio 语音输入配置
[auth]
; 千问AI平台(https://platform.qianwenai.com) 或 阿里云百炼控制台 获取
api_key =

[general]
; ==== 通用配置（batch 与 stream 两种模式均生效） ====
; 识别模式：batch / stream
;   batch  整段录音 → 整段识别 → 一次性上屏（默认，稳定）
;   stream 实时流式识别，边说边上屏（需 websocket-client，延迟更低）
work_mode = batch
; 输入方式：auto / clipboard（auto=自动上屏，clipboard=仅复制手动粘贴）
type_method = auto
; 日志开关：true / false（运行正常可关掉，省一点磁盘/IO）
log = true
; 音量阈值：低于该值视为静音（0~1）
;   麦克风过灵敏（键盘/风扇声被误当人声、说完了不结束）就调大，太不灵敏就调小
vad_threshold = 0.03
; 最长录音秒数（防止一直有声音导致不结束）
max_duration = 60

; ==== batch 模式专属 ====
; 静音多少秒后自动结束录音（说完这句会自动识别上屏）
batch_silence_timeout = 2.0

; ==== stream 模式专属 ====
; 流式识别服务地址：留空使用内置默认值 wss://dashscope.aliyuncs.com/api-ws/v1/inference（国内）
;   国际版（千问AI平台）请填：wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference
stream_url =
; 流式识别语言提示：留空自动检测；可填 zh / en / ja / ko 等
stream_language =
; 流式服务端 VAD 断句静音阈值(ms)：控制"一句话"的判定，默认 1300
stream_silence = 1300
; 流式断句方式：true=语义断句（按标点/语义，逗号不拆句，适合整句听写）；false=VAD 断句（按静音，延迟更低）
stream_semantic = true
; 流式模式：服务端连续多久没有识别结果就强制结束（兜底，客户端判定）
stream_stop_silence = 5.0
```

`[general]` 里的配置按作用域分三组：通用配置（`batch` 与 `stream` 均生效）、`batch` 模式专属、`stream` 模式专属；`[auth]` 的 `api_key` 对所有模式通用。

> `stream_*` 开头的配置项（`stream_url`、`stream_language`、`stream_silence`、`stream_semantic`、`stream_stop_silence`）一般**无需修改**，直接用模板默认值即可：它们是服务端 VAD 的断句/语言提示参数，与麦克风底噪无关。真正和麦克风灵敏度相关、可能需要调的只有 `vad_threshold` 与 `batch_silence_timeout`（可用 `./detect_mic.sh` 自动推荐）。

### 通用配置（`batch` 与 `stream` 均生效）

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `work_mode` | `batch` | `batch` 整段录音→整段识别→一次性上屏；`stream` 实时流式识别、边说边上屏 |
| `type_method` | `auto` | `auto` 自动上屏；`clipboard` 只复制文本、手动粘贴 |
| `log` | `true` | 关闭日志可省磁盘/IO，运行中修改需重启生效 |
| `vad_threshold` | `0.03` | 低于此视为静音。麦克风过灵敏（键盘/风扇声被误当人声）就调大，太不灵敏就调小 |
| `max_duration` | `60` | 防止一直有声音导致录音不结束 |

### batch 模式专属

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `batch_silence_timeout` | `2.0` | 静音多久后自动结束整段录音，说话间隙长就调大 |

### stream 模式专属

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `stream_url` | `wss://dashscope.aliyuncs.com/api-ws/v1/inference` | 流式识别 WebSocket 地址；国际版改为 `wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference` |
| `stream_language` | 空（自动检测） | 流式识别语言提示，如 `zh` / `en` / `ja` / `ko` |
| `stream_silence` | `1300` | 流式服务端 VAD 断句静音阈值(ms)，控制"一句话"的判定 |
| `stream_semantic` | `true` | `true` 语义断句（按标点/语义，逗号不拆句）；`false` VAD 断句（按静音，延迟更低） |
| `stream_stop_silence` | `5.0` | 服务端连续多久无识别结果就自动结束（`stream` 靠它而非本地静音，思考停顿不会打断） |

API Key 也可以改用环境变量（优先级高于配置文件）：

```bash
export QWEN_API_KEY=sk-xxxx
```

### 麦克风灵敏度推荐配置

`vad_threshold`（音量阈值）在 `batch` 与 `stream` 两种模式都生效，是过滤环境噪声、判断"是否在说话"的关键；`batch_silence_timeout` 只在 `batch` 模式下决定"静音多久自动结束"。按你的麦克风情况选一套：

| 场景 | `vad_threshold` | `batch_silence_timeout` | 说明 |
|---|---|---|---|
| 默认（折中） | `0.03` | `2.0` | 大多数麦克风的均衡值 |
| 麦克风过于灵敏（键盘/风扇声被误当人声、说完了不结束） | `0.05` ~ `0.08` | `1.5` ~ `2.0` | 抬高音量门槛过滤环境噪声，缩短静音等待 |
| 麦克风不够灵敏（小声说话识别不到、一句话没说完就被掐断） | `0.01` ~ `0.02` | `2.5` ~ `3.0` | 降低门槛不丢小声，延长静音等待避免误结束 |

- 首选还是**在系统层面调低麦克风输入增益 / 开启噪声抑制**（`pavucontrol`、PipeWire / PulseAudio 的降噪模块），上面的阈值只是软件兜底。
- 懒得自己对照这张表的话，直接跑 `./detect_mic.sh`，它会测底噪并按上表自动给出推荐值。
- `stream` 模式不靠本地静音自动结束（思考停顿不会打断），其"结束时机"由 `stream_stop_silence` 控制；若仍偶发键盘声触发悬浮预览层，可在说完后**再按一次快捷键**手动停止。

## 两种识别模式

| 模式 | 值 | 原理 | 特点 |
|---|---|---|---|
| 整段识别 | `batch` | 录音结束后整段音频 Base64 直传 `qwen-audio-3.0-asr-flash`，拿到完整文本后一次性上屏 | 稳定、兼容性最好 |
| 实时流式 | `stream` | 边录边把 PCM 音频经 WebSocket 推给 `qwen-audio-3.0-asr-flash-streaming`，实时接收中间/最终结果；中间结果在悬浮预览层实时刷新，最终结果按句粘贴上屏 | "边说边出字"，延迟更低 |

`stream` 模式说明：

- 依赖 `websocket-client`（`setup.sh` 已自动安装，也可 `.venv/bin/pip install websocket-client`）。
- 上屏复用批量模式同样可靠的「剪贴板 + Ctrl+V」通道，最终结果按句粘贴上屏（不经过输入法组合，避免 fcitx5/IBus 干扰）。
- 中间结果显示在一个**不抢焦点的 GTK 悬浮预览层**里实时刷新（`overlay.py`，复刻输入法候选区的"整句覆盖"效果，如逗号会被后续结果覆盖）；若系统缺少 GTK（`python3-gi` + `gir1.2-gtk-3.0`），自动退化为桌面通知。
- 断句由服务端 VAD 完成，`stream_silence` 控制"一句话"的静音判定；默认走 VAD 断句（低延迟），适合交互场景。
- 本地同样做音量门限：低于 `vad_threshold` 的音频块按静音处理（不送去识别），可过滤键盘/风扇噪声；但不会因短暂静音自动结束，结束靠再按一次快捷键或 `stream_stop_silence`。

### 两种模式如何结束录音

| 模式 | 结束方式 |
|---|---|
| `batch` | ① 说完静音 `batch_silence_timeout` 秒 → 自动结束并整段识别上屏；② 录音中再按一次快捷键手动结束；③ 到 `max_duration` 强制结束 |
| `stream` | ① 录音中再按一次快捷键手动结束；② 服务端连续 `stream_stop_silence` 秒无识别结果 → 自动结束；③ 到 `max_duration` 强制结束 |

关键区别：`batch` 靠**本地静音**判断"一句话说完"；`stream` 则**不因短暂静音（如思考停顿）结束**，**适合边说边想的场景**，结束主要靠手动或服务端无结果。

## 命令行

```bash
.venv/bin/python voice_input.py --help                          # 查看参数
.venv/bin/python voice_input.py --version                       # 查看版本
.venv/bin/python voice_input.py --file 某段录音.wav              # 直接识别已有音频
.venv/bin/python voice_input.py --api-key sk-xxxx --file a.wav  # 临时指定 Key 测试
```

## 麦克风底噪检测

```bash
./detect_mic.sh
```

首次使用或更换麦克风后运行。脚本会先提醒你在「系统设置 → 声音 → 输入」里选好并设为默认的麦克风，然后录 3 秒底噪，打印 `vad_threshold` / `batch_silence_timeout` 的推荐值（**只打印、不改配置**）。

运行流程：

1. 提示设置默认输入设备 → 按回车继续；
2. 未检测到可用麦克风 → 提示并退出；
3. 录 3 秒底噪（提示保持安静）→ 计算 95 分位底噪 → 按档位给出推荐值。

脚本按三级降级链运行，最小依赖只有 `alsa-utils`（`arecord`）+ `python3`：

| 步骤 | 首选 | 备选 | 兜底（零依赖） |
|---|---|---|---|
| 检测麦克风 | `arecord -l` | `pactl list sources short` | 读 `/proc/asound/pcm` |
| 录音测量 | `arecord` | `parec` → `sox` | — |
| 计算底噪 | `python3`（纯 stdlib） | `awk`（仅 RMS，粗略） | — |

相关系统包：`alsa-utils`（`arecord`）、`pulseaudio-utils`（`pactl`/`parec`）、`sox`；缺工具时脚本会打印对应的安装命令（如 `sudo apt install alsa-utils`）。

底噪用「95 分位」而非裸 RMS 衡量，可避免偶发尖峰（键盘声、插拔声）把底噪估高。推荐值映射到 [麦克风灵敏度推荐配置](#麦克风灵敏度推荐配置) 表的档位；若检测到输入接近满幅（疑似选错设备/输入悬空）或几乎完全静音，会提示你回系统设置重新选择麦克风。

## 手动安装

<details>
<summary>点击展开</summary>

```bash
# 1. 系统依赖（以 Debian/Ubuntu 为例）
sudo apt install libportaudio2 libnotify-bin wl-clipboard xclip xdotool
# Wayland 下按需：GNOME 需要 ydotool；KDE/Sway 建议 wtype
# stream 模式悬浮预览层（可选，缺了会自动退化为桌面通知）：
sudo apt install python3-gi gir1.2-gtk-3.0

# 2. Python 依赖（虚拟环境）
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install sounddevice numpy websocket-client

# 3. ydotool 额外步骤（GNOME Wayland 需要）
sudo usermod -aG input $USER   # 然后注销重登
sudo systemctl enable --now ydotool
```

</details>

## 兼容性

### 显示服务器与桌面环境

| 显示服务器 | 桌面环境 | 剪贴板 | 上屏方式 |
|---|---|---|---|
| Wayland | GNOME (Mutter) | `wl-copy` | `ydotool`（uinput，需 input 组权限） |
| Wayland | KDE Plasma / Sway / Hyprland 等 | `wl-copy` | `wtype`（免 root） |
| X11 | GNOME / KDE / XFCE / Cinnamon / i3 等 | `xclip` / `xsel` | `xdotool` |

脚本按以下顺序自动探测与降级：

- 显示服务器：`WAYLAND_DISPLAY` 有值 → Wayland；否则有 `DISPLAY` → X11
- 剪贴板：Wayland 用 `wl-copy`；X11 依次尝试 `xclip` → `xsel`
- 上屏：X11 用 `xdotool key ctrl+v`；Wayland 下非 GNOME 且装有 `wtype` 时优先 `wtype`，否则用 `ydotool`；都不满足则退化为"已复制，请按 Ctrl+V"

关于 input 组：加入 `input` 组只是让当前用户能打开 `/dev/uinput` 的常规做法。如果系统给 `/dev/uinput` 配置了 ACL（如 `setfacl -m u:用户名:rw /dev/uinput`），即使不在 `input` 组也能正常上屏。可用 `getfacl /dev/uinput` 查看。

### 发行版

| 发行版 | 包管理器 |
|---|---|
| Debian / Ubuntu / Mint / Pop!_OS 等 | `apt`，`setup.sh` 自动处理 |
| Fedora / RHEL / Rocky / AlmaLinux | `dnf` |
| Arch / Manjaro / EndeavourOS | `pacman` |
| openSUSE | `zypper` |
| Void | `xbps-install` |
| 其他 | 脚本给出需要手动安装的包列表 |

## 故障排查

| 症状 | 原因 | 解决 |
|---|---|---|
| 一按快捷键就提示缺 sounddevice/numpy | 没走虚拟环境 | 用 `.venv/bin/python` 启动，或重跑 `./setup.sh` |
| 说完了不自动结束、一直录到超时 | 麦克风底噪高于 `vad_threshold` | 调大 `vad_threshold`（如 0.03~0.05） |
| 一句话没说完就被截断（batch） | 阈值太高或静音判定太短 | 调小 `vad_threshold` 或调大 `batch_silence_timeout` |
| stream 下键盘/风扇声触发悬浮层、说完了不结束 | 麦克风过灵敏，环境噪声高于 `vad_threshold` | 调大 `vad_threshold`（0.05~0.08），或系统层面调低麦克风增益，或说完再按一次快捷键手动停止 |
| 识别成功但没上屏 | 缺少上屏工具 | X11 装 `xdotool`；Wayland 装 `ydotool`(GNOME) 或 `wtype`(KDE/Sway) |
| ydotool 没反应 | 用户不在 input 组且无 `/dev/uinput` ACL，或服务未运行 | `sudo usermod -aG input $USER` 后重登，或 `setfacl -m u:$USER:rw /dev/uinput`；`systemctl enable --now ydotool` |
| 没声音 | 默认输入设备不对 | 检查系统录音设置，或用 `pactl list sources short` 确认 |
| 提示未配置 API Key | 配置里是空值 | 编辑 `~/.config/qwen-voice-input/config.ini` 填入 `api_key` |
| 识别结果乱码 | 终端编码问题 | 脚本内部统一 UTF-8，检查你的系统 locale |
