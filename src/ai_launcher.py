#!/usr/bin/env python3
"""Agent Deck —— 一个入口选 agent + 选目录，然后起会话。

用法：
  ai                    交互：先选 agent，再选目录
  ai cco                指定 agent，只选目录
  ai cco ~/dev/ai       直接启动，不进菜单
  ai --resume cco DIR   在该目录打开 Resume 选择页
  ai --list             打印 agent 清单
  ai --shell            直接开 shell（选完目录）

配置：~/.config/ai-launcher/agents.toml（可用 AI_LAUNCHER_CONFIG 覆盖）
历史：~/.local/share/ai-launcher/history.tsv（可用 AI_LAUNCHER_HISTORY 覆盖）
"""

import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor

_HERE = os.path.dirname(os.path.realpath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import deck_tui as tui
from deck_tui import (
    HOME, ESC, FG, RESET, BOLD, DIM, MUTED, TEXT, ACCENT,
    GREEN, YELLOW, RED, SELBG, Term, draw, hit, hit_tab, tab_header,
    action_row, fit_row, pad, pad_tail, pad_ansi, shorten, expand, ago,
    peer_available, switch_deck,
)

VERSION = "0.5.2"
CONF = os.environ.get(
    "AI_LAUNCHER_CONFIG",
    os.path.join(HOME, ".config", "ai-launcher", "agents.toml"),
)
HIST = os.environ.get(
    "AI_LAUNCHER_HISTORY",
    os.path.join(HOME, ".local", "share", "ai-launcher", "history.tsv"),
)
# 另一个 deck：按 d 跳过去。装了才显示、才响应。
PEER_BIN = os.environ.get(
    "AI_LAUNCHER_PEER_BIN", os.path.join(HOME, ".local", "bin", "host"))
PEER_PROFILE = os.environ.get("AI_LAUNCHER_PEER_PROFILE", "Host Deck")
PEER_HANDOFF_ENV = "HOST_DECK_HANDOFF"

HIST_KEEP = 24      # 历史文件里最多留多少条
HIST_SHOW = 8       # 菜单里最多显示多少条
PATH_SHOW = 9       # 路径补全里最多显示多少个子目录


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
        a.setdefault("resume_args", [])
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", a["color"]):
            sys.exit(f"agent {a['key']} 的 color 不是 #RRGGBB：{a['color']}")
        if not isinstance(a["resume_args"], list) or \
                not all(isinstance(arg, str) and arg for arg in a["resume_args"]):
            sys.exit(f"agent {a['key']} 的 resume_args 必须是字符串数组")
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


def mode_header(agent, resume=False, hover=None):
    """渲染目录页 New/Resume tabs，并返回相对标题起点的鼠标区域。"""
    prefix_plain = f"{agent['name']} › "
    prefix = f"{FG(agent['color'])}{agent['name']}{RESET}{DIM} › {RESET}"
    labels = [("new", " New ")]
    if agent.get("resume_args"):
        labels.append(("resume", " Resume "))
    active = "resume" if resume else "new"
    return tab_header(prefix_plain, prefix, labels, active, hover, agent["color"])


def agent_row(a, num):
    def render(selected):
        mark = f"{FG(a['color'])}▸{RESET}" if selected else " "
        bg = SELBG if selected else ""
        body = (f"{bg}  {mark}{bg} {DIM}{str(num).rjust(2)}{RESET}{bg}  "
                f"{FG(a['color'])}{BOLD if selected else ''}{pad(a['name'], 18)}{RESET}{bg}"
                f"{MUTED}{pad(a['key'], 8)}{RESET}{bg}"
                f"{DIM}{pad(a['note'], max(tui.BOX_W - 34, 8))}{RESET}{bg}")
        return fit_row(body)
    return render


def dir_row(it, num):
    def render(selected):
        mark = f"{ACCENT}▸{RESET}" if selected else " "
        bg = SELBG if selected else ""
        path_w = max(tui.BOX_W - 39, 12)   # 余下给 前缀8 + 分支14 + 状态8 + 时间9
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
        name_w = min(26, max(18, tui.BOX_W // 3))
        path_w = max(tui.BOX_W - name_w - 10, 12)
        body = (f"{bg}  {mark}{bg}    "
                f"{FG(color)}{BOLD if selected else ''}{pad(it['name'] + '/', name_w)}{RESET}{bg} "
                f"{DIM}{pad_tail(shorten(it['path']), path_w)}{RESET}{bg}")
        return fit_row(body)
    return render


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
    can_switch = peer_available(PEER_BIN)
    hover = None
    last_visual = None
    geom = {"rows": {}, "left": 0, "width": 0}
    while True:
        rows = [(True, agent_row(a, i + 1)) for i, a in enumerate(agents)]
        visual_sel = hover if hover is not None else sel
        visual = (visual_sel,)
        if visual != last_visual:
            tail = " · d 切到 Host" if can_switch else ""
            geom = draw("◇ Agent Deck", status_right(), rows, visual_sel,
                        "↑↓/鼠标 选 · Enter 进 · 1-9 直达 · s 纯 Shell"
                        f"{tail} · q 退出")
            last_visual = visual
        kind, *rest = term.key()
        if kind == "mouse":
            row, col, action = rest
            i = hit(geom, row, col)
            if action == "move":
                hover = i
            elif action == "click" and i is not None:
                return agents[i]
            continue
        hover = None
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
        elif k == "d" and can_switch:
            return "SWITCH"
        elif k in ("q", "\x03", "esc"):
            return None


def pick_path(term, agent, initial=""):
    """实时路径输入：展示匹配子目录，并支持键盘或鼠标完成选择。"""
    buf = initial
    sel = -1
    hover = None
    error = ""
    while True:
        suggestions = path_suggestions(buf)
        valid = bool(buf) and os.path.isdir(expand(buf))
        shown = pad_tail(buf, max(tui.BOX_W - 14, 12)).rstrip() if buf else ""
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
        visual_sel = hover if hover is not None else sel
        geom = draw(title, f"{agent['key']} · {status_right(agent)} · {state}", rows, visual_sel,
                    "↑↓ 选 · Tab/→ 下级 · Enter 确认 · Ctrl+U 清空 · Esc 返回")
        kind, *rest = term.key()
        if kind == "mouse":
            row, col, action = rest
            i = hit(geom, row, col)
            if action == "move":
                hover = i
            elif action == "click" and i is not None and i < len(suggestions):
                return suggestions[i]["path"]
            continue
        hover = None
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
    path_sel = 0
    hover = None
    tab_hover = None
    can_resume = bool(agent.get("resume_args"))
    resume_mode = False
    right = f"{agent['key']} · {status_right(agent)}"
    hint = ("Esc 返回 · " if allow_back else "") + \
           "Tab New/Resume · Enter 选择 · / 路径 · q 退出"
    while True:
        rows = [(True, dir_row(it, i + 1)) for i, it in enumerate(items)]
        rows.append((False, lambda _: ""))
        manual_idx = len(items)
        rows.append((True, action_row(
            "/", "输入其它路径", "实时目录补全", agent["color"])))
        selectable_count = manual_idx + 1
        title, tab_regions = mode_header(agent, resume_mode, tab_hover)
        visual_sel = hover if hover is not None else sel
        geom = draw(title, right, rows, visual_sel, hint, tab_regions)
        kind, *rest = term.key()
        if kind == "mouse":
            row, col, action = rest
            tab = hit_tab(geom, row, col)
            i = hit(geom, row, col)
            if action == "move":
                tab_hover = tab
                hover = None if tab is not None else i
            elif action == "click" and tab is not None:
                resume_mode = tab == "resume"
                tab_hover = None
            elif action == "click" and i is not None:
                if i < len(items):
                    return items[i]["path"], resume_mode
                if i == manual_idx:
                    initial = shorten(items[path_sel]["path"]).rstrip("/") + "/"
                    typed = pick_path(term, agent, initial)
                    if typed:
                        return typed, resume_mode
            continue
        hover = None
        tab_hover = None
        k = rest[0]
        if k in ("up", "k"):
            sel = (sel - 1) % selectable_count
            if sel < len(items):
                path_sel = sel
        elif k in ("down", "j"):
            sel = (sel + 1) % selectable_count
            if sel < len(items):
                path_sel = sel
        elif k in ("\r", "\n", "right", "l"):
            if sel < len(items):
                return items[sel]["path"], resume_mode
            if sel == manual_idx:
                initial = shorten(items[path_sel]["path"]).rstrip("/") + "/"
                typed = pick_path(term, agent, initial)
                if typed:
                    return typed, resume_mode
        elif len(k) == 1 and "1" <= k <= "9" and int(k) <= len(items):
            return items[int(k) - 1]["path"], resume_mode
        elif k == "r" and can_resume:
            resume_mode = True
        elif k == "n":
            resume_mode = False
        elif k == "\t" and can_resume:
            resume_mode = not resume_mode
        elif k in ("/", "e"):
            initial = "/" if k == "/" else shorten(items[path_sel]["path"]).rstrip("/") + "/"
            typed = pick_path(term, agent, initial)
            if typed:
                return typed, resume_mode
        elif k in ("esc", "left", "h") and allow_back:
            return None
        elif k in ("q", "\x03"):
            sys.exit(0)


# ─────────────────────────── 启动 ───────────────────────────

def build_script(agent, target, resume=False):
    q = shlex.quote(target)
    lines = [
        # .proxy.sh 会在 source 时自动 proxy-on；先关掉自动行为，再由当前
        # agent 的 proxy 策略显式决定。环境变量只影响本进程树。
        'export WSL_PROXY_AUTO=0',
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
    else:
        lines.append(
            'unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy '
            'ALL_PROXY all_proxy NO_PROXY no_proxy')
    cmd = agent["cmd"]
    resume_args = agent.get("resume_args", []) if resume else []
    invocation = " ".join([cmd] + [shlex.quote(arg) for arg in resume_args])
    lines += [
        f'if ! command -v {cmd} >/dev/null 2>&1; then',
        f'  printf "\\033[31m未找到命令 {cmd}\\033[0m\\n"; exec bash -i',
        'fi',
        f'{invocation} "$@"',
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


def wt_handoff(agent, target, passthru, resume=False):
    """在当前 Windows Terminal 窗口开一个新 tab，用该 agent 原来的 profile。

    Windows Terminal 的 tab 图标只能由 profile 决定，没有转义序列能在运行时
    改它。所以想让 tab 保留各家原来的 logo，只能换一个 tab 起。
    那 5 个 profile 已设为 hidden：下拉菜单里看不到，但 `wt -p` 仍能拉起。
    """
    if os.environ.get("AI_LAUNCHER_HANDOFF") != "1":
        return False                      # 不是从 Agent Deck tab 进来的，就地起
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
    mode = "--resume " if resume else ""
    inner = (f"AI_LAUNCHER_TITLED=1 ~/.local/bin/ai --no-handoff {mode}"
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


def launch(agent, target, passthru, resume=False):
    write_history(target)
    if agent != "SHELL" and resume and not agent.get("resume_args"):
        sys.exit(f"{agent['name']} 未配置 resume_args")
    if agent != "SHELL" and wt_handoff(agent, target, passthru, resume):
        return                            # 新 tab 已接管，本 tab 就此退出
    if agent == "SHELL":
        sys.stdout.write(f"{ESC}]0;shell · {shorten(target)}\x07")
        sys.stdout.write(f"  {ACCENT}→{RESET} shell  {DIM}{shorten(target)}{RESET}\n")
        sys.stdout.flush()
        os.execv("/bin/bash", ["bash", "-c", build_shell_script(target)])
    if os.environ.get("AI_LAUNCHER_TITLED") != "1":
        sys.stdout.write(f"{ESC}]0;{agent['name']} · {shorten(target)}\x07")
    sys.stdout.write(
        f"  {FG(agent['color'])}{'↻ ' if resume else ''}{agent['name']}{RESET}"
        f"  {DIM}{shorten(target)}{RESET}\n")
    sys.stdout.flush()
    argv = ["bash", "-c", build_script(agent, target, resume), "ai"] + passthru
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
        print(f"Agent Deck {VERSION}")
        return
    if args and args[0] == "--list":
        for a in agents:
            resume = " ".join(a.get("resume_args", [])) or "—"
            print(f"{a['key']:<8} {a['name']:<16} {a['cmd']:<10} resume: {resume}")
        return

    want_shell = False
    want_resume = False
    while args and args[0].startswith("--"):
        if args[0] == "--shell":
            want_shell = True
        elif args[0] == "--resume":
            want_resume = True
        elif args[0] == "--no-handoff":
            # 已经在目标 tab 里了，别再开新 tab
            os.environ.pop("AI_LAUNCHER_HANDOFF", None)
        else:
            sys.exit(f"未知参数：{args[0]}")
        args = args[1:]

    if want_shell and want_resume:
        sys.exit("纯 Shell 不支持 --resume")

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
        launch("SHELL" if want_shell else picked, direct_dir, passthru, want_resume)
        return

    if not sys.stdin.isatty():
        sys.exit("需要交互式终端；或用 `ai <agent> <目录>` 直达")

    with Term() as term:
        while True:
            agent = picked or ("SHELL" if want_shell else pick_agent(term, agents))
            if agent is None:
                return
            if agent == "SWITCH":
                break               # 出了 with 才切，终端要先恢复
            ref = agent if agent != "SHELL" else \
                {"name": "纯 Shell", "key": "shell",
                 "color": "#9ca3af", "default_dir": "$HOME/dev",
                 "resume_args": []}
            if direct_dir:
                target, resume = direct_dir, want_resume
            else:
                choice = pick_dir(term, ref, allow_back=not (picked or want_shell))
                if choice is None:
                    continue
                target, resume = choice
                resume = resume or want_resume
            if target is None:
                continue
            break
    if agent == "SWITCH":
        switch_deck(PEER_BIN, PEER_PROFILE, PEER_HANDOFF_ENV,
                    os.environ.get("AI_LAUNCHER_HANDOFF") == "1")
    launch(agent, target, passthru, resume)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
