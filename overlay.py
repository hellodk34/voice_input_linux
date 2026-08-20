#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""流式识别浮动显示层。

在悬浮窗口中展示"边说边出字"的中间结果，整句被整体替换（复刻输入法
preedit 的视觉效果），最终结果由主程序负责一次性上屏，本窗口只负责预览。

命令（从 stdin 逐行读取，由主程序写入）：
    SHOW <text>   显示文本（空字符串显示 "…"）
    HIDE          隐藏窗口
    QUIT          退出

窗口特性：不抢焦点、置顶、无边框、不占任务栏；背景 50% 透明（不遮挡底层内容），
白色字体，圆角。位置尽量贴主显示器底部，Wayland 下若被合成器忽略则默认居中。
"""

import sys
import threading
from math import pi

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango

CSS = b"""
#overlay-label {
    color: #ffffff;
}
"""

# 半透明深色背景（RGB + alpha=0.5，即 50% 透明）
BG_RGBA = (0.05, 0.05, 0.07, 0.5)


def _rounded_rect(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, pi / 2)
    cr.arc(x + r, y + h - r, r, pi / 2, pi)
    cr.arc(x + r, y + r, r, pi, 3 * pi / 2)
    cr.close_path()


def _resize_and_position(win, label):
    """占主显示器下方 33% 高度、左右各留 20%（中间 60%）宽度；字号随高度缩放。"""
    try:
        display = Gdk.Display.get_default()
        mon = None
        try:
            mon = display.get_primary_monitor()
        except Exception:
            pass
        if mon is None:
            mon = display.get_monitor(0)
        geo = mon.get_geometry()
        w = int(geo.width * 0.6)
        h = int(geo.height * 0.33)
        x = geo.x + int(geo.width * 0.2)
        y = geo.y + geo.height - h
        win.set_size_request(w, h)
        win.resize(w, h)
        win.move(max(0, x), max(0, y))
        # 字号按窗口高度的 10% 计算（限幅 24~64 逻辑像素）。用 pt（点）指定，
        # Pango 会按显示 DPI/缩放因子换算，HiDPI（如 4K@200%）下也能自动正确缩放。
        size_px = max(24, min(64, int(h * 0.10)))
        desc = Pango.FontDescription()
        desc.set_size(int(size_px * 72.0 / 96.0 * Pango.SCALE))
        label.override_font(desc)
    except Exception:
        pass


def main():
    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_decorated(False)
    win.set_keep_above(True)
    win.set_accept_focus(False)
    win.set_focus_on_map(False)
    win.set_can_focus(False)
    win.set_type_hint(Gdk.WindowTypeHint.UTILITY)
    win.set_skip_taskbar_hint(True)
    win.set_skip_pager_hint(True)
    win.set_name("qwen-voice-input-overlay")

    screen = Gdk.Screen.get_default()
    try:
        visual = screen.get_rgba_visual()
        if visual:
            win.set_visual(visual)
    except Exception:
        pass
    win.set_app_paintable(True)

    label = Gtk.Label()
    label.set_name("overlay-label")
    label.set_line_wrap(True)
    label.set_justify(Gtk.Justification.CENTER)
    label.set_halign(Gtk.Align.CENTER)
    label.set_valign(Gtk.Align.CENTER)
    label.set_can_focus(False)
    label.set_margin_top(24)
    label.set_margin_bottom(24)
    label.set_margin_start(32)
    label.set_margin_end(32)
    win.add(label)

    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    def on_draw(widget, cr):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        cr.set_source_rgba(*BG_RGBA)
        _rounded_rect(cr, 0, 0, w, h, 16)
        cr.fill()
        return False

    win.connect("draw", on_draw)

    def handle_line(line):
        line = line.rstrip("\n").rstrip("\r")
        if line.startswith("SHOW "):
            text = line[5:]
            label.set_text(text if text else "…")
            win.show_all()
            _resize_and_position(win, label)
        elif line == "HIDE":
            win.hide()
        elif line == "QUIT":
            Gtk.main_quit()
        return False  # idle_add 不重复调度

    def reader():
        try:
            for line in sys.stdin:
                GLib.idle_add(handle_line, line)
        finally:
            GLib.idle_add(Gtk.main_quit)

    threading.Thread(target=reader, daemon=True).start()
    win.connect("destroy", Gtk.main_quit)
    Gtk.main()


if __name__ == "__main__":
    main()
