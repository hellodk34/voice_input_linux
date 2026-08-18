#!/usr/bin/env bash
# Qwen-Audio 语音输入 —— 环境探测脚本（只检测 + 给建议，不安装任何东西）
# 用法: ./detect.sh
set -u
cd "$(dirname "$0")"

[ -f /etc/os-release ] && . /etc/os-release
ID="${ID:-unknown}"
VERSION="${VERSION_ID:-}"

DESKTOP_RAW="${XDG_CURRENT_DESKTOP:-}"
DESKTOP="${DESKTOP_RAW,,}"   # 统一小写，方便匹配
if [ -n "${WAYLAND_DISPLAY:-}" ]; then
    DS="wayland"
elif [ -n "${DISPLAY:-}" ]; then
    DS="x11"
else
    DS="unknown"
fi

echo "================ 系统环境 ================"
echo "  发行版      : $ID ${VERSION}"
echo "  桌面环境    : ${DESKTOP_RAW:-未知}"
echo "  显示服务器  : $DS"

echo ""
echo "================ 软件检测 ================"
have() {
    command -v "$1" >/dev/null 2>&1
}
have_lib() {
    # 优先查 ldconfig 缓存；缓存缺失/为空时（部分系统 ldconfig 未刷新）
    # 回退到常见库目录直接查文件。兼容传入带或不带 lib 前缀。
    if ldconfig -p 2>/dev/null | grep -q "$1"; then
        return 0
    fi
    for pat in /usr/lib/*/"$1".so* /usr/lib/*/lib"$1".so* \
               /usr/lib64/"$1".so* /usr/lib64/lib"$1".so* \
               /usr/lib/"$1".so* /usr/lib/lib"$1".so* \
               /usr/local/lib/"$1".so* /usr/local/lib/lib"$1".so* \
               /lib/*/"$1".so* /lib/*/lib"$1".so*; do
        compgen -G "$pat" >/dev/null && return 0
    done
    return 1
}
check() {
    if have "$1"; then
        printf "  [OK]   %-12s %s\n" "$1" "$2"
    else
        printf "  [MISS] %-12s %s\n" "$1" "$2"
    fi
}
check notify-send  "系统通知（通用）"
check wl-copy      "Wayland 剪贴板"
check xclip        "X11 剪贴板"
check xsel         "X11 剪贴板（备选）"
check ydotool      "Wayland 上屏（GNOME 必装）"
check wtype        "Wayland 上屏（KDE/Sway）"
check xdotool      "X11 上屏"
if have_lib "libportaudio"; then
    echo "  [OK]   PortAudio    录音库"
else
    echo "  [MISS] PortAudio    录音库"
fi

echo ""
echo "================ Python 依赖 ================"
if [ -x .venv/bin/python ]; then
    for m in sounddevice numpy; do
        if .venv/bin/python -c "import $m" >/dev/null 2>&1; then
            printf "  [OK]   %s\n" "$m"
        else
            printf "  [MISS] %s\n" "$m"
        fi
    done
else
    echo "  [MISS] .venv 不存在，请先运行 ./setup.sh 或 python3 -m venv .venv"
fi

echo ""
echo "================ 安装建议 ================"

pkg_name() {
    case "$1" in
        portaudio)
            case "$ID" in
                debian|ubuntu|linuxmint|pop|raspbian|elementary) echo "libportaudio2" ;;
                *) echo "portaudio" ;;
            esac ;;
        libnotify)
            case "$ID" in
                debian|ubuntu|linuxmint|pop|raspbian|elementary) echo "libnotify-bin" ;;
                *) echo "libnotify" ;;
            esac ;;
        wl-copy) echo "wl-clipboard" ;;
        *) echo "$1" ;;
    esac
}

