#!/usr/bin/env python3
"""Host Deck —— 一个入口选 SSH 主机，然后交给原生 OpenSSH。

用法：
  host                    交互：选择主机后连接
  host dev-box            直接连接该 Host 别名
  host --attach dev-box   连接后进入 tmux
  host --list             打印发现的主机
  host --import-tabby     从 Tabby 导入 SSH 连接（不改 Tabby）
  host dev-box -- -v      额外参数原样传给 ssh

连接事实以 ~/.ssh/config 为准。本配置只保存分组、颜色、收藏等编排信息。
密码进系统凭据库，不进配置文件。
配置：~/.config/host-deck/hosts.toml（可用 HOST_DECK_CONFIG 覆盖）
历史：~/.local/share/host-deck/history.tsv
"""

import glob
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
import host_secrets as secrets
from deck_tui import (
    HOME, ESC, FG, RESET, BOLD, DIM, MUTED, TEXT,
    YELLOW, RED, SELBG, Term, draw, hit, hit_tab, hit_cell, tab_header,
    action_row, fit_row, pad, pad_tail, ago, dwidth,
)

VERSION = "0.4.0"
DEFAULT_COLOR = "#38bdf8"
CONF = os.environ.get(
    "HOST_DECK_CONFIG",
    os.path.join(HOME, ".config", "host-deck", "hosts.toml"),
)
HIST = os.environ.get(
    "HOST_DECK_HISTORY",
    os.path.join(HOME, ".local", "share", "host-deck", "history.tsv"),
)
FAV = os.environ.get(
    "HOST_DECK_FAVORITES",
    os.path.join(HOME, ".local", "share", "host-deck", "favorites.txt"),
)
SSH_CONFIG = os.environ.get(
    "HOST_DECK_SSH_CONFIG",
    os.path.join(HOME, ".ssh", "config"),
)
COLLAPSED = os.environ.get(
    "HOST_DECK_COLLAPSED",
    os.path.join(HOME, ".local", "share", "host-deck", "collapsed.txt"),
)
PLAY_BTN = " ▶ "
EDIT_BTN = " ✎ "
HIST_KEEP = 24
HIST_SHOW = 8
BANNED_KEYS = (
    "password", "passphrase", "identityfile", "identity_file",
    "privatekey", "private_key", "token", "secret", "credential",
)
ALIAS_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
NEW_HOST_FIELDS = (
    ("alias", "别名", False, "ssh config 的 Host"),
    ("hostname", "主机", False, "IP 或域名"),
    ("user", "用户", False, "可空"),
    ("port", "端口", False, "默认 22"),
    ("identity", "密钥", False, "私钥路径，可空"),
    ("name", "显示名", False, "默认与别名相同"),
    ("group", "分组", False, "如 prod / dev"),
    ("password", "密码", True, "进系统凭据库，可空"),
)


# ─────────────────────────── SSH 发现 ───────────────────────────

def iter_ssh_config_files(path, seen=None):
    """只跟随 Include，不解析连接参数。"""
    seen = seen if seen is not None else set()
    path = os.path.abspath(os.path.expanduser(path))
    if path in seen or not os.path.isfile(path):
        return
    seen.add(path)
    yield path
    base = os.path.dirname(path)
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            parts = raw.split(None, 1)
            if len(parts) != 2 or parts[0].lower() != "include":
                continue
            for token in shlex.split(parts[1], posix=True):
                pattern = os.path.expanduser(token)
                if not os.path.isabs(pattern):
                    pattern = os.path.join(base, pattern)
                for match in sorted(glob.glob(pattern)):
                    yield from iter_ssh_config_files(match, seen)


def discover_aliases(config_path=None):
    """读取 Host / Include，跳过通配模式，返回可连接别名。"""
    config_path = config_path or SSH_CONFIG
    if not os.path.isfile(config_path):
        return []
    aliases, seen = [], set()
    for path in iter_ssh_config_files(config_path):
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                raw = line.strip()
                if not raw or raw.startswith("#"):
                    continue
                parts = raw.split(None, 1)
                if len(parts) != 2 or parts[0].lower() != "host":
                    continue
                for token in parts[1].split():
                    if any(char in token for char in "*?!"):
                        continue
                    if token in seen:
                        continue
                    seen.add(token)
                    aliases.append(token)
    return aliases


def parse_ssh_g(text: str) -> dict:
    """解析 `ssh -G` 输出。IdentityFile 只读入、不展示。"""
    out = {}
    identity = []
    for line in text.splitlines():
        if not line:
            continue
        key, _, value = line.partition(" ")
        key = key.lower()
        if key == "identityfile":
            identity.append(value)
            continue
        if key not in out:
            out[key] = value
    if identity:
        out["_identityfile"] = identity
    return out


def ssh_g(alias: str) -> dict:
    try:
        result = subprocess.run(
            ["ssh", "-G", "--", alias],
            capture_output=True, text=True, timeout=1.5,
        )
    except Exception:
        return {}
    if result.returncode != 0 and not result.stdout:
        return {}
    return parse_ssh_g(result.stdout)


def format_summary(params: dict) -> str:
    """展示最终连接目标，不含密钥路径或凭据。"""
    if not params:
        return ""
    user = params.get("user") or ""
    hostname = params.get("hostname") or ""
    port = params.get("port") or "22"
    if not hostname:
        return ""
    target = f"{user}@{hostname}" if user else hostname
    if port != "22":
        target += f":{port}"
    jump = params.get("proxyjump") or ""
    if jump:
        target += f" via {jump.split(',')[0]}"
    return target


def fill_summaries(items):
    if os.environ.get("HOST_DECK_SKIP_SSH_G") == "1":
        return
    with ThreadPoolExecutor(max_workers=8) as pool:
        for item, summary in zip(
            items, pool.map(lambda it: format_summary(ssh_g(it["alias"])), items)
        ):
            item["summary"] = summary


# ─────────────────────────── 配置 / 历史 / 收藏 ───────────────────────────

