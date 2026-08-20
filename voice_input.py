#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qwen-Audio 语音输入（跨发行版 / 跨桌面环境）

用法：
    .venv/bin/python voice_input.py                # 按一次开始录音；录音中再按一次 = 手动停止并识别
    .venv/bin/python voice_input.py --file a.wav   # 直接识别已有音频文件
    .venv/bin/python voice_input.py --api-key sk-xxx  # 临时指定 API Key

识别模式（config.ini 的 work_mode）：
    - batch  整段录音 → 整段识别 → 一次性上屏（默认，稳定）
    - stream 实时流式识别，边说边上屏（需 websocket-client）

兼容：
    - 显示服务器：Wayland 与 X11 自动探测
    - 桌面环境：GNOME / KDE / XFCE / Sway / i3 等
    - 剪贴板：wl-copy(Wayland) / xclip、xsel(X11)
    - 自动上屏：xdotool(X11) / wtype(KDE、Sway 等) / ydotool(Wayland 通用)

依赖：见 setup.sh；核心为 sounddevice + numpy（stream 另需 websocket-client）。
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
import uuid
import wave
from pathlib import Path

PROG = "qwen-voice-input"
__version__ = "1.0.1"
REPO_URL = "https://github.com/hellodk34/voice_input_linux"
RUN_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
PID_FILE = RUN_DIR / f"{PROG}.pid"
STOP_FILE = RUN_DIR / f"{PROG}.stop"
WAV_FILE = Path("/tmp") / f"{PROG}.wav"

API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
MODEL = "qwen-audio-3.0-asr-flash"
STREAM_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
STREAM_MODEL = "qwen-audio-3.0-asr-flash-streaming"
SAMPLE_RATE = 16000
OVERLAY_SCRIPT = Path(__file__).resolve().parent / "overlay.py"

CONFIG_FILE = Path.home() / ".config" / PROG / "config.ini"
LOG_FILE = CONFIG_FILE.parent / "log.txt"

CONFIG_TEMPLATE = """; Qwen-Audio 语音输入配置
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
"""

def ensure_config():
    if not CONFIG_FILE.exists():
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        return []
    return _warn_missing_keys()


