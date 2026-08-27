"""Shared terminal TUI primitives for Agent Deck and Host Deck."""

import codecs
import os
import re
import select
import shutil
import sys
import termios
import time
import tty
import unicodedata

HOME = os.path.expanduser("~")

ESC = "\x1b"
def FG(c):
    if not (isinstance(c, str) and len(c) >= 7 and c[0] == "#"):
        return ""
    try:
        return f"{ESC}[38;2;{int(c[1:3],16)};{int(c[3:5],16)};{int(c[5:7],16)}m"
    except ValueError:
        return ""
BG = lambda c: f"{ESC}[48;2;{int(c[1:3],16)};{int(c[3:5],16)};{int(c[5:7],16)}m"
RESET = f"{ESC}[0m"
BOLD = f"{ESC}[1m"
DIM = FG("#6b7280")
MUTED = FG("#9ca3af")
TEXT = FG("#e5e7eb")
ACCENT = FG("#d97757")
GREEN = FG("#4ade80")
YELLOW = FG("#fbbf24")
RED = FG("#f87171")
SELBG = BG("#2f3542")

BOX_W = 72          # 卡片内容宽度（不含左右边框），启动时按终端宽度收窄


def dwidth(s: str) -> int:
    """终端显示宽度：中日韩宽字符算 2 格。"""
    w = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def pad(s: str, w: int) -> str:
    """右侧补空格到指定显示宽度；超长则截断加省略号。"""
    cur = dwidth(s)
    if cur <= w:
        return s + " " * (w - cur)
    out, acc = "", 0
    for ch in s:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if acc + cw > w - 1:
            break
        out += ch
        acc += cw
    return out + "…" + " " * (w - acc - 1)


def pad_tail(s: str, w: int) -> str:
    """超长时保留尾部（项目名比顶层目录更有辨识度），前面加省略号。"""
    if dwidth(s) <= w:
        return s + " " * (w - dwidth(s))
    out, acc = "", 0
    for ch in reversed(s):
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if acc + cw > w - 1:
            break
        out = ch + out
        acc += cw
    return "…" + out + " " * (w - acc - 1)


def shorten(p: str) -> str:
    return "~" + p[len(HOME):] if p == HOME or p.startswith(HOME + "/") else p


def expand(p: str) -> str:
    return os.path.abspath(os.path.expanduser(os.path.expandvars(p)))


def ago(ts: float) -> str:
    d = time.time() - ts
    if d < 90:
        return "刚刚"
    if d < 3600:
        return f"{int(d // 60)} 分钟前"
    if d < 86400:
        return f"{int(d // 3600)} 小时前"
    if d < 172800:
        return "昨天"
    if d < 86400 * 30:
        return f"{int(d // 86400)} 天前"
    return time.strftime("%m-%d", time.localtime(ts))