def default_config():
    return {
        "wt_profile": "SSH (WSL)",
        "default_tmux_session": "",
        "hosts": {},
    }


def load_config():
    cfg = default_config()
    if not os.path.exists(CONF):
        return cfg
    with open(CONF, "rb") as handle:
        raw = tomllib.load(handle)
    cfg["wt_profile"] = raw.get("wt_profile") or cfg["wt_profile"]
    cfg["default_tmux_session"] = raw.get("default_tmux_session") or ""
    for index, host in enumerate(raw.get("host", []), 1):
        for banned in BANNED_KEYS:
            if banned in host:
                sys.exit(f"第 {index} 个 host 不得保存 {banned}")
        alias = host.get("alias")
        if not alias:
            sys.exit(f"第 {index} 个 host 缺少 alias")
        if alias in cfg["hosts"]:
            sys.exit(f"host alias 重复：{alias}")
        color = host.get("color") or DEFAULT_COLOR
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            sys.exit(f"host {alias} 的 color 不是 #RRGGBB：{color}")
        cfg["hosts"][alias] = {
            "alias": alias,
            "name": host.get("name") or alias,
            "group": host.get("group") or "",
            "color": color,
            "favorite": bool(host.get("favorite", False)),
            "hidden": bool(host.get("hidden", False)),
            "remote_dir": host.get("remote_dir") or "",
            "after_cmd": host.get("after_cmd") or "",
            "tmux_session": host.get("tmux_session") or "",
            "via": host.get("via") or "",
        }
    return cfg


def read_history():
    out = []
    if os.path.exists(HIST):
        with open(HIST, encoding="utf-8") as handle:
            for line in handle:
                parts = line.rstrip("\n").split("\t")
                if len(parts) == 2 and parts[1]:
                    try:
                        out.append((float(parts[0]), parts[1]))
                    except ValueError:
                        pass
    return out


def write_history(alias: str):
    rows = [(time.time(), alias)] + [row for row in read_history() if row[1] != alias]
    os.makedirs(os.path.dirname(HIST), exist_ok=True)
    tmp = HIST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        for ts, name in rows[:HIST_KEEP]:
            handle.write(f"{ts:.0f}\t{name}\n")
    os.replace(tmp, HIST)


def read_favorites(cfg=None):
    if os.path.exists(FAV):
        with open(FAV, encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.strip()]
    if cfg:
        return [alias for alias, meta in cfg["hosts"].items() if meta.get("favorite")]
    return []


def write_favorites(aliases):
    os.makedirs(os.path.dirname(FAV), exist_ok=True)
    tmp = FAV + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        for alias in aliases:
            handle.write(alias + "\n")
    os.replace(tmp, FAV)


def read_collapsed():
    if not os.path.exists(COLLAPSED):
        return set()
    with open(COLLAPSED, encoding="utf-8") as handle:
        return {line.strip() for line in handle if line.strip()}


def write_collapsed(names):
    os.makedirs(os.path.dirname(COLLAPSED), exist_ok=True)
    tmp = COLLAPSED + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        for name in sorted(names):
            handle.write(name + "\n")
    os.replace(tmp, COLLAPSED)


def toggle_collapsed(title):
    current = read_collapsed()
    if title in current:
        current.remove(title)
    else:
        current.add(title)
    write_collapsed(current)
    return current


def toggle_favorite(alias, cfg):
    current = read_favorites(cfg)
    if alias in current:
        current = [item for item in current if item != alias]
    else:
        current.append(alias)
    write_favorites(current)
    return current


def make_item(alias, meta=None, cfg=None, adhoc=False):
    meta = meta or {}
    cfg = cfg or default_config()
    session = meta.get("tmux_session") or cfg.get("default_tmux_session") or ""
    return {
        "alias": alias,
        "name": meta.get("name") or alias,
        "group": meta.get("group") or "",
        "color": meta.get("color") or DEFAULT_COLOR,
        "favorite": False,
        "remote_dir": meta.get("remote_dir") or "",
        "after_cmd": meta.get("after_cmd") or "",
        "tmux_session": session,
        "ts": None,
        "summary": "临时目标" if adhoc else "",
        "adhoc": adhoc,
        "via": meta.get("via") or "",
        "hostname": meta.get("hostname") or "",
        "user": meta.get("user") or "",
        "port": meta.get("port") or "",
    }


def build_items(cfg):
    aliases = discover_aliases()
    extra = [alias for alias in cfg["hosts"] if alias not in aliases]
    hist = {name: ts for ts, name in read_history()}
    favorites = set(read_favorites(cfg))
    items = []
    for alias in aliases + extra:
        meta = cfg["hosts"].get(alias, {})
        if meta.get("hidden"):
            continue
        item = make_item(alias, meta, cfg)
        item["favorite"] = alias in favorites
        item["ts"] = hist.get(alias)
        items.append(item)
    return items


def _toml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _ssh_config_value(value: str) -> str:
    if any(char.isspace() for char in value) or value[:1] in "\"'":
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def validate_draft(draft):
    alias = (draft.get("alias") or "").strip()
    hostname = (draft.get("hostname") or "").strip()
    user = (draft.get("user") or "").strip()
    port = (draft.get("port") or "").strip()
    identity = (draft.get("identity") or "").strip()
    name = (draft.get("name") or "").strip()
    group = (draft.get("group") or "").strip()
    password = draft.get("password") or ""
    errors = []
    if not alias:
        errors.append("请填写别名")
    elif not ALIAS_RE.fullmatch(alias):
        errors.append("别名只能用字母、数字、点、冒号、下划线和连字符")
    elif alias in discover_aliases() and alias != (draft.get("_orig_alias") or ""):
        errors.append(f"别名已存在：{alias}")
    if not hostname:
        errors.append("请填写主机")
    elif any(char.isspace() for char in hostname):
        errors.append("主机不能包含空格")
    if port:
        if not port.isdigit() or not (1 <= int(port) <= 65535):
            errors.append("端口必须是 1-65535")
    if identity and identity.startswith("-"):
        errors.append("密钥路径无效")
    cleaned = {
        "alias": alias,
        "hostname": hostname,
        "user": user,
        "port": "" if not port or port == "22" else port,
        "identity": identity,
        "name": name or alias,
        "group": group,
        "password": password,
        "color": DEFAULT_COLOR,
        "via": "windows" if os.environ.get("WSL_DISTRO_NAME") else "",
    }
    return errors, cleaned