def _missing_config_keys():
    """返回本地配置里缺少的键（与内置模板比对，用于升级提醒）。"""
    try:
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read_string(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        tmpl = configparser.ConfigParser(interpolation=None)
        tmpl.read_string(CONFIG_TEMPLATE)
    except Exception:
        return []

    expected = {(s, k) for s in tmpl.sections() for k in tmpl.options(s)}
    present = {(s, k) for s in cfg.sections() for k in cfg.options(s)}
    return sorted(f"{s}.{k}" for s, k in (expected - present))


def _warn_missing_keys():
    missing = _missing_config_keys()
    if not missing:
        return []
    notify(
        f"配置文件缺少选项：{', '.join(missing)}\n"
        f"建议删除 {CONFIG_FILE} 后重新运行以生成最新配置，再重新填入 API Key。\n"
        f"详见 {REPO_URL}",
        expire_ms=12000,
    )
    return missing


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


def get_work_mode():
    m = (get_config("general", "work_mode", "batch") or "batch").strip().lower()
    return m if m in ("batch", "stream") else "batch"


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
    log("listener: 启动（v%s）" % __version__)
    STOP_FILE.unlink(missing_ok=True)
    WAV_FILE.unlink(missing_ok=True)
    PID_FILE.write_text(str(os.getpid()))

    try:
        import numpy as np
        import sounddevice as sd
    except ImportError:
        notify("缺少 sounddevice/numpy，请运行 ./setup.sh 或 .venv/bin/pip install sounddevice numpy")
        return 1

    if get_work_mode() == "stream":
        return _run_stream_listener(np, sd)

    batch_silence_timeout = float(get_config("general", "batch_silence_timeout", "2.0"))
    vad_threshold = float(get_config("general", "vad_threshold", "0.03"))
    max_duration = float(get_config("general", "max_duration", "60.0"))
    log(f"listener: 参数 静音阈值={batch_silence_timeout}s 音量阈值={vad_threshold} 最长={max_duration}s")

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
                if (time.monotonic() - start) >= 1.0 and silence_dur >= batch_silence_timeout:
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


def _run_stream_listener(np, sd):
    """实时流式识别：中间结果走桌面通知，最终结果按句粘贴上屏。"""
    log("listener(stream): 启动（v%s）" % __version__)
    try:
        import websocket
    except ImportError:
        notify("缺少 websocket-client，请运行 .venv/bin/pip install websocket-client")
        return 1

    api_key = get_api_key()
    if not api_key:
        notify(f"未配置 API Key，请编辑 {CONFIG_FILE}")
        return 1

    ws_url = (get_config("general", "stream_url", "") or "").strip() or STREAM_WS_URL
    language = (get_config("general", "stream_language", "") or "").strip()
    try:
        silence = int(float(get_config("general", "stream_silence", "1300") or "1300"))
    except ValueError:
        silence = 1300
    vad_threshold = float(get_config("general", "vad_threshold", "0.03"))
    max_duration = float(get_config("general", "max_duration", "60.0"))
    stream_stop_silence = float(get_config("general", "stream_stop_silence", "5.0"))
    stream_semantic = (get_config("general", "stream_semantic", "true") or "true").strip().lower() in (
        "1", "true", "yes", "on",
    )
    log(f"listener(stream): url={ws_url} 语言={language or 'auto'} 断句静音={silence}ms 语义断句={stream_semantic} 音量阈值={vad_threshold} 无结果停止={stream_stop_silence}s")

    notify("请开始说话……（实时识别，边说边上屏）", expire_ms=1500)

    task_id = uuid.uuid4().hex[:32]
    parameters = {
        "format": "pcm",
        "sample_rate": SAMPLE_RATE,
        "max_sentence_silence": silence,
        "semantic_punctuation_enabled": stream_semantic,
    }
    if language:
        parameters["language_hints"] = [language]
    run_task = {
        "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
        "payload": {
            "task_group": "audio",
            "task": "asr",
            "function": "recognition",
            "model": STREAM_MODEL,
            "parameters": parameters,
            "input": {},
        },
    }

    try:
        ws = websocket.create_connection(
            ws_url, header={"Authorization": f"Bearer {api_key}"}, timeout=10
        )
    except Exception as e:
        notify(f"连接流式识别服务失败：{e}")
        log(f"listener(stream): 连接失败 {e!r}")
        return 1

    overlay = _start_overlay()
    log(f"listener(stream): overlay={overlay is not None}")
    committed = []   # 已上屏的句子
    current = ""     # 当前句最新中间文本
    last_hint = 0.0

    def show(text):
        if overlay is not None:
            _overlay_send(overlay, "SHOW " + text)

    def handle_result(text, sentence_end):
        nonlocal committed, current, last_hint
        if sentence_end:
            _overlay_send(overlay, "HIDE")
            if text.strip():
                _input_text(text)
                committed.append(text)
            current = ""
        else:
            current = text
            if overlay is not None:
                show(text)
            else:
                now = time.monotonic()
                if text and now - last_hint > 0.7:
                    notify("…" + text, expire_ms=1200)
                    last_hint = now

    finished = False
    stop_reason = None
    start = time.monotonic()
    rc = 1

    try:
        ws.send(json.dumps(run_task))
        ws.settimeout(0.05)

        # 等待 task-started
        while True:
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                raw = None
                break
            if isinstance(raw, bytes):
                continue
            ev = json.loads(raw)
            event = (ev.get("header") or {}).get("event")
            if event == "task-started":
                break
            if event == "task-failed":
                raise RuntimeError((ev.get("header") or {}).get("error_message", "task-failed"))

        last_activity = start
        with sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=1024
        ) as stream:
            while not finished:
                try:
                    indata, _ = stream.read(1024)
                except Exception as e:
                    log(f"listener(stream): 录音异常 {e!r}")
                    break
                rms = float(np.sqrt(np.mean(indata ** 2)))
                if rms < vad_threshold:
                    pcm = np.zeros(1024, dtype=np.int16).tobytes()
                else:
                    pcm = (np.clip(indata, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
                try:
                    ws.send(pcm, opcode=websocket.ABNF.OPCODE_BINARY)
                except Exception as e:
                    log(f"listener(stream): 发送异常 {e!r}")
                    break

                while True:
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        break
                    except Exception:
                        raw = None
                        break
                    if raw is None or isinstance(raw, bytes):
                        break
                    ev = json.loads(raw)
                    event = (ev.get("header") or {}).get("event")
                    if event == "result-generated":
                        sentence = (ev.get("payload") or {}).get("output", {}).get("sentence", {})
                        if sentence.get("heartbeat"):
                            continue
                        last_activity = time.monotonic()
                        handle_result(sentence.get("text", ""), bool(sentence.get("sentence_end")))
                    elif event == "task-failed":
                        raise RuntimeError((ev.get("header") or {}).get("error_message", "task-failed"))
                    elif event == "task-finished":
                        finished = True

                if STOP_FILE.exists():
                    stop_reason = "manual"
                    break
                if time.monotonic() - start >= max_duration:
                    stop_reason = "max"
                    break
                if time.monotonic() - last_activity >= stream_stop_silence:
                    stop_reason = "silence"
                    break

        ws.send(json.dumps({
            "header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {"input": {}},
        }))
        ws.settimeout(2)
        while not finished:
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                break
            except Exception:
                break
            if raw is None or isinstance(raw, bytes):
                continue
            ev = json.loads(raw)
            event = (ev.get("header") or {}).get("event")
            if event == "result-generated":
                sentence = (ev.get("payload") or {}).get("output", {}).get("sentence", {})
                if not sentence.get("heartbeat"):
                    handle_result(sentence.get("text", ""), bool(sentence.get("sentence_end")))
            elif event == "task-finished":
                finished = True
            elif event == "task-failed":
                raise RuntimeError((ev.get("header") or {}).get("error_message", "task-failed"))
        rc = 0
    except RuntimeError as e:
        notify(f"流式识别失败：{e}")
        log(f"listener(stream): {e}")
    except Exception as e:
        notify(f"流式识别出错：{e!r}")
        log(f"listener(stream): 出错 {e!r}")
    finally:
        try:
            ws.close()
        except Exception:
            pass
        _overlay_close(overlay)

    _cleanup_listener()
    log(f"listener(stream): 结束（原因={stop_reason}，已上屏 {len(committed)} 句）")
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


def _find_gi_python():
    candidates = ["/usr/bin/python3"]
    w = shutil.which("python3")
    if w and w not in candidates:
        candidates.append(w)
    for cand in candidates:
        if not cand:
            continue
        try:
            r = subprocess.run(
                [cand, "-c", "import gi; gi.require_version('Gtk', '3.0'); import gi.repository.Gtk"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if r.returncode == 0:
                return cand
        except Exception:
            continue
    return None


def _start_overlay():
    if not OVERLAY_SCRIPT.exists():
        return None
    py = _find_gi_python()
    if not py:
        return None
    err = subprocess.DEVNULL
    try:
        err = open(CONFIG_FILE.parent / "overlay.log", "ab")
    except Exception:
        err = subprocess.DEVNULL
    try:
        return subprocess.Popen(
            [py, str(OVERLAY_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=err,
        )
    except Exception:
        return None


def _overlay_send(proc, cmd):
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.stdin.write((cmd.replace("\n", " ") + "\n").encode("utf-8"))
        proc.stdin.flush()
    except Exception:
        pass


def _overlay_close(proc):
    if proc is None:
        return
    try:
        _overlay_send(proc, "QUIT")
        proc.stdin.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=1)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


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
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--listen", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if ensure_config():
        return 1
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