class Term:
    """raw 模式 + 备用屏 + SGR 鼠标上报。"""

    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.saved = None
        # 自己管缓冲：sys.stdin.read(1) 会预读一大块，导致 select 误判
        # ESC 后面没有后续字节，方向键会被当成单独的 Esc。
        self.buf = []
        self.dec = codecs.getincrementaldecoder("utf-8")("replace")

    def __enter__(self):
        self.saved = termios.tcgetattr(self.fd)
        tty.setraw(self.fd)
        # 1049 备用屏 · 25l 隐藏光标 · 1003 移动上报 · 1006 SGR 编码
        sys.stdout.write(f"{ESC}[?1049h{ESC}[?25l{ESC}[?1003h{ESC}[?1006h")
        sys.stdout.flush()
        return self

    def __exit__(self, *_):
        sys.stdout.write(f"{ESC}[?1006l{ESC}[?1003l{ESC}[?25h{ESC}[?1049l")
        sys.stdout.flush()
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def show_cursor(self, on: bool):
        sys.stdout.write(f"{ESC}[?25h" if on else f"{ESC}[?25l")
        sys.stdout.flush()

    def _getch(self, timeout=None):
        """取一个字符；timeout 秒内没有则返回 None（timeout=None 阻塞）。"""
        while not self.buf:
            if timeout is not None and not select.select([self.fd], [], [], timeout)[0]:
                return None
            data = os.read(self.fd, 1024)
            if not data:
                return None
            self.buf.extend(self.dec.decode(data))
        return self.buf.pop(0)

    def key(self):
        """返回 ('key', 名字) 或 ('mouse', 行, 列, 类型)。"""
        ch = self._getch()
        if ch is None:
            return ("key", "q")
        if ch != ESC:
            return ("key", ch)
        seq = self._getch(0.05)
        if seq is None:
            return ("key", "esc")
        if seq == "[":
            buf = ""
            while True:
                c = self._getch(0.3)
                if c is None:
                    break
                buf += c
                if c.isalpha() or c == "~":
                    break
                if len(buf) > 24:
                    break
            m = re.match(r"^<(\d+);(\d+);(\d+)([Mm])$", buf)
            if m:
                btn, col, row, updown = int(m[1]), int(m[2]), int(m[3]), m[4]
                if btn == 64:
                    return ("key", "up")
                if btn == 65:
                    return ("key", "down")
                if btn & 32 and updown == "M":
                    return ("mouse", row, col, "move")
                if btn == 0 and updown == "M":
                    return ("mouse", row, col, "click")
                return ("key", "")
            return ("key", {"A": "up", "B": "down", "C": "right",
                            "D": "left", "H": "home", "F": "end"}.get(buf, ""))
        return ("key", "esc")


def frame(title: str, right: str):
    """标题卡片三行，宽度 BOX_W+2，不带外层缩进（居中由 draw 统一加）。"""
    inner = BOX_W
    t = f" {title}"
    # title 里带颜色码，量宽度前先剥掉，否则右侧信息顶不到右边
    gap = inner - dwidth(strip_ansi(t)) - dwidth(strip_ansi(right)) - 1
    gap = max(gap, 1)
    line = f"{t}{' ' * gap}{DIM}{right}{RESET} "
    return [f"{DIM}╭{'─' * inner}╮{RESET}",
            f"{DIM}│{RESET}{line}{DIM}│{RESET}",
            f"{DIM}╰{'─' * inner}╯{RESET}"]


def tab_header(prefix_plain, prefix, labels, active, hover=None, color="#e5e7eb"):
    """渲染可点击标题 Tab，并返回相对标题起点的鼠标区域。

    labels: [(id, " Label ")]
    """
    parts = [prefix]
    regions = []
    offset = dwidth(prefix_plain)
    for i, (mode, label) in enumerate(labels):
        if i:
            parts.append(f"{DIM}|{RESET}")
            offset += 1
        is_active = mode == active
        hovered = hover == mode and not is_active
        bg = SELBG if is_active or hovered else ""
        fg = FG(color) if is_active or hovered else DIM
        weight = BOLD if is_active else ""
        parts.append(f"{bg}{fg}{weight}{label}{RESET}")
        width = dwidth(label)
        regions.append({"start": offset, "end": offset + width - 1, "mode": mode})
        offset += width
    return "".join(parts), regions