def append_ssh_block(host, comment=""):
    path = SSH_CONFIG
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = []
    if comment:
        lines.append(comment)
    lines += [f"Host {host['alias']}", f"    HostName {_ssh_config_value(host['hostname'])}"]
    if host.get("user"):
        lines.append(f"    User {_ssh_config_value(host['user'])}")
    if host.get("port"):
        lines.append(f"    Port {host['port']}")
    if host.get("identity"):
        lines.append(f"    IdentityFile {_ssh_config_value(host['identity'])}")
    block = "\n".join(lines) + "\n"
    created = not os.path.exists(path)
    prefix = ""
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as handle:
            current = handle.read()
        if current and not current.endswith("\n"):
            prefix = "\n"
        prefix += "\n"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(prefix + block)
    if created:
        os.chmod(path, 0o600)


def replace_ssh_block(old_alias, host):
    path = SSH_CONFIG
    if not os.path.isfile(path):
        append_ssh_block(host)
        return
    with open(path, encoding="utf-8") as handle:
        lines = handle.readlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^Host\s+{re.escape(old_alias)}(?:\s|$)", line):
            start = i
            if i > 0 and lines[i - 1].startswith("# host-deck tabby:"):
                start = i - 1
            break
    if start is None:
        append_ssh_block(host)
        return
    end = start + 1
    while end < len(lines):
        if re.match(r"^Host\s+", lines[end]) or lines[end].startswith("# host-deck tabby:"):
            break
        end += 1
    comment = ""
    if lines[start].startswith("# host-deck tabby:"):
        comment = lines[start].rstrip("\n")
    new_lines = []
    if comment:
        new_lines.append(comment + "\n")
    new_lines.append(f"Host {host['alias']}\n")
    new_lines.append(f"    HostName {_ssh_config_value(host['hostname'])}\n")
    if host.get("user"):
        new_lines.append(f"    User {_ssh_config_value(host['user'])}\n")
    if host.get("port"):
        new_lines.append(f"    Port {host['port']}\n")
    if host.get("identity"):
        new_lines.append(f"    IdentityFile {_ssh_config_value(host['identity'])}\n")
    text = "".join(lines[:start] + new_lines + lines[end:])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def write_host_config(cfg):
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    lines = [
        f"wt_profile = {_toml_quote(cfg.get('wt_profile') or 'SSH (WSL)')}",
        f"default_tmux_session = {_toml_quote(cfg.get('default_tmux_session') or '')}",
        "",
    ]
    for meta in cfg.get("hosts", {}).values():
        lines += [
            "[[host]]",
            f"alias = {_toml_quote(meta['alias'])}",
            f"name = {_toml_quote(meta.get('name') or meta['alias'])}",
            f"group = {_toml_quote(meta.get('group') or '')}",
            f"color = {_toml_quote(meta.get('color') or DEFAULT_COLOR)}",
            f"via = {_toml_quote(meta.get('via') or '')}",
            "",
        ]
    tmp = CONF + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines).rstrip() + "\n")
    os.replace(tmp, CONF)


def upsert_host_meta(host):
    cfg = load_config()
    old = None
    orig = host.get("_orig_alias") or host["alias"]
    if orig != host["alias"] and orig in cfg["hosts"]:
        old = cfg["hosts"].pop(orig)
    prev = cfg["hosts"].get(host["alias"]) or old or {}
    cfg["hosts"][host["alias"]] = {
        "alias": host["alias"],
        "name": host.get("name") or host["alias"],
        "group": host.get("group") or "",
        "color": host.get("color") or prev.get("color") or DEFAULT_COLOR,
        "favorite": bool(prev.get("favorite", False)),
        "hidden": bool(prev.get("hidden", False)),
        "remote_dir": prev.get("remote_dir") or "",
        "after_cmd": prev.get("after_cmd") or "",
        "tmux_session": prev.get("tmux_session") or "",
        "via": host.get("via") or prev.get("via") or "",
    }
    write_host_config(cfg)


