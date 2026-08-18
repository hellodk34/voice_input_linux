#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qwen-Audio 语音输入（跨发行版 / 跨桌面环境）

用法：
    voice_input.py                # 按一次开始录音；静默 silent_timeout 秒自动结束并识别
                                  # 录音中再按一次 = 手动停止并识别
    voice_input.py --file a.wav   # 直接识别已有音频文件
    voice_input.py --api-key sk-xxx  # 临时指定 API Key

兼容：
    - 显示服务器：Wayland 与 X11 自动探测
    - 桌面环境：GNOME / KDE / XFCE / Sway / i3 等
    - 剪贴板：wl-copy(Wayland) / xclip、xsel(X11)
    - 自动上屏：ydotool(Wayland 通用) / wtype(KDE、Sway 等) / xdotool(X11)

依赖：见 setup.sh；核心为 sounddevice + numpy（虚拟环境 .venv 中）。
"""

import argparse
import base64
import configparser
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

PROG = "qwen-voice-input"
RUN_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
PID_FILE = RUN_DIR / f"{PROG}.pid"
STOP_FILE = RUN_DIR / f"{PROG}.stop"
WAV_FILE = Path("/tmp") / f"{PROG}.wav"

API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
MODEL = "qwen-audio-3.0-asr-flash"
SAMPLE_RATE = 16000

CONFIG_FILE = Path.home() / ".config" / PROG / "config.ini"
LOG_FILE = CONFIG_FILE.parent / "log.txt"

CONFIG_TEMPLATE = """; Qwen-Audio 语音输入配置
[auth]
; 千问AI平台(https://platform.qianwenai.com) 或 阿里云百炼控制台 获取
api_key =

[general]
; 输入方式：auto / clipboard（auto=自动上屏，clipboard=仅复制手动粘贴）
type_method = auto
; 日志开关：true / false（运行正常可关掉，省一点磁盘/IO）
log = true
; 静音多少秒后自动结束录音（说完了会自动识别）
silence_timeout = 2.0
; 音量阈值：低于该值视为静音（0~1）；内置麦克风底噪高、说完了不自动结束时调大
vad_threshold = 0.03
; 最长录音秒数（防止一直有声音导致不结束）
max_duration = 60
"""


def ensure_config():
    if not CONFIG_FILE.exists():
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(CONFIG_TEMPLATE, encoding="utf-8")


_log_enabled = None


def log(msg):
    global _log_enabled
    if _log_enabled is None:
        _log_enabled = get_config("general", "log", "true").lower() in (
            "1", "true", "yes", "on",
        )
    if not _log_enabled:
        return
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def notify(text, expire_ms=4000):
    try:
        subprocess.run(
            ["notify-send", "-a", PROG, "-t", str(expire_ms), "语音输入", text],
            check=False,
        )
    except FileNotFoundError:
        print(text, file=sys.stderr)


def _read_config():
    cfg = configparser.ConfigParser()
    try:
        cfg.read_string(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except Exception:
        pass
    return cfg


def get_config(section, key, default=None):
    if not CONFIG_FILE.exists():
        return default
    cfg = _read_config()
    if cfg.has_option(section, key):
        return cfg.get(section, key).strip()
    return default


def get_api_key():
    key = os.environ.get("QWEN_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")
    if key:
        return key.strip()
    return get_config("auth", "api_key", "") or ""


def get_type_method():
    return get_config("general", "type_method", "auto")


def distro_id():
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("ID="):
                return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return ""


def _pid_alive(pid):
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _recording_pid():
    if not PID_FILE.exists():
        return 0
    try:
        return int(PID_FILE.read_text().strip())
    except ValueError:
        PID_FILE.unlink(missing_ok=True)
        return 0


def _write_wav(path, data):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(data)


def _cleanup_listener():
    STOP_FILE.unlink(missing_ok=True)
    if PID_FILE.exists():
        try:
            if int(PID_FILE.read_text().strip()) == os.getpid():
                PID_FILE.unlink(missing_ok=True)
        except (ValueError, OSError):
            PID_FILE.unlink(missing_ok=True)


def start_recording():
    script = Path(__file__).resolve()
    try:
        proc = subprocess.Popen(
            [sys.executable, str(script), "--listen"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        notify(f"无法启动录音进程：{e}")
        return 1
    PID_FILE.write_text(str(proc.pid))
    return 0


def run_listener():
    log("listener: 启动")
    STOP_FILE.unlink(missing_ok=True)
    WAV_FILE.unlink(missing_ok=True)
    PID_FILE.write_text(str(os.getpid()))

    try:
        import numpy as np
        import sounddevice as sd
    except ImportError:
        notify("缺少 sounddevice/numpy，请运行 ./setup.sh 或 .venv/bin/pip install sounddevice numpy")
        return 1

    silence_timeout = float(get_config("general", "silence_timeout", "2.0"))
    vad_threshold = float(get_config("general", "vad_threshold", "0.03"))
    max_duration = float(get_config("general", "max_duration", "60.0"))
    log(f"listener: 参数 静音阈值={silence_timeout}s 音量阈值={vad_threshold} 最长={max_duration}s")

    notify("请开始说话……", expire_ms=1500)

    frames = []
    silence_dur = 0.0
    stop_reason = None

    def callback(indata, frames_, time_, status):
        nonlocal silence_dur
        frames.append(indata.copy())
        rms = float(np.sqrt(np.mean(indata ** 2)))
        block_sec = frames_ / SAMPLE_RATE
        if rms < vad_threshold:
            silence_dur += block_sec
        else:
            silence_dur = 0.0

    start = time.monotonic()
    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32",
            blocksize=1024, callback=callback,
        ):
            while True:
                if STOP_FILE.exists():
                    stop_reason = "manual"
                    break
                if time.monotonic() - start >= max_duration:
                    stop_reason = "max"
                    break
                if (time.monotonic() - start) >= 1.0 and silence_dur >= silence_timeout:
                    stop_reason = "silence"
                    break
                time.sleep(0.05)
    except Exception as e:
        log(f"listener: 录音异常 {e!r}")
        notify(f"录音出错：{e}")
        return 1

    log(f"listener: 停止（原因={stop_reason}，时长={time.monotonic() - start:.1f}s，块数={len(frames)}）")
    if not frames:
        notify("没有录到声音")
        _cleanup_listener()
        return 1

    audio = np.concatenate(frames)
    audio = np.clip(audio, -1.0, 1.0)
    pcm = (audio * 32767.0).astype(np.int16)
    _write_wav(WAV_FILE, pcm.tobytes())
    log(f"listener: 已写 {WAV_FILE}，{WAV_FILE.stat().st_size} 字节")

    notify("录音结束，正在识别……")
    rc = transcribe_file(WAV_FILE)
    _cleanup_listener()
    return rc


def _display_server():
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def _is_gnome():
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    return "gnome" in desktop


def _copy_to_clipboard(text):
    data = text.encode("utf-8")
    if shutil.which("wl-copy"):
        r = subprocess.run(
            ["wl-copy", text],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if r.returncode == 0:
            return True
    if shutil.which("xclip"):
        r = subprocess.run(
            ["xclip", "-selection", "clipboard", "-in"],
            input=data,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if r.returncode == 0:
            return True
    if shutil.which("xsel"):
        r = subprocess.run(
            ["xsel", "--clipboard", "--input"],
            input=data,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if r.returncode == 0:
            return True
    return False


def _paste_via_tool():
    ds = _display_server()
    if ds == "x11":
        if shutil.which("xdotool"):
            r = subprocess.run(
                ["xdotool", "key", "--clearmodifiers", "ctrl+v"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return r.returncode == 0
        return False
    if not _is_gnome() and shutil.which("wtype"):
        r = subprocess.run(
            ["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if r.returncode == 0:
            return True
    if shutil.which("ydotool"):
        r = subprocess.run(
            ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return r.returncode == 0
    return False


def _input_text(text):
    manual = get_type_method() == "clipboard"
    if manual:
        if _copy_to_clipboard(text):
            notify("文字已复制到剪贴板，请按 Ctrl+V 粘贴", expire_ms=6000)
            return 0
        notify("缺少剪贴板工具（wl-copy / xclip / xsel）", expire_ms=6000)
        return 1

    if _copy_to_clipboard(text):
        time.sleep(0.3)
        if _paste_via_tool():
            return 0
        notify("文字已复制到剪贴板，请按 Ctrl+V 粘贴", expire_ms=6000)
        return 0

    notify("缺少剪贴板/粘贴工具，请运行 ./setup.sh 补齐（wl-clipboard/xclip + xdotool/ydotool/wtype）", expire_ms=6000)
    return 1


def transcribe_file(path):
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        notify("没有可用的录音文件")
        return 1
    api_key = get_api_key()
    if not api_key:
        notify(f"未配置 API Key，请编辑 {CONFIG_FILE}")
        return 1

    fmt = "wav" if p.suffix.lower() == ".wav" else p.suffix.lower().lstrip(".")
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    data_uri = f"data:audio/{fmt};base64,{b64}"
    log(f"transcribe: 识别 {p}（{p.stat().st_size} 字节）")

    payload = {
        "model": MODEL,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": data_uri},
                        }
                    ],
                }
            ]
        },
        "parameters": {"format": fmt, "sample_rate": str(SAMPLE_RATE)},
    }

    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        log("transcribe: 发送请求")
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        log("transcribe: 收到响应")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        msg = f"API 错误 HTTP {e.code}：{body[:200]}"
        log("transcribe: " + msg)
        notify(msg)
        return 1
    except urllib.error.URLError as e:
        msg = f"网络错误：{e.reason}"
        log("transcribe: " + msg)
        notify(msg)
        return 1
    except Exception as e:
        msg = f"请求/解析失败：{e!r}"
        log("transcribe: " + msg)
        notify(msg)
        return 1

    code = result.get("code")
    if code and code != 200:
        msg = f"API 返回错误：{result.get('message', code)}"
        log("transcribe: " + msg)
        notify(msg)
        return 1

    text = (result.get("output") or {}).get("text", "").strip()
    if not text:
        notify("未识别到内容")
        return 1

    log("transcribe: 成功 -> " + text)
    if get_type_method() == "clipboard":
        notify(f"识别成功：{text}", expire_ms=6000)
    return _input_text(text)


def main():
    parser = argparse.ArgumentParser(description="Qwen-Audio 语音输入")
    parser.add_argument("--file", help="识别已有音频文件，跳过录音")
    parser.add_argument("--api-key", help="临时指定 API Key")
    parser.add_argument("--listen", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    ensure_config()
    if args.api_key:
        os.environ["QWEN_API_KEY"] = args.api_key

    if args.file:
        return transcribe_file(args.file)
    if args.listen:
        return run_listener()

    pid = _recording_pid()
    if pid and _pid_alive(pid):
        STOP_FILE.touch()
        return 0

    return start_recording()


if __name__ == "__main__":
    sys.exit(main())