def draw(header_title, header_right, rows, sel, footer, header_regions=None,
         box_max=72):
    """rows: [(可选中?, 渲染函数(selected)->str)]。

    整块内容在终端里水平 + 垂直居中。返回几何信息，供鼠标命中判定和
    路径输入行定位使用。
    render 可挂 hotspots: [{"kind", "start", "end"}]，坐标相对该行内容
    （0 起，按显示宽度）。
    """
    global BOX_W
    cols, rows_h = shutil.get_terminal_size((100, 30))
    cap = 72 if box_max is None else max(52, box_max)
    BOX_W = max(52, min(cap, cols - 6))
    width = BOX_W + 2                       # 卡片总宽（含边框）

    lines = frame(header_title, header_right)
    lines.append("")
    rowmap = {}
    pending = []
    idx = 0
    for selectable, render in rows:
        if selectable:
            rowmap[len(lines)] = idx        # 先记块内偏移，稍后加上 top
            lines.append(render(idx == sel))
            spots = getattr(render, "hotspots", None)
            if spots:
                pending.append((len(lines) - 1, idx, spots))
            idx += 1
        else:
            lines.append(render(False))
    lines.append("")
    lines.append(f" {DIM}{'─' * BOX_W}{RESET} ")
    lines.append(f"  {DIM}{footer}{RESET}")

    left = max((cols - width) // 2, 0)
    top = max((rows_h - len(lines)) // 2, 0)
    pad_l = " " * left

    out = [""] * top + [pad_l + ln if ln else "" for ln in lines]
    # 2026 同步输出：Windows Terminal 会等整帧画完再显示，避免先清屏后闪一下。
    # 不用 2J 整页擦除；每行 K 清到行尾，最后 J 清掉下面的旧内容。
    parts = [f"{ESC}[?2026h{ESC}[H"]
    for i, ln in enumerate(out):
        parts.append(ln)
        parts.append(f"{ESC}[K")
        if i + 1 < len(out):
            parts.append("\r\n")
    parts.append(f"{ESC}[J{ESC}[?2026l")
    sys.stdout.write("".join(parts))
    sys.stdout.flush()
    # 屏幕行号从 1 起：块内偏移 + top + 1
    # 标题文字首字符在 left + 3；标题内容位于 frame 的第二行。
    tabs = [
        {**region,
         "start": left + 3 + region["start"],
         "end": left + 3 + region["end"],
         "row": top + 2}
        for region in (header_regions or [])
    ]
    cells = []
    for v_row, index, spots in pending:
        screen_row = v_row + top + 1
        for spot in spots:
            cells.append({
                "row": screen_row,
                "start": left + spot["start"] + 1,
                "end": left + spot["end"] + 1,
                "kind": spot["kind"],
                "index": index,
            })
    return {"rows": {v_row + top + 1: i for v_row, i in rowmap.items()},
            "tabs": tabs, "cells": cells, "left": left, "width": width,
            "bottom": top + len(lines)}


def hit(geom, row, col):
    """鼠标点击是否落在某一可选行上；是则返回索引，否则 None。"""
    if not (geom["left"] < col <= geom["left"] + geom["width"]):
        return None
    return geom["rows"].get(row)


def hit_tab(geom, row, col):
    for region in geom.get("tabs", []):
        if row == region["row"] and region["start"] <= col <= region["end"]:
            return region["mode"]
    return None


def hit_cell(geom, row, col):
    for cell in geom.get("cells") or []:
        if row == cell["row"] and cell["start"] <= col <= cell["end"]:
            return cell
    return None


def strip_ansi(s: str) -> str:
    return re.sub(rf"{ESC}\[[0-9;]*m", "", s)


def fit_row(body: str) -> str:
    """把整行（含高亮底色）补满卡片宽度，让选中条不缺一截。"""
    n = max(BOX_W + 2 - dwidth(strip_ansi(body)), 0)
    return body + " " * n + RESET


def action_row(symbol, label, note, color):
    def render(selected):
        mark = f"{FG(color)}▸{RESET}" if selected else " "
        bg = SELBG if selected else ""
        label_w = min(28, max(20, BOX_W // 2))
        note_w = max(BOX_W - label_w - 12, 10)
        body = (f"{bg}  {mark}{bg}    {FG(color)}{symbol} "
                f"{BOLD if selected else ''}{pad(label, label_w)}{RESET}{bg} "
                f"{DIM}{pad_tail(note, note_w)}{RESET}{bg}")
        return fit_row(body)
    return render


def pad_ansi(s: str, w: int) -> str:
    """给已带颜色码的短串补空格（按可见字符算宽）。"""
    return s + " " * max(w - dwidth(strip_ansi(s)), 0)
