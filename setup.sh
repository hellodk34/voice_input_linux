#!/usr/bin/env bash
# Qwen-Audio 语音输入 —— 一键安装脚本（多发行版 / 多桌面环境）
set -euo pipefail
cd "$(dirname "$0")"

[ -f /etc/os-release ] && . /etc/os-release
ID="${ID:-unknown}"

DESKTOP_RAW="${XDG_CURRENT_DESKTOP:-}"
DESKTOP="${DESKTOP_RAW,,}"   # 统一小写，方便匹配
if [ -n "${WAYLAND_DISPLAY:-}" ]; then
    DS="wayland"
elif [ -n "${DISPLAY:-}" ]; then
    DS="x11"
else
    DS="unknown"
fi

pkg_name() {
    local tool="$1"
    case "$tool" in
        portaudio)
            case "$ID" in
                debian|ubuntu|linuxmint|pop|raspbian|elementary) echo "libportaudio2" ;;
                *) echo "portaudio" ;;
            esac ;;
        notify-send)
            case "$ID" in
                debian|ubuntu|linuxmint|pop|raspbian|elementary) echo "libnotify-bin" ;;
                *) echo "libnotify" ;;
            esac ;;
        wl-copy) echo "wl-clipboard" ;;
        *) echo "$tool" ;;
    esac
}

install_cmd() {
    case "$ID" in
        debian|ubuntu|linuxmint|pop|raspbian|elementary)
            sudo apt-get update && sudo apt-get install -y "$@" ;;
        fedora|rhel|centos|rocky|alma)
            sudo dnf install -y "$@" ;;
        arch|manjaro|endeavouros|arcolinux)
            sudo pacman -Sy --noconfirm "$@" ;;
        opensuse*|suse|sled|sles)
            sudo zypper --non-interactive install "$@" ;;
        void)
            sudo xbps-install -Sy "$@" ;;
        *)
            echo "!! 未识别的发行版（ID=$ID），请手动安装以下软件包："
            printf '   %s\n' "$@"
            ;;
    esac
}

have() {
    command -v "$1" >/dev/null 2>&1
}
have_lib() {
    # 优先查 ldconfig 缓存；缓存缺失/为空时回退到常见库目录直接查文件
    if ldconfig -p 2>/dev/null | grep -q "$1"; then
        return 0
    fi
    # 兼容传入带或不带 lib 前缀（如 libportaudio / portaudio）
    for pat in /usr/lib/*/"$1".so* /usr/lib/*/lib"$1".so* \
               /usr/lib64/"$1".so* /usr/lib64/lib"$1".so* \
               /usr/lib/"$1".so* /usr/lib/lib"$1".so* \
               /usr/local/lib/"$1".so* /usr/local/lib/lib"$1".so* \
               /lib/*/"$1".so* /lib/*/lib"$1".so*; do
        compgen -G "$pat" >/dev/null && return 0
    done
    return 1
}

need=(portaudio notify-send)
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

pkglist=()
for t in "${need[@]}"; do
    pkglist+=("$(pkg_name "$t")")
done

echo "=============================================="
echo " 发行版: $ID"
echo " 桌面环境: ${DESKTOP_RAW:-未知}"
echo " 显示服务器: $DS"
echo "=============================================="
echo "==> 安装系统包: ${pkglist[*]}"
install_cmd "${pkglist[@]}"

echo "==> 创建虚拟环境并安装 Python 依赖"
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install sounddevice numpy websocket-client

if [[ " ${pkglist[*]} " == *"ydotool"* ]]; then
    echo ""
    echo "==> ydotool 提示（Wayland 上屏需要）"
    if ! id -nG | grep -qw input; then
        if getfacl -p /dev/uinput 2>/dev/null | grep -qw "$(id -un)"; then
            echo "    /dev/uinput 已有你的 ACL 权限，可直接上屏，无需处理。"
        else
            echo "    当前用户不在 input 组，请执行："
            echo "      sudo usermod -aG input \$USER"
            echo "    然后注销重登（或重启）后生效。"
            echo "    （或用 setfacl -m u:\$USER:rw /dev/uinput 替代）"
        fi
    fi
fi

echo ""
echo "==> 环境自检"
MISSING=()
PYMISSING=()
for t in "${need[@]}"; do
    if [ "$t" = "portaudio" ]; then
        if have_lib "$t"; then
            printf "  [OK]   %-12s %s\n" "$t" "录音库"
        else
            printf "  [MISS] %-12s %s\n" "$t" "录音库"
            MISSING+=("$(pkg_name "$t")")
        fi
    elif have "$t"; then
        printf "  [OK]   %s\n" "$t"
    else
        printf "  [MISS] %s\n" "$t"
        MISSING+=("$(pkg_name "$t")")
    fi
done
for m in sounddevice numpy websocket-client; do
    case "$m" in
        websocket-client) mod=websocket ;;
        *) mod="$m" ;;
    esac
    if .venv/bin/python -c "import $mod" >/dev/null 2>&1; then
        printf "  [OK]   %-12s %s\n" "$m" "Python 依赖"
    else
        printf "  [MISS] %-12s %s\n" "$m" "Python 依赖"
        PYMISSING+=("$m")
    fi
done
if [ "${#MISSING[@]}" -gt 0 ]; then
    echo "  仍缺少系统包: ${MISSING[*]}，请参考上方提示手动安装。"
fi
if [ "${#PYMISSING[@]}" -gt 0 ]; then
    echo "  仍缺少 Python 依赖: ${PYMISSING[*]}，请运行 .venv/bin/pip install ${PYMISSING[*]}"
fi
if [ "${#MISSING[@]}" -eq 0 ] && [ "${#PYMISSING[@]}" -eq 0 ]; then
    echo "  依赖全部就绪。"
fi

PY="$(pwd)/.venv/bin/python"
SCRIPT="$(pwd)/voice_input.py"

echo ""
echo "==> 下一步"
echo "  1. 编辑 ~/.config/qwen-voice-input/config.ini 填入 api_key"
echo "  2. 配置全局快捷键，命令为:"
echo "       $PY $SCRIPT"
echo "  3. 各桌面环境设置位置:"
case "$DS:$DESKTOP" in
    *:*gnome*)
        echo "     GNOME: 设置 → 键盘 → 查看及自定义快捷键 → 自定义快捷键" ;;
    *:*kde*|*:*plasma*)
        echo "     KDE Plasma: 系统设置 → 快捷键 → 自定义快捷键 → 新建 → 命令" ;;
    *:*xfce*)
        echo "     XFCE: 设置 → 键盘 → 应用程序快捷键" ;;
    *:*cinnamon*)
        echo "     Cinnamon: 系统设置 → 键盘 → 快捷键" ;;
    *)
        echo "     请在你的桌面环境设置中绑定一个全局快捷键调用上面的命令" ;;
esac
echo "  4. 使用: 按一次快捷键开始说话，说完静音 2 秒自动识别上屏；"
echo "     录音中再按一次 = 手动停止。"