def append_host_meta(host):
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    cfg = load_config()
    if host["alias"] in cfg["hosts"]:
        return
    lines = [
        "",
        "[[host]]",
        f"alias = {_toml_quote(host['alias'])}",
        f"name = {_toml_quote(host['name'])}",
        f"group = {_toml_quote(host.get('group') or '')}",
        f"color = {_toml_quote(host.get('color') or DEFAULT_COLOR)}",
        f"via = {_toml_quote(host.get('via') or '')}",
        "",
    ]
    with open(CONF, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def create_host(draft):
    """追加 SSH Host 与 Host Deck 元数据。密码只进凭据库。"""
    errors, cleaned = validate_draft(draft)
    if errors:
        return None, errors
    password = cleaned.get("password") or ""
    if password:
        try:
            secrets.store_password(
                cleaned["alias"], password, user=cleaned.get("user") or "")
        except Exception as exc:
            return None, [f"密码没写进凭据库：{exc}"]
        cleaned["password"] = ""
    try:
        append_ssh_block(cleaned)
        append_host_meta(cleaned)
    except Exception as exc:
        if password:
            secrets.delete_password(cleaned["alias"])
        return None, [str(exc)]
    return make_item(cleaned["alias"], cleaned), []


def update_host(draft):
    errors, cleaned = validate_draft(draft)
    if errors:
        return None, errors
    orig = draft.get("_orig_alias") or cleaned["alias"]
    cleaned["_orig_alias"] = orig
    password = cleaned.get("password") or ""
    if password:
        try:
            secrets.store_password(
                cleaned["alias"], password, user=cleaned.get("user") or "")
        except Exception as exc:
            return None, [f"密码没写进凭据库：{exc}"]
        if orig != cleaned["alias"]:
            secrets.delete_password(orig)
        cleaned["password"] = ""
    try:
        replace_ssh_block(orig, cleaned)
        upsert_host_meta(cleaned)
    except Exception as exc:
        return None, [str(exc)]
    return make_item(cleaned["alias"], cleaned), []


def read_ssh_block_fields(alias):
    fields = {"hostname": "", "user": "", "port": "", "identity": ""}
    path = SSH_CONFIG
    if not os.path.isfile(path):
        return fields
    with open(path, encoding="utf-8") as handle:
        lines = handle.readlines()
    inside = False
    for line in lines:
        if re.match(rf"^Host\s+{re.escape(alias)}(?:\s|$)", line):
            inside = True
            continue
        if inside and re.match(r"^Host\s+", line):
            break
        if not inside or not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        key = parts[0].lower()
        value = parts[1].strip().strip('"')
        if key == "hostname":
            fields["hostname"] = value
        elif key == "user":
            fields["user"] = value
        elif key == "port":
            fields["port"] = value
        elif key == "identityfile" and not fields["identity"]:
            fields["identity"] = value
    return fields


def item_matches(item, query):
    needle = query.lower()
    hay = " ".join([
        item["alias"], item["name"], item["group"], item.get("summary") or "",
    ]).lower()
    return needle in hay


def menu_sections(items, query=""):
    """返回 [(标题或 None, [item...])]，供分组渲染。"""
    if query:
        matched = [item for item in items if item_matches(item, query)]
        return [(None, matched)] if matched else [(None, [])]
    favorites = [item for item in items if item.get("favorite")]
    used = {item["alias"] for item in favorites}
    recent, seen_recent = [], set()
    for _ts, alias in read_history():
        if alias in used or alias in seen_recent:
            continue
        match = next((item for item in items if item["alias"] == alias), None)
        if not match:
            continue
        recent.append(match)
        seen_recent.add(alias)
        if len(recent) >= HIST_SHOW:
            break
    used.update(seen_recent)
    groups = {}
    rest = []
    for item in items:
        if item["alias"] in used:
            continue
        if item["group"]:
            groups.setdefault(item["group"], []).append(item)
        else:
            rest.append(item)
    sections = []
    if favorites:
        sections.append(("收藏", favorites))
    if recent:
        sections.append(("最近", recent))
    for name in sorted(groups):
        sections.append((name, groups[name]))
    if rest:
        sections.append(("其他" if sections else None, rest))
    if not sections:
        sections.append((None, []))
    return sections


# ─────────────────────────── 渲染 ───────────────────────────

def host_row(item, num, btn_hover=None):
    def render(selected):
        mark = f"{FG(item['color'])}▸{RESET}" if selected else " "
        bg = SELBG if selected else ""
        star = f"{YELLOW}★{RESET}{bg}" if item.get("favorite") else " "
        play = PLAY_BTN
        edit = EDIT_BTN
        btn_w = dwidth(play) + dwidth(edit)
        prefix_w = 8
        name_w = max(18, min(36, tui.BOX_W // 3))
        summary_w = max(tui.BOX_W - prefix_w - name_w - btn_w - 4, 16)
        play_bg = SELBG if selected or btn_hover == "play" else ""
        edit_bg = SELBG if selected or btn_hover == "edit" else ""
        body = (
            f"{bg}  {mark}{bg} {DIM}{str(num).rjust(2)}{RESET}{bg} {star}"
            f"{FG(item['color'])}{BOLD if selected else ''}{pad(item['name'], name_w)}{RESET}{bg} "
            f"{DIM}{pad_tail(item.get('summary') or item['alias'], summary_w)}{RESET}{bg}"
            f"{play_bg}{FG('#4ade80') if (selected or btn_hover == 'play') else DIM}{play}{RESET}{bg}"
            f"{edit_bg}{TEXT if (selected or btn_hover == 'edit') else DIM}{edit}{RESET}{bg}"
        )
        line = fit_row(body)
        total = tui.BOX_W + 2
        edit_end = total - 1
        edit_start = edit_end - dwidth(edit) + 1
        play_end = edit_start - 1
        play_start = play_end - dwidth(play) + 1
        render.hotspots = [
            {"kind": "play", "start": play_start, "end": play_end},
            {"kind": "edit", "start": edit_start, "end": edit_end},
        ]
        return line
    return render


def group_row(title, count, collapsed):
    def render(selected):
        arrow = "▸" if collapsed else "▾"
        bg = SELBG if selected else ""
        label = f"{arrow} {title}"
        body = (
            f"{bg}  {TEXT}{BOLD if selected else ''}{pad(label, max(tui.BOX_W - 12, 16))}{RESET}{bg}"
            f"{DIM}{str(count).rjust(3)}{RESET}{bg}"
        )
        return fit_row(body)
    return render


def header_line(attach=False, hover=None, color=DEFAULT_COLOR):
    prefix_plain = "Host Deck › "
    prefix = f"{TEXT}{BOLD}Host Deck{RESET}{DIM} › {RESET}"
    labels = [("connect", " Connect "), ("attach", " Attach ")]
    active = "attach" if attach else "connect"
    return tab_header(prefix_plain, prefix, labels, active, hover, color)


def status_right(count):
    distro = os.environ.get("WSL_DISTRO_NAME", "linux")
    return f"{distro} · {count} 台"


def section_row(title):
    def render(_selected):
        return f"  {DIM}{title}{RESET}"
    return render


def field_row(label, value, secret=False, placeholder=""):
    def render(selected):
        mark = f"{FG(DEFAULT_COLOR)}▸{RESET}" if selected else " "
        bg = SELBG if selected else ""
        shown = ("•" * len(value)) if secret else value
        color = TEXT
        if not shown:
            shown = placeholder
            color = DIM
        cursor = f"{FG(DEFAULT_COLOR)}█{RESET}" if selected else ""
        body = (
            f"{bg}  {mark}{bg} {DIM}{pad(label, 8)}{RESET}{bg} "
            f"{color}{BOLD if selected else ''}{pad(shown, max(tui.BOX_W - 16, 12))}{RESET}{bg}"
            f"{cursor}{bg}"
        )
        return fit_row(body)
    return render


def pick_new_host(term, cfg, existing=None):
    draft = {key: "" for key, _label, _secret, _hint in NEW_HOST_FIELDS}
    draft["port"] = "22"
    if existing:
        fields = read_ssh_block_fields(existing["alias"])
        draft["alias"] = existing["alias"]
        draft["hostname"] = fields["hostname"]
        draft["user"] = fields["user"]
        draft["port"] = fields["port"] or "22"
        draft["identity"] = fields["identity"]
        draft["name"] = existing.get("name") or existing["alias"]
        draft["group"] = existing.get("group") or ""
        draft["_orig_alias"] = existing["alias"]
    sel = 0
    hover = None
    error = ""
    save_idx = len(NEW_HOST_FIELDS)
    cancel_idx = save_idx + 1
    total = cancel_idx + 1
    while True:
        rows = []
        for i, (key, label, secret, hint) in enumerate(NEW_HOST_FIELDS):
            placeholder = hint if not draft[key] else ""
            rows.append((True, field_row(label, draft[key], secret, placeholder or hint)))
        rows.append((False, lambda _s: ""))
        if error:
            rows.append((False, lambda _s, error=error: f"    {RED}{error}{RESET}"))
        rows.append((True, action_row("+", "保存", "写入 ssh config，密码进凭据库", "#4ade80")))
        rows.append((True, action_row("×", "取消", "不保存", DEFAULT_COLOR)))
        visual_sel = hover if hover is not None else sel
        mode = "编辑连接" if existing else "新连接"
        geom = draw(
            f"{TEXT}{BOLD}Host Deck{RESET}{DIM} › {mode}{RESET}",
            status_right(len(cfg.get("hosts") or {})),
            rows, visual_sel,
            "↑↓ 换项 · 输入文字 · Enter 保存/下一项 · Esc 取消",
            box_max=88,
        )
        kind, *rest = term.key()
        if kind == "mouse":
            row, col, action = rest
            index = hit(geom, row, col)
            if action == "move":
                hover = index
            elif action == "click" and index is not None:
                if index == save_idx:
                    item, errors = (update_host if existing else create_host)(draft)
                    if item is None:
                        error = "；".join(errors)
                    else:
                        return True
                elif index == cancel_idx:
                    return False
                else:
                    sel = index
            continue
        hover = None
        key = rest[0]
        if key in ("up",):
            sel = (sel - 1) % total
        elif key in ("down",):
            sel = (sel + 1) % total
        elif key in ("esc", "\x03"):
            return False
        elif key in ("\t",):
            sel = (sel + 1) % total
        elif key in ("\r", "\n"):
            if sel == save_idx:
                item, errors = (update_host if existing else create_host)(draft)
                if item is None:
                    error = "；".join(errors)
                else:
                    return True
            elif sel == cancel_idx:
                return False
            else:
                sel = min(sel + 1, save_idx)
        elif sel >= save_idx:
            continue
        elif key in ("\x7f", "\b"):
            field = NEW_HOST_FIELDS[sel][0]
            draft[field] = draft[field][:-1]
            error = ""
        elif key == "\x15":
            field = NEW_HOST_FIELDS[sel][0]
            draft[field] = ""
            error = ""
        elif key and len(key) == 1 and key.isprintable():
            field = NEW_HOST_FIELDS[sel][0]
            draft[field] += key
            error = ""


def _host_of(entry):
    return entry["item"] if isinstance(entry, dict) and entry.get("kind") == "host" else entry


def pick_host(term, items, cfg, attach=False):
    sel = 0
    hover = None
    tab_hover = None
    btn_hover = None
    query = ""
    search_mode = False
    error = ""
    color = DEFAULT_COLOR
    collapsed = read_collapsed()
    fill_summaries(items)

    while True:
        favorites = set(read_favorites(cfg))
        for item in items:
            item["favorite"] = item["alias"] in favorites
        sections = menu_sections(items, query if search_mode or query else "")
        rows = []
        selectable = []
        host_count = 0
        if search_mode:
            shown = pad_tail(query, max(tui.BOX_W - 14, 12)).rstrip() if query else ""
            rows.append((False, lambda _s, shown=shown: (
                f"    {DIM}搜索:{RESET} {TEXT}{shown}{RESET}{FG(color)}█{RESET}"
            )))
            rows.append((False, lambda _s: ""))
            if error:
                rows.append((False, lambda _s, error=error: f"    {RED}{error}{RESET}"))
        for title, group_items in sections:
            hiding = bool(title) and title in collapsed and not search_mode
            if title:
                selectable.append({"kind": "group", "title": title})
                rows.append((True, group_row(title, len(group_items), hiding)))
            if hiding:
                continue
            for item in group_items:
                host_count += 1
                selectable.append({"kind": "host", "item": item, "num": host_count})
                hover_btn = btn_hover[1] if btn_hover and btn_hover[0] == len(selectable) - 1 else None
                rows.append((True, host_row(item, host_count, hover_btn)))
        if host_count == 0 and not search_mode:
            rows.append((False, lambda _s: f"    {DIM}没有发现 SSH Host 别名{RESET}"))
            rows.append((False, lambda _s: (
                f"    {DIM}按 n 添加连接，或按 / 输入目标{RESET}"
            )))
            rows.append((False, lambda _s: ""))
        search_idx = len(selectable)
        add_idx = search_idx + 1
        rows.append((True, action_row(
            "/", "搜索 / 输入目标", "别名、分组，或 user@host", color)))
        rows.append((True, action_row(
            "+", "新连接", "写入 ssh config，可记密码", color)))
        import_idx = add_idx + 1
        rows.append((True, action_row(
            "↓", "从 Tabby 导入", "只读 Tabby，追加到 ssh config", color)))
        if sel >= import_idx + 1:
            sel = 0
        title, tab_regions = header_line(attach, tab_hover, color)
        visual_sel = hover if hover is not None else sel
        hint = ("输入筛选 · Enter 连接 · Esc 退出搜索" if search_mode else
                "↑↓ 选 · Enter/▶ 连接 · ✎/e 编辑 · 分组回车折叠 · n 新建 · / 搜索 · q 退出")
        geom = draw(title, status_right(len(items)), rows, visual_sel, hint, tab_regions,
                    box_max=88)
        kind, *rest = term.key()
        if kind == "mouse":
            row, col, action = rest
            tab = hit_tab(geom, row, col)
            cell = hit_cell(geom, row, col)
            index = hit(geom, row, col)
            if action == "move":
                tab_hover = tab
                hover = None if tab is not None else index
                btn_hover = (cell["index"], cell["kind"]) if cell and cell["kind"] in ("play", "edit") else None
            elif action == "click" and tab is not None:
                attach = tab == "attach"
                tab_hover = None
            elif action == "click" and cell and cell["kind"] == "play":
                entry = selectable[cell["index"]]
                if entry.get("kind") == "host":
                    return entry["item"], attach
            elif action == "click" and cell and cell["kind"] == "edit":
                entry = selectable[cell["index"]]
                if entry.get("kind") == "host":
                    if pick_new_host(term, cfg, existing=entry["item"]):
                        return "reload", attach
            elif action == "click" and index is not None:
                if index < len(selectable):
                    entry = selectable[index]
                    if entry.get("kind") == "group":
                        collapsed = toggle_collapsed(entry["title"])
                    else:
                        return entry["item"], attach
                elif index == search_idx:
                    search_mode, error = True, ""
                elif index == add_idx:
                    if pick_new_host(term, cfg):
                        return "reload", attach
                elif index == import_idx:
                    return "import-tabby", attach
            continue
        hover = None
        tab_hover = None
        btn_hover = None
        key = rest[0]
        if search_mode:
            hosts = [e["item"] for e in selectable if e.get("kind") == "host"]
            if key == "up" and hosts:
                sel = len(selectable) - 1 if sel < 0 or sel >= len(selectable) else (
                    sel - 1) % len(selectable)
            elif key == "down" and hosts:
                sel = 0 if sel < 0 or sel >= len(selectable) else (sel + 1) % len(selectable)
            elif key in ("\r", "\n"):
                if 0 <= sel < len(selectable) and selectable[sel].get("kind") == "host":
                    return selectable[sel]["item"], attach
                if query.strip():
                    return make_item(query.strip(), adhoc=True, cfg=cfg), attach
                error = "输入 Host 别名或 user@host"
            elif key in ("esc", "\x03"):
                search_mode, query, error, sel = False, "", "", 0
            elif key in ("\x7f", "\b"):
                query, error, sel = query[:-1], "", 0
            elif key == "\x15":
                query, error, sel = "", "", 0
            elif key and len(key) == 1 and key.isprintable():
                query += key
                error, sel = "", 0
            continue
        if key in ("up", "k"):
            sel = (sel - 1) % (import_idx + 1)
        elif key in ("down", "j"):
            sel = (sel + 1) % (import_idx + 1)
        elif key in ("\r", "\n", "right", "l"):
            if sel < len(selectable):
                entry = selectable[sel]
                if entry.get("kind") == "group":
                    collapsed = toggle_collapsed(entry["title"])
                else:
                    return entry["item"], attach
            elif sel == search_idx:
                search_mode, error = True, ""
            elif sel == add_idx:
                if pick_new_host(term, cfg):
                    return "reload", attach
            elif sel == import_idx:
                return "import-tabby", attach
        elif len(key) == 1 and "1" <= key <= "9":
            hosts = [e["item"] for e in selectable if e.get("kind") == "host"]
            if int(key) <= len(hosts):
                return hosts[int(key) - 1], attach
        elif key == "a":
            attach = True
        elif key == "c":
            attach = False
        elif key == "\t":
            attach = not attach
        elif key == "f" and sel < len(selectable) and selectable[sel].get("kind") == "host":
            toggle_favorite(selectable[sel]["item"]["alias"], cfg)
        elif key == "/":
            search_mode, error = True, ""
        elif key == "e" and sel < len(selectable) and selectable[sel].get("kind") == "host":
            if pick_new_host(term, cfg, existing=selectable[sel]["item"]):
                return "reload", attach
        elif key == "n":
            if pick_new_host(term, cfg):
                return "reload", attach
        elif key == "i":
            return "import-tabby", attach
        elif key in ("q", "\x03", "esc"):
            return None


# ─────────────────────────── 启动 ───────────────────────────

def tab_title(item, attach=False):
    name = item.get("name") or item["alias"]
    group = item.get("group") or ""
    prefix = "tmux " if attach else ""
    if group and group != name:
        return f"{prefix}{name} · {group}"
    return f"{prefix}{name}"


def build_remote_command(item, attach=False):
    chunks = []
    if item.get("remote_dir"):
        chunks.append("cd " + shlex.quote(item["remote_dir"]))
    if attach:
        session = item.get("tmux_session") or ""
        if session:
            chunks.append("tmux new-session -A -s " + shlex.quote(session))
        else:
            chunks.append("tmux attach-session || tmux new-session")
    else:
        if item.get("after_cmd"):
            chunks.append(item["after_cmd"])
        if item.get("remote_dir") or item.get("after_cmd"):
            chunks.append("exec bash -il")
    if not chunks:
        return None
    return " && ".join(chunks)


def windows_ssh_exe():
    for path in (
        shutil.which("ssh.exe"),
        "/mnt/c/Windows/System32/OpenSSH/ssh.exe",
    ):
        if path and os.path.isfile(path):
            return path
    return None


def wsl_to_windows_path(path: str) -> str:
    raw = (path or "").replace("\\", "/")
    match = re.match(r"^/mnt/([a-zA-Z])/(.*)$", raw)
    if match:
        return match.group(1).upper() + ":\\" + match.group(2).replace("/", "\\")
    return path


def _win_to_wsl(win_path: str) -> str:
    raw = win_path.replace("\\", "/")
    match = re.match(r"^([A-Za-z]):/(.*)$", raw)
    if match:
        return f"/mnt/{match.group(1).lower()}/{match.group(2)}"
    return win_path


def windows_python():
    for path in (
        shutil.which("python.exe"),
        *glob.glob("/mnt/c/Users/*/AppData/Local/Programs/Python/Python*/python.exe"),
    ):
        if path and os.path.isfile(path):
            return path
    return None


def ensure_windows_file(name: str):
    """把辅助脚本拷到 %LOCALAPPDATA%\\HostDeck。"""
    win_root, wsl_root = "", ""
    try:
        result = subprocess.run(
            ["cmd.exe", "/c", "echo %LOCALAPPDATA%"],
            capture_output=True, text=True, timeout=5,
        )
        win_root = (result.stdout or "").strip().splitlines()[-1].strip()
    except Exception:
        win_root = ""
    if win_root and ":\\" in win_root:
        wsl_root = _win_to_wsl(win_root)
    else:
        for path in glob.glob("/mnt/c/Users/*/AppData/Local"):
            uname = path.split("/")[4]
            if uname not in ("Public", "Default", "Default User", "All Users"):
                wsl_root = path
                win_root = f"C:\\Users\\{uname}\\AppData\\Local"
                break
    if not wsl_root:
        return ""
    dest_dir = os.path.join(wsl_root, "HostDeck")
    os.makedirs(dest_dir, exist_ok=True)
    src = os.path.join(_HERE, name)
    if os.path.isfile(src):
        shutil.copy2(src, os.path.join(dest_dir, name))
    return win_root.rstrip("\\") + "\\HostDeck\\" + name


def ensure_windows_proxy():
    path = ensure_windows_file("winproxy.py")
    return path.replace("\\", "/") if path else ""


def ensure_windows_askpass():
    ensure_windows_file("host_askpass.ps1")
    return ensure_windows_file("host_askpass.cmd")


def use_windows_net(item):
    if os.environ.get("HOST_DECK_FORCE_WSL") == "1":
        return False
    via = (item.get("via") or "").lower()
    if via == "wsl":
        return False
    if not (windows_python() or windows_ssh_exe()):
        return False
    if via == "windows":
        return True
    return bool(os.environ.get("WSL_DISTRO_NAME"))


def use_windows_ssh(item):
    """兼容旧名：是否走 Windows 网络。"""
    return use_windows_net(item)


def connection_target(item):
    params = {} if os.environ.get("HOST_DECK_SKIP_SSH_G") == "1" else ssh_g(item["alias"])
    hostname = params.get("hostname") or item.get("hostname") or item["alias"]
    user = params.get("user") or item.get("user") or ""
    port = params.get("port") or item.get("port") or "22"
    return hostname, user, str(port)


def build_ssh_argv(item, passthru, attach=False):
    remote = build_remote_command(item, attach)
    proxy_py = windows_python() if use_windows_net(item) else None
    proxy_script = ensure_windows_proxy() if proxy_py else ""
    argv = ["ssh", "-o", "ConnectTimeout=15"]
    if proxy_py and proxy_script:
        proxy = f"{shlex.quote(proxy_py)} {shlex.quote(proxy_script)} %h %p"
        argv += ["-o", f"ProxyCommand={proxy}"]
        if secrets.has_password(item["alias"]):
            argv += [
                "-o", "PreferredAuthentications=password",
                "-o", "PubkeyAuthentication=no",
            ]
        if remote:
            argv.append("-t")
        argv.extend(passthru)
        argv.append("--")
        argv.append(item["alias"])
        if remote:
            argv.extend(["--", remote])
        return argv
    if use_windows_net(item) and windows_ssh_exe():
        hostname, user, port = connection_target(item)
        argv = [
            windows_ssh_exe(),
            "-o", "ConnectTimeout=15",
            "-o", "StrictHostKeyChecking=accept-new",
        ]
        if port and port != "22":
            argv += ["-p", port]
        if remote:
            argv.append("-t")
        argv.extend(passthru)
        argv.append(f"{user}@{hostname}" if user else hostname)
        if remote:
            argv.append(remote)
        return argv
    argv.extend(passthru)
    if remote:
        argv.append("-t")
    argv.append("--")
    argv.append(item["alias"])
    if remote:
        argv.extend(["--", remote])
    return argv


def askpass_path():
    return os.path.realpath(__file__)


def build_script(item, passthru, attach=False, use_askpass=None):
    argv = build_ssh_argv(item, passthru, attach)
    quoted = " ".join(shlex.quote(part) for part in argv)
    title = tab_title(item, attach).replace("\x1b", "").replace("\x07", "")
    alias = item["alias"]
    windows = use_windows_net(item)
    proxy = bool(windows and windows_python() and ensure_windows_proxy())
    if use_askpass is None:
        use_askpass = secrets.has_password(alias)
    road = "Windows 网络" if windows else "WSL OpenSSH"
    lines = [
        f"printf '\\033]0;{title}\\007'",
        f"printf '正在连接 {alias}（{road}）…\\n'",
        'if ! command -v ssh >/dev/null 2>&1 && ! command -v ssh.exe >/dev/null 2>&1; then',
        '  printf "\\033[31m未找到 ssh / ssh.exe\\033[0m\\n"; exec bash -i',
        'fi',
    ]
    linux_askpass = use_askpass and (proxy or not windows)
    if linux_askpass:
        helper = shlex.quote(askpass_path())
        lines += [
            f"export HOST_DECK_ASKPASS=1",
            f"export HOST_DECK_ASKPASS_ALIAS={shlex.quote(alias)}",
            f"export SSH_ASKPASS={helper}",
            "export SSH_ASKPASS_REQUIRE=force",
            'export DISPLAY="${DISPLAY:-:0}"',
        ]
        if os.environ.get("HOST_DECK_SECRETS_DIR"):
            lines.append(
                "export HOST_DECK_SECRETS_DIR="
                + shlex.quote(os.environ["HOST_DECK_SECRETS_DIR"])
            )
    lines += [
        quoted,
        "code=$?",
        'if [ "$code" -ne 0 ]; then',
        f'  printf "\\n\\033[31mssh 退出码 $code\\033[0m  '
        f'\\033[33mshell 已保留，目标 {alias}\\033[0m\\n"',
        f'  printf "\\033[33m连不上时：会走 Windows 网络代理；密码来自系统凭据库。\\033[0m\\n"',
        "fi",
        "exec bash -i",
    ]
    return "\n".join(lines)


def wt_handoff(item, passthru, attach=False, profile=""):
    if os.environ.get("HOST_DECK_HANDOFF") != "1":
        return False
    profile = profile or "SSH (WSL)"
    wt = shutil.which("wt.exe")
    if not wt:
        return False
    alias = item["alias"]
    if any(char in alias for char in ';"\\'):
        return False
    mode = "--attach " if attach else ""
    inner = (
        f"HOST_DECK_TITLED=1 ~/.local/bin/host --no-handoff {mode}"
        f"{shlex.quote(alias)}"
    )
    if passthru:
        inner += " -- " + " ".join(shlex.quote(part) for part in passthru)
    title = tab_title(item, attach)
    distro = os.environ.get("WSL_DISTRO_NAME", "Ubuntu")
    user = os.environ.get("USER", "linux")
    cmd = [
        wt, "-w", "0", "nt", "-p", profile, "--title", title,
        "wsl.exe", "-d", distro, "-u", user, "--cd", "~", "--",
        "bash", "-lc", inner,
    ]
    try:
        result = subprocess.run(
            cmd, timeout=20,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            return False
        time.sleep(0.4)
        return True
    except Exception:
        return False


def launch(item, passthru, attach=False, cfg=None):
    write_history(item["alias"])
    profile = (cfg or default_config()).get("wt_profile") or "SSH (WSL)"
    if wt_handoff(item, passthru, attach, profile):
        return "handoff"
    title = tab_title(item, attach)
    if os.environ.get("HOST_DECK_TITLED") != "1":
        sys.stdout.write(f"{ESC}]0;{title}\x07")
    color = item.get("color") or DEFAULT_COLOR
    sys.stdout.write(
        f"  {FG(color)}{'↻ ' if attach else ''}{item['name']}{RESET}"
        f"  {DIM}{item['alias']}{RESET}\n")
    sys.stdout.flush()
    script = build_script(item, passthru, attach)
    os.execv("/bin/bash", ["bash", "-c", script, "host"])


def find_item(cfg, alias):
    items = build_items(cfg)
    for item in items:
        if item["alias"] == alias:
            return item
    meta = cfg["hosts"].get(alias)
    adhoc = alias not in discover_aliases() and not meta
    return make_item(alias, meta, cfg, adhoc=adhoc)


def print_list(cfg):
    items = build_items(cfg)
    fill_summaries(items)
    if not items:
        print("没有发现 SSH Host 别名。运行 `host` 后按 n 添加，或编辑 ~/.ssh/config。")
        return
    for item in items:
        star = "*" if item.get("favorite") else " "
        summary = item.get("summary") or ""
        print(f"{star} {item['alias']:<16} {item['name']:<16} "
              f"{item['group']:<10} {summary}")


# ─────────────────────────── 入口 ───────────────────────────

def main():
    if os.environ.get("HOST_DECK_ASKPASS") == "1":
        sys.exit(secrets.run_askpass())

    args = sys.argv[1:]
    passthru = []
    if "--" in args:
        index = args.index("--")
        args, passthru = args[:index], args[index + 1:]

    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args and args[0] in ("-V", "--version"):
        print(f"Host Deck {VERSION}")
        return

    cfg = load_config()

    if args and args[0] == "--list":
        print_list(cfg)
        return
    if args and args[0] == "--import-tabby":
        import tabby_import
        path = args[1] if len(args) > 1 else None
        try:
            stats = tabby_import.import_from_tabby(path)
        except Exception as exc:
            sys.exit(str(exc))
        tabby_import.print_stats(stats)
        return

    want_attach = False
    while args and args[0].startswith("--"):
        if args[0] == "--attach":
            want_attach = True
        elif args[0] == "--no-handoff":
            os.environ.pop("HOST_DECK_HANDOFF", None)
        else:
            sys.exit(f"未知参数：{args[0]}")
        args = args[1:]

    direct = args[0] if args and not args[0].startswith("-") else None
    extra = args[1:]
    if extra:
        sys.exit(f"多余参数：{' '.join(extra)}（ssh 参数请放在 -- 后面）")

    if direct:
        launch(find_item(cfg, direct), passthru, want_attach, cfg)
        return

    if not sys.stdin.isatty():
        sys.exit("需要交互式终端；或用 `host <别名>` 直达")

    items = build_items(cfg)
    with Term() as term:
        while True:
            choice = pick_host(term, items, cfg, attach=want_attach)
            if choice is None:
                return
            item, attach = choice
            if item == "reload":
                cfg = load_config()
                items = build_items(cfg)
                want_attach = attach
                continue
            if item == "import-tabby":
                import tabby_import
                try:
                    tabby_import.import_from_tabby()
                except Exception:
                    pass
                cfg = load_config()
                items = build_items(cfg)
                want_attach = attach
                continue
            result = launch(item, passthru, attach, cfg)
            if result != "handoff":
                return
            items = build_items(cfg)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
