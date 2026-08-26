#!/usr/bin/env python3
"""AI 启动器 —— 一个入口选 agent + 选目录，然后起会话。

用法：
  ai                    交互：先选 agent，再选目录
  ai cco                指定 agent，只选目录
  ai cco ~/dev/ai       直接启动，不进菜单
  ai --list             打印 agent 清单
  ai --shell            直接开 shell（选完目录）

配置：~/.config/ai-launcher/agents.toml（可用 AI_LAUNCHER_CONFIG 覆盖）
历史：~/.local/share/ai-launcher/history.tsv（可用 AI_LAUNCHER_HISTORY 覆盖）
"""

import codecs
import os
import re
import select
import shlex
import shutil
import subprocess
import sys
import termios
import time
import tomllib
import tty
import unicodedata
from concurrent.futures import ThreadPoolExecutor

HOME = os.path.expanduser("~")
VERSION = "0.2.1"
CONF = os.environ.get(
    "AI_LAUNCHER_CONFIG",
    os.path.join(HOME, ".config", "ai-launcher", "agents.toml"),
)
HIST = os.environ.get(
    "AI_LAUNCHER_HISTORY",
    os.path.join(HOME, ".local", "share", "ai-launcher", "history.tsv"),
)
HIST_KEEP = 24      # 历史文件里最多留多少条
HIST_SHOW = 8       # 菜单里最多显示多少条
PATH_SHOW = 9       # 路径补全里最多显示多少个子目录

ESC = "\x1b"
FG = lambda c: f"{ESC}[38;2;{int(c[1:3],16)};{int(c[3:5],16)};{int(c[5:7],16)}m"
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


# ─────────────────────────── 宽度与文本 ───────────────────────────

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


# ─────────────────────────── 配置与历史 ───────────────────────────

def load_agents():
    if not os.path.exists(CONF):
        sys.exit(f"找不到配置：{CONF}")
    with open(CONF, "rb") as f:
        cfg = tomllib.load(f)
    default_dir = cfg.get("default_dir", "$HOME/dev")
    agents = cfg.get("agent", [])
    seen = set()
    for i, a in enumerate(agents, 1):
        missing = [field for field in ("key", "name", "cmd") if not a.get(field)]
        if missing:
            sys.exit(f"第 {i} 个 agent 缺少字段：{', '.join(missing)}")
        if a["key"] in seen:
            sys.exit(f"agent key 重复：{a['key']}")
        seen.add(a["key"])
        a.setdefault("default_dir", default_dir)
        a.setdefault("color", "#e5e7eb")
        a.setdefault("note", "")
        a.setdefault("proxy", False)
        a.setdefault("path_prepend", [])
        a.setdefault("env", {})
        a.setdefault("unset", [])
        a.setdefault("wt_profile", "")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", a["color"]):
            sys.exit(f"agent {a['key']} 的 color 不是 #RRGGBB：{a['color']}")
    if not agents:
        sys.exit("配置里没有任何 [[agent]]")
    return agents


def read_history():
    out = []
    if os.path.exists(HIST):
        with open(HIST, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) == 2 and parts[1]:
                    try:
                        out.append((float(parts[0]), parts[1]))
                    except ValueError:
                        pass
    return out


def write_history(path: str):
    rows = [(time.time(), path)] + [r for r in read_history() if r[1] != path]
    os.makedirs(os.path.dirname(HIST), exist_ok=True)
    tmp = HIST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for ts, p in rows[:HIST_KEEP]:
            f.write(f"{ts:.0f}\t{p}\n")
    os.replace(tmp, HIST)


def dir_candidates(agent):
    """历史目录 + 该 agent 默认目录 + ~，去重、去掉已不存在的。"""
    items, seen = [], set()
    for ts, p in read_history():
        if p in seen or not os.path.isdir(p):
            continue
        seen.add(p)
        items.append({"path": p, "ts": ts})
        if len(items) >= HIST_SHOW:
            break
    for extra in (expand(agent["default_dir"]), HOME):
        if extra not in seen and os.path.isdir(extra):
            seen.add(extra)
            items.append({"path": extra, "ts": None})
    return items