install_cmd() {
    case "$ID" in
        debian|ubuntu|linuxmint|pop|raspbian|elementary) echo "sudo apt-get update && sudo apt-get install -y $*" ;;
        fedora|rhel|centos|rocky|alma)                    echo "sudo dnf install -y $*" ;;
        arch|manjaro|endeavouros|arcolinux)               echo "sudo pacman -Sy --noconfirm $*" ;;
        opensuse*|suse|sled|sles)                         echo "sudo zypper --non-interactive install $*" ;;
        void)                                             echo "sudo xbps-install -Sy $*" ;;
        *)                                                echo "（未识别的发行版，请手动安装: $*）" ;;
    esac
}

need=()   # 按当前环境必须的工具（二进制名）
case "$DS" in
    wayland)
        need+=(wl-copy)
        if [[ "$DESKTOP" == *gnome* ]]; then
            need+=(ydotool)
        else
            need+=(wtype)
        fi ;;
    x11)
        need+=(xclip xdotool) ;;
    *)
        need+=(wl-copy xclip xdotool) ;;
esac

missing=()
# 录音库 + 通知（按库/二进制检测）
if ! have_lib "libportaudio"; then missing+=("$(pkg_name portaudio)"); fi
if ! have notify-send;       then missing+=("$(pkg_name libnotify)"); fi
# 剪贴板/上屏工具（按二进制检测）
for t in "${need[@]}"; do
    if ! have "$t"; then missing+=("$(pkg_name "$t")"); fi
done

if [ "${#missing[@]}" -eq 0 ]; then
    echo "  系统工具齐全，无需额外安装。"
else
    echo "  按当前环境（$DS / ${DESKTOP_RAW:-未知}），建议安装以下系统包："
    printf '    - %s\n' "${missing[@]}"
    echo ""
    echo "  一键执行（自动匹配发行版）："
    echo "    $(install_cmd "${missing[@]}")"
fi

if [ ! -x .venv/bin/python ]; then
    echo ""
    echo "  Python 虚拟环境:"
    echo "    python3 -m venv .venv"
fi
if [ -x .venv/bin/python ] && ! .venv/bin/python -c "import sounddevice" >/dev/null 2>&1; then
    echo ""
    echo "  Python 依赖:"
    echo "    .venv/bin/pip install sounddevice numpy"
fi

echo ""
echo "================ 上屏能力评估 ================"
case "$DS" in
    x11)
        if have xdotool; then
            echo "  ✅ X11 + xdotool：可自动上屏"
        else
            echo "  ⚠️  X11 但缺少 xdotool：只能复制，需手动 Ctrl+V"
        fi ;;
    wayland)
        if [[ "$DESKTOP" == *gnome* ]]; then
            if have ydotool; then
                if id -nG | grep -qw input; then
                    echo "  ✅ GNOME Wayland + ydotool + input 组：可自动上屏"
                elif getfacl -p /dev/uinput 2>/dev/null | grep -qw "$(id -un)"; then
                    echo "  ✅ GNOME Wayland + ydotool + /dev/uinput ACL：可自动上屏"
                else
                    echo "  ⚠️  ydotool 已装，但当前用户不在 input 组且无 /dev/uinput 权限："
                    echo "       sudo usermod -aG input \$USER   # 然后重登"
                    echo "       或 setfacl -m u:\$USER:rw /dev/uinput"
                fi
            else
                echo "  ⚠️  GNOME Wayland 缺少 ydotool：不能自动上屏，请安装并加入 input 组"
            fi
        else
            if have wtype; then
                echo "  ✅ $DESKTOP_RAW Wayland + wtype：可自动上屏"
            elif have ydotool; then
                echo "  ✅ $DESKTOP_RAW Wayland + ydotool：可自动上屏"
            else
                echo "  ⚠️  $DESKTOP_RAW Wayland 缺少 wtype/ydotool：不能自动上屏"
            fi
        fi ;;
    *)
        echo "  ⚠️  未检测到显示服务器（可能是纯终端环境）" ;;
esac

echo ""
echo "完整安装（含建虚拟环境、装 Python 依赖）请运行: ./setup.sh"
