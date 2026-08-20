#!/usr/bin/env bash
# Qwen-Audio 语音输入 —— 麦克风底噪检测 & 推荐 vad_threshold / batch_silence_timeout
# 用法: ./detect_mic.sh
# 说明: 只检测当前「系统默认」输入设备的底噪并打印推荐值，不修改任何配置。
set -u
cd "$(dirname "$0")"

have() { command -v "$1" >/dev/null 2>&1; }

echo "=================================================="
echo " 麦克风底噪检测（推荐 vad_threshold 等配置）"
echo "=================================================="
echo ""
echo "开始前请先完成："
echo "  1. 打开「系统设置 → 声音 → 输入」，选择你要用的麦克风，"
echo "     并设为默认输入设备；"
echo "  2. 对着它正常说话，观察电平条是否跳动，确认能正常收音。"
echo ""
echo "本脚本只检测当前「系统默认」输入设备。"
echo "=================================================="
read -r -p "设置好了？按回车开始检测（Ctrl+C 取消）" _ || exit 1
echo ""

# ---------- 1. 检测是否有可用麦克风 ----------
echo "== 检测麦克风 =="
mic_found=0
if have arecord && arecord -l 2>/dev/null | grep -qi 'capture'; then
    mic_found=1
fi
if [ "$mic_found" -eq 0 ] && have pactl && pactl list sources short 2>/dev/null | grep -viE 'monitor' | grep -q .; then
    mic_found=1
fi
if [ "$mic_found" -eq 0 ] && [ -r /proc/asound/pcm ] && grep -qi 'capture' /proc/asound/pcm 2>/dev/null; then
    mic_found=1
fi

if [ "$mic_found" -eq 0 ]; then
    echo "未检测到可用麦克风。"
    echo "  请检查麦克风是否已连接/启用；若缺少 alsa-utils 请先安装："
    echo "  sudo apt install alsa-utils    （Debian/Ubuntu）"
    exit 1
fi
echo "已检测到麦克风设备。"
echo ""

# ---------- 2. 录制一小段底噪 ----------
DUR=3
echo "== 测量底噪 =="
echo "接下来录制 ${DUR} 秒，请保持安静（不要说话、不要敲键盘）。"
sleep 1

raw="$(mktemp --suffix=.raw 2>/dev/null || echo "/tmp/mic_noise_$$.raw")"
capture_ok=0

if have arecord; then
    if arecord -q -f S16_LE -r 16000 -c 1 -d "$DUR" "$raw" 2>/dev/null; then
        capture_ok=1
    elif arecord -q -f S16_LE -r 16000 -c 2 -d "$DUR" "$raw" 2>/dev/null; then
        capture_ok=1
    fi
fi

if [ "$capture_ok" -eq 0 ] && have parec; then
    timeout "$((DUR + 1))" parec --format=s16le --rate=16000 --channels=1 --raw > "$raw" 2>/dev/null
    rc=$?
    if [ "$rc" -eq 0 ] || [ "$rc" -eq 124 ]; then
        capture_ok=1
    fi
fi

if [ "$capture_ok" -eq 0 ] && have sox; then
    if sox -q -d -t raw -r 16000 -c 1 -e signed -b 16 "$raw" trim 0 "$DUR" 2>/dev/null; then
        capture_ok=1
    fi
fi

if [ "$capture_ok" -eq 0 ]; then
    rm -f "$raw"
    echo "无法录音测量，需要 alsa-utils(arecord) / pulseaudio-utils(parec) / sox 其中之一："
    echo "  sudo apt install alsa-utils    （Debian/Ubuntu）"
    exit 1
fi

# ---------- 3. 分析底噪并给出推荐 ----------
if have python3; then
    python3 - "$raw" <<'PY'
import sys, struct, math

raw = open(sys.argv[1], "rb").read()
n = len(raw) // 2
if n == 0:
    print("录音数据为空，无法分析。请检查麦克风是否被静音。")
    sys.exit(2)

vals = struct.unpack("<%dh" % n, raw)
f = sorted(abs(v) / 32768.0 for v in vals)
rms = math.sqrt(sum(x * x for x in f) / n)
p95 = f[int(n * 0.95)]
db = lambda x: 20 * math.log10(x + 1e-12)

print(f"样本数 {n}（约 {n / 16000:.1f} 秒）")
print(f"底噪 RMS       : {rms:.5f}（{db(rms):.1f} dBFS）")
print(f"底噪 95 分位   : {p95:.5f}（{db(p95):.1f} dBFS）")

if p95 > 0.7:
    print()
    print("⚠ 输入接近满幅，疑似选错设备或输入悬空/顶死，")
    print("  请回到「系统设置 → 声音 → 输入」重新选择麦克风后重试。")
    sys.exit(2)
if p95 < 0.0005:
    print()
    print("⚠ 检测到几乎完全静音，麦克风可能被静音或增益为 0，")
    print("  请检查系统声音设置后重试。")
    sys.exit(2)

if p95 < 0.005:
    level, vad, timeout = "很低", "0.02", "2.5"
    desc = "麦克风非常安静、底噪极低，能清晰捕捉轻声说话"
elif p95 < 0.015:
    level, vad, timeout = "低", "0.03", "2.0"
    desc = "麦克风比较安静干净，日常使用很舒服"
elif p95 < 0.03:
    level, vad, timeout = "中等", "0.05", "1.5"
    desc = "有一定底噪，建议抬高阈值过滤环境声"
else:
    level, vad, timeout = "较高", "0.08", "1.5"
    desc = "底噪偏大、环境噪声较多，建议系统层面降噪或换个麦克风"

print()
print(f"底噪等级：{level}（{desc}）")
print("推荐配置（写入 ~/.config/qwen-voice-input/config.ini 的 [general] 段）：")
print(f"  vad_threshold         = {vad}")
print(f"  batch_silence_timeout = {timeout}")
print()
print("说明：以上仅根据底噪自动估算，实际以说话测试为准——")
print("  说完仍不结束就把 vad_threshold 调大；小声识别不到就调小。")
PY
    rc=$?
    rm -f "$raw"
    exit "$rc"
fi

# 无 python3 的回退：仅用 awk 按 RMS 粗估（Debian 桌面一般都有 python3）
echo "未找到 python3，改用 awk 按 RMS 粗略估算。"
od -An -v -t d2 "$raw" 2>/dev/null | awk '
  { for (i = 1; i <= NF; i++) { x = $i / 32768.0; s += x * x; n++ } }
  END {
    rms = (n > 0) ? sqrt(s / n) : 0
    printf "底噪 RMS: %.5f\n", rms
    if (rms > 0.7)  { print "⚠ 输入接近满幅，请重新选择麦克风。"; exit 2 }
    if (rms < 0.0005) { print "⚠ 几乎完全静音，请检查麦克风是否被静音。"; exit 2 }
    if (rms < 0.01)      print "推荐: vad_threshold = 0.03, batch_silence_timeout = 2.0"
    else if (rms < 0.03) print "推荐: vad_threshold = 0.05, batch_silence_timeout = 1.5"
    else                 print "推荐: vad_threshold = 0.08, batch_silence_timeout = 1.5"
    print "（awk 回退仅用 RMS，结果较粗略，建议安装 python3 获得更准确推荐）"
  }'
rc=$?
rm -f "$raw"
exit "$rc"