def git_info(path: str):
    """返回 (分支, 改动数)；不是 git 仓库返回 (None, 0)。"""
    try:
        b = subprocess.run(["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=1.5)
        if b.returncode != 0:
            return None, 0
        branch = b.stdout.strip() or "HEAD"
        s = subprocess.run(["git", "-C", path, "status", "--porcelain", "-uno"],
                           capture_output=True, text=True, timeout=1.5)
        dirty = len([x for x in s.stdout.splitlines() if x.strip()])
        return branch, dirty
    except Exception:
        return None, 0


def fill_git(items):
    with ThreadPoolExecutor(max_workers=8) as ex:
        for it, res in zip(items, ex.map(lambda i: git_info(i["path"]), items)):
            it["branch"], it["dirty"] = res


# ─────────────────────────── 终端与输入 ───────────────────────────

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
        # 1049 备用屏 · 25l 隐藏光标 · 1000 点击上报 · 1006 SGR 编码
        sys.stdout.write(f"{ESC}[?1049h{ESC}[?25l{ESC}[?1000h{ESC}[?1006h")
        sys.stdout.flush()
        return self

    def __exit__(self, *_):
        sys.stdout.write(f"{ESC}[?1006l{ESC}[?1000l{ESC}[?25h{ESC}[?1049l")
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
                if btn == 0 and updown == "M":
                    return ("mouse", row, col, "click")
                return ("key", "")
            return ("key", {"A": "up", "B": "down", "C": "right",
                            "D": "left", "H": "home", "F": "end"}.get(buf, ""))
        return ("key", "esc")

def path_suggestions(buf: str, limit: int = PATH_SHOW):
    """返回当前路径片段匹配的直接子目录，保留用户输入的路径写法。"""
    if not buf:
        return []
    if buf.endswith(os.sep) or buf in ("~", "$HOME"):
        parent = expand(buf)
        fragment = ""
        prefix = buf if buf.endswith(os.sep) else buf + os.sep
    else:
        raw_parent, fragment = os.path.split(buf)
        parent = expand(raw_parent or os.curdir)
        prefix = buf[:-len(fragment)] if fragment else buf
    if not os.path.isdir(parent):
        return []
    try:
        names = [
            name for name in os.listdir(parent)
            if name.lower().startswith(fragment.lower())
            and (fragment.startswith(".") or not name.startswith("."))
            and os.path.isdir(os.path.join(parent, name))
        ]
    except OSError:
        return []
    names.sort(key=lambda name: (name.lower(), name))
    return [
        {
            "name": name,
            "path": os.path.abspath(os.path.join(parent, name)),
            "input": prefix + name + os.sep,
        }
        for name in names[:limit]
    ]


# ─────────────────────────── 渲染 ───────────────────────────

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


def draw(header_title, header_right, rows, sel, footer):
    """rows: [(可选中?, 渲染函数(selected)->str)]。

    整块内容在终端里水平 + 垂直居中。返回几何信息，供鼠标命中判定和
    路径输入行定位使用。
    """
    global BOX_W
    cols, rows_h = shutil.get_terminal_size((100, 30))
    BOX_W = max(52, min(72, cols - 6))
    width = BOX_W + 2                       # 卡片总宽（含边框）

    lines = frame(header_title, header_right)
    lines.append("")
    rowmap = {}
    idx = 0
    for selectable, render in rows:
        if selectable:
            rowmap[len(lines)] = idx        # 先记块内偏移，稍后加上 top
            lines.append(render(idx == sel))
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
    sys.stdout.write(f"{ESC}[H{ESC}[2J" + "\r\n".join(out))
    sys.stdout.flush()
    # 屏幕行号从 1 起：块内偏移 + top + 1
    return {"rows": {v_row + top + 1: i for v_row, i in rowmap.items()},
            "left": left, "width": width, "bottom": top + len(lines)}


def hit(geom, row, col):
    """鼠标点击是否落在某一可选行上；是则返回索引，否则 None。"""
    if not (geom["left"] < col <= geom["left"] + geom["width"]):
        return None
    return geom["rows"].get(row)


def strip_ansi(s: str) -> str:
    return re.sub(rf"{ESC}\[[0-9;]*m", "", s)


def fit_row(body: str) -> str:
    """把整行（含高亮底色）补满卡片宽度，让选中条不缺一截。"""
    n = max(BOX_W + 2 - dwidth(strip_ansi(body)), 0)
    return body + " " * n + RESET


def agent_row(a, num):
    def render(selected):
        mark = f"{FG(a['color'])}▸{RESET}" if selected else " "
        bg = SELBG if selected else ""
        body = (f"{bg}  {mark}{bg} {DIM}{str(num).rjust(2)}{RESET}{bg}  "
                f"{FG(a['color'])}{BOLD if selected else ''}{pad(a['name'], 18)}{RESET}{bg}"
                f"{MUTED}{pad(a['key'], 8)}{RESET}{bg}"
                f"{DIM}{pad(a['note'], max(BOX_W - 34, 8))}{RESET}{bg}")
        return fit_row(body)
    return render


def dir_row(it, num):
    def render(selected):
        mark = f"{ACCENT}▸{RESET}" if selected else " "
        bg = SELBG if selected else ""
        path_w = max(BOX_W - 39, 12)   # 余下给 前缀8 + 分支14 + 状态8 + 时间9
        branch = it.get("branch")
        if branch:
            st = f"{GREEN}✓ 干净{RESET}{bg}" if not it.get("dirty") else \
                 f"{YELLOW}● {it['dirty']} 改{RESET}{bg}"
            gitcol = f"{MUTED}{pad(branch, 14)}{RESET}{bg}" + pad_ansi(st, 8)
        else:
            gitcol = f"{DIM}{pad('—', 22)}{RESET}{bg}"
        when = ago(it["ts"]) if it.get("ts") else ""
        body = (f"{bg}  {mark}{bg} {DIM}{str(num).rjust(2)}{RESET}{bg}  "
                f"{TEXT}{BOLD if selected else ''}{pad_tail(shorten(it['path']), path_w)}{RESET}{bg} "
                f"{gitcol}{DIM}{pad(when, 9)}{RESET}{bg}")
        return fit_row(body)
    return render


def path_candidate_row(it, color):
    def render(selected):
        mark = f"{FG(color)}▸{RESET}" if selected else " "
        bg = SELBG if selected else ""
        name_w = min(26, max(18, BOX_W // 3))
        path_w = max(BOX_W - name_w - 10, 12)
        body = (f"{bg}  {mark}{bg}    "
                f"{FG(color)}{BOLD if selected else ''}{pad(it['name'] + '/', name_w)}{RESET}{bg} "
                f"{DIM}{pad_tail(shorten(it['path']), path_w)}{RESET}{bg}")
        return fit_row(body)
    return render


def pad_ansi(s: str, w: int) -> str:
    """给已带颜色码的短串补空格（按可见字符算宽）。"""
    return s + " " * max(w - dwidth(strip_ansi(s)), 0)


def status_right(agent=None):
    distro = os.environ.get("WSL_DISTRO_NAME", "linux")
    proxy_available = os.path.exists(os.path.join(HOME, ".proxy.sh"))
    if agent is None:
        proxy = "proxy-on ✓" if proxy_available else "proxy-on —"
    elif not agent.get("proxy", False):
        proxy = "直连"
    else:
        proxy = "代理 ✓" if proxy_available else "代理未配置"
    return f"{distro} · {proxy}"


# ─────────────────────────── 交互流程 ───────────────────────────

def pick_agent(term, agents):
    sel = 0
    while True:
        rows = [(True, agent_row(a, i + 1)) for i, a in enumerate(agents)]
        geom = draw("◇ AI 启动器", status_right(), rows, sel,
                    "↑↓/鼠标 选 · Enter 进 · 1-9 直达 · s 纯 Shell · q 退出")
        kind, *rest = term.key()
        if kind == "mouse":
            row, col, _ = rest
            i = hit(geom, row, col)
            if i is not None:
                return agents[i]
            continue
        k = rest[0]
        if k in ("up", "k"):
            sel = (sel - 1) % len(agents)
        elif k in ("down", "j"):
            sel = (sel + 1) % len(agents)
        elif k in ("\r", "\n", "right", "l"):
            return agents[sel]
        elif len(k) == 1 and "1" <= k <= "9" and int(k) <= len(agents):
            return agents[int(k) - 1]
        elif k == "s":
            return "SHELL"
        elif k in ("q", "\x03", "esc"):
            return None


def pick_path(term, agent, initial=""):
    """实时路径输入：展示匹配子目录，并支持键盘或鼠标完成选择。"""
    buf = initial
    sel = -1
    error = ""
    while True:
        suggestions = path_suggestions(buf)
        valid = bool(buf) and os.path.isdir(expand(buf))
        shown = pad_tail(buf, max(BOX_W - 14, 12)).rstrip() if buf else ""
        rows = [
            (False, lambda _, shown=shown:
             f"    {DIM}路径:{RESET} {TEXT}{shown}{RESET}{FG(agent['color'])}█{RESET}"),
            (False, lambda _: ""),
        ]
        if error:
            rows.append((False, lambda _, error=error: f"    {RED}{error}{RESET}"))
        elif not suggestions:
            hint = "当前目录没有子目录" if valid else "继续输入，或按 Esc 返回"
            rows.append((False, lambda _, hint=hint: f"    {DIM}{hint}{RESET}"))
        rows.extend((True, path_candidate_row(item, agent["color"]))
                    for item in suggestions)
        state = "目录 ✓" if valid else f"{len(suggestions)} 个匹配"
        title = f"{FG(agent['color'])}{agent['name']}{RESET}{DIM} › 输入目录{RESET}"
        geom = draw(title, f"{agent['key']} · {status_right(agent)} · {state}", rows, sel,
                    "↑↓ 选 · Tab/→ 下级 · Enter 确认 · Ctrl+U 清空 · Esc 返回")
        kind, *rest = term.key()
        if kind == "mouse":
            row, col, _ = rest
            i = hit(geom, row, col)
            if i is not None and i < len(suggestions):
                return suggestions[i]["path"]
            continue
        k = rest[0]
        if k == "up" and suggestions:
            sel = len(suggestions) - 1 if sel < 0 else (sel - 1) % len(suggestions)
        elif k == "down" and suggestions:
            sel = 0 if sel < 0 else (sel + 1) % len(suggestions)
        elif k in ("\t", "right") and suggestions:
            i = sel if 0 <= sel < len(suggestions) else 0
            buf = suggestions[i]["input"]
            sel, error = -1, ""
        elif k in ("\r", "\n"):
            if 0 <= sel < len(suggestions):
                return suggestions[sel]["path"]
            if valid:
                return expand(buf)
            if len(suggestions) == 1:
                return suggestions[0]["path"]
            error = "目录不存在，请继续输入或从候选中选择"
        elif k in ("esc", "\x03"):
            return None
        elif k in ("\x7f", "\b"):
            buf, sel, error = buf[:-1], -1, ""
        elif k == "\x15":
            buf, sel, error = "", -1, ""
        elif k and len(k) == 1 and k.isprintable():
            buf += k
            sel, error = -1, ""


def pick_dir(term, agent, allow_back):
    items = dir_candidates(agent)
    fill_git(items)
    sel = 0
    title = f"{FG(agent['color'])}{agent['name']}{RESET}{DIM} › 选目录{RESET}"
    right = f"{agent['key']} · {status_right(agent)}"
    hint = ("Esc 返回 · " if allow_back else "") + \
           "Enter 进 · / 从根输入 · e 浏览当前 · q 退出"
    while True:
        rows = [(True, dir_row(it, i + 1)) for i, it in enumerate(items)]
        rows.append((False, lambda _: ""))
        rows.append((False, lambda _: f"    {DIM} /{RESET}  {MUTED}输入其它路径…{RESET}"))
        geom = draw(title, right, rows, sel, hint)
        kind, *rest = term.key()
        if kind == "mouse":
            row, col, _ = rest
            i = hit(geom, row, col)
            if i is not None:
                return items[i]["path"]
            continue
        k = rest[0]
        if k in ("up", "k"):
            sel = (sel - 1) % len(items)
        elif k in ("down", "j"):
            sel = (sel + 1) % len(items)
        elif k in ("\r", "\n", "right", "l"):
            return items[sel]["path"]
        elif len(k) == 1 and "1" <= k <= "9" and int(k) <= len(items):
            return items[int(k) - 1]["path"]
        elif k in ("/", "e"):
            initial = "/" if k == "/" else shorten(items[sel]["path"]).rstrip("/") + "/"
            typed = pick_path(term, agent, initial)
            if typed:
                return typed
        elif k in ("esc", "left", "h") and allow_back:
            return None
        elif k in ("q", "\x03"):
            sys.exit(0)


# ─────────────────────────── 启动 ───────────────────────────

def build_script(agent, target):
    q = shlex.quote(target)
    lines = [
        '[ -f "$HOME/.proxy.sh" ] && source "$HOME/.proxy.sh"',
        '[ -f "$HOME/.npm-path.sh" ] && source "$HOME/.npm-path.sh"',
        'export PATH="$HOME/.local/bin:$PATH"',
    ]
    for p in agent["path_prepend"]:
        lines.append(f'export PATH="{p}:$PATH"')
    if agent["unset"]:
        lines.append("unset " + " ".join(agent["unset"]))
    for k, v in agent["env"].items():
        lines.append(f'export {k}="{v}"')
    lines.append(f"cd {q} || exit 1")
    if agent["proxy"]:
        lines.append(
            'if command -v proxy-on >/dev/null 2>&1; then proxy-on --quiet '
            '|| printf "\\033[33mproxy-on 失败，继续无代理\\033[0m\\n"; fi')
    cmd = agent["cmd"]
    lines += [
        f'if ! command -v {cmd} >/dev/null 2>&1; then',
        f'  printf "\\033[31m未找到命令 {cmd}\\033[0m\\n"; exec bash -i',
        'fi',
        f'{cmd} "$@"',
        'code=$?',
        'if [ "$code" -ne 0 ]; then',
        f'  printf "\\n\\033[31m{cmd} 退出码 $code\\033[0m  \\033[33mshell 保留在 {target}\\033[0m\\n"',
        'fi',
        'exec bash -i',
    ]
    return "\n".join(lines)


def build_shell_script(target):
    return "\n".join([
        '[ -f "$HOME/.proxy.sh" ] && source "$HOME/.proxy.sh"',
        f"cd {shlex.quote(target)} || exit 1",
        "exec bash -i",
    ])


def wt_handoff(agent, target, passthru):
    """在当前 Windows Terminal 窗口开一个新 tab，用该 agent 原来的 profile。

    Windows Terminal 的 tab 图标只能由 profile 决定，没有转义序列能在运行时
    改它。所以想让 tab 保留各家原来的 logo，只能换一个 tab 起。
    那 5 个 profile 已设为 hidden：下拉菜单里看不到，但 `wt -p` 仍能拉起。
    """
    if os.environ.get("AI_LAUNCHER_HANDOFF") != "1":
        return False                      # 不是从 AI tab 进来的，就地起
    prof = agent.get("wt_profile")
    if not prof:
        return False
    wt = shutil.which("wt.exe")
    if not wt:
        return False
    if any(c in target for c in ';"\\'):  # 这些字符会被 wt 的命令行再解析一遍
        return False
    # AI_LAUNCHER_TITLED=1：新 tab 的标题交给 profile 的 tabTitle 和 agent 自己，
    # 我们不再插一脚，行为跟合并前完全一致。
    inner = (f"AI_LAUNCHER_TITLED=1 ~/.local/bin/ai --no-handoff "
             f"{agent['key']} {shlex.quote(target)}")
    if passthru:
        inner += " -- " + " ".join(shlex.quote(x) for x in passthru)
    cmd = [wt, "-w", "0", "nt", "-p", prof,
           "wsl.exe", "-d", os.environ.get("WSL_DISTRO_NAME", "Ubuntu"),
           "-u", os.environ.get("USER", "linux"), "--cd", "~", "--",
           "bash", "-lc", inner]
    try:
        # 用 run 而不是 Popen：等 wt 真的把新 tab 建出来再退出本 tab，
        # 否则当 AI tab 是窗口里唯一一个时，窗口会先被关掉。
        r = subprocess.run(cmd, timeout=20,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            return False
        time.sleep(0.4)
        return True
    except Exception:
        return False


def launch(agent, target, passthru):
    write_history(target)
    if agent != "SHELL" and wt_handoff(agent, target, passthru):
        return                            # 新 tab 已接管，本 tab 就此退出
    if agent == "SHELL":
        sys.stdout.write(f"{ESC}]0;shell · {shorten(target)}\x07")
        sys.stdout.write(f"  {ACCENT}→{RESET} shell  {DIM}{shorten(target)}{RESET}\n")
        sys.stdout.flush()
        os.execv("/bin/bash", ["bash", "-c", build_shell_script(target)])
    if os.environ.get("AI_LAUNCHER_TITLED") != "1":
        sys.stdout.write(f"{ESC}]0;{agent['name']} · {shorten(target)}\x07")
    sys.stdout.write(
        f"  {FG(agent['color'])}{agent['name']}{RESET}"
        f"  {DIM}{shorten(target)}{RESET}\n")
    sys.stdout.flush()
    argv = ["bash", "-c", build_script(agent, target), "ai"] + passthru
    os.execv("/bin/bash", argv)


# ─────────────────────────── 入口 ───────────────────────────

def main():
    agents = load_agents()
    args = sys.argv[1:]
    passthru = []
    if "--" in args:
        i = args.index("--")
        args, passthru = args[:i], args[i + 1:]

    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args and args[0] in ("-V", "--version"):
        print(f"ai-launcher {VERSION}")
        return
    if args and args[0] == "--list":
        for a in agents:
            print(f"{a['key']:<8} {a['name']:<16} {a['cmd']}")
        return

    want_shell = False
    while args and args[0].startswith("--"):
        if args[0] == "--shell":
            want_shell = True
        elif args[0] == "--no-handoff":
            # 已经在目标 tab 里了，别再开新 tab
            os.environ.pop("AI_LAUNCHER_HANDOFF", None)
        else:
            sys.exit(f"未知参数：{args[0]}")
        args = args[1:]

    picked = None
    if args and not args[0].startswith("-"):
        match = [a for a in agents if a["key"] == args[0]]
        if match:
            picked, args = match[0], args[1:]
        elif not want_shell:
            sys.exit(f"未知 agent: {args[0]}（用 ai --list 看清单）")

    direct_dir = expand(args[0]) if args else None
    if direct_dir and not os.path.isdir(direct_dir):
        sys.exit(f"目录不存在：{direct_dir}")

    # 全部参数齐了就不进菜单
    if direct_dir and (picked or want_shell):
        launch("SHELL" if want_shell else picked, direct_dir, passthru)
        return

    if not sys.stdin.isatty():
        sys.exit("需要交互式终端；或用 `ai <agent> <目录>` 直达")

    with Term() as term:
        while True:
            agent = picked or ("SHELL" if want_shell else pick_agent(term, agents))
            if agent is None:
                return
            ref = agent if agent != "SHELL" else \
                {"name": "纯 Shell", "key": "shell",
                 "color": "#9ca3af", "default_dir": "$HOME/dev"}
            target = direct_dir or pick_dir(term, ref, allow_back=not (picked or want_shell))
            if target is None:
                continue
            break
    launch(agent, target, passthru)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
