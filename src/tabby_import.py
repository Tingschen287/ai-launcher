"""Import SSH profiles from Tabby config.yaml into Host Deck.

Does not modify Tabby. Passwords are copied from the OS credential store
into Host Deck's own credential targets, never into files.
"""

import glob
import os
import re

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

import host_deck as deck
import host_secrets as secrets

GROUP_COLORS = (
    "#38bdf8", "#fbbf24", "#f87171", "#4ade80", "#a78bfa", "#fb7185",
)


def default_tabby_config():
    override = os.environ.get("HOST_DECK_TABBY_CONFIG")
    if override:
        return os.path.expanduser(override)
    matches = []
    for pattern in (
        "/mnt/c/Users/*/AppData/Roaming/tabby/config.yaml",
        os.path.expanduser("~/.config/tabby/config.yaml"),
    ):
        for path in glob.glob(pattern):
            name = path.replace("\\", "/").split("/")
            if any(part in ("Public", "Default", "All Users") for part in name):
                continue
            if os.path.isfile(path):
                matches.append(path)
    matches.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return matches[0] if matches else None


def windows_path_to_wsl(path: str) -> str:
    raw = (path or "").strip()
    if raw.startswith("file://"):
        raw = raw[7:]
    raw = re.sub(r"[\\/]+", "/", raw)
    match = re.match(r"^([A-Za-z]):/(.*)$", raw)
    if match:
        return f"/mnt/{match.group(1).lower()}/{match.group(2)}"
    return os.path.expanduser(raw)


def color_for(group: str) -> str:
    if not group:
        return deck.DEFAULT_COLOR
    return GROUP_COLORS[sum(map(ord, group)) % len(GROUP_COLORS)]


def load_tabby(path: str):
    if yaml is None:
        raise RuntimeError("需要 PyYAML：sudo apt install python3-yaml")
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    groups = {
        item.get("id"): item.get("name") or ""
        for item in (data.get("groups") or [])
        if isinstance(item, dict) and item.get("id")
    }
    profiles = []
    for item in data.get("profiles") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in (None, "ssh"):
            continue
        options = item.get("options") or {}
        host = str(options.get("host") or "").strip()
        if not host:
            continue
        group_id = item.get("group") or ""
        keys = []
        for key in options.get("privateKeys") or []:
            if isinstance(key, str) and key.strip():
                keys.append(windows_path_to_wsl(key.strip()))
            elif isinstance(key, dict) and key.get("path"):
                keys.append(windows_path_to_wsl(str(key["path"])))
        port = options.get("port")
        port = "" if port in (None, "", 22, "22") else str(port)
        profiles.append({
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or host),
            "group": groups.get(group_id, "") if group_id else "",
            "hostname": host,
            "user": str(options.get("user") or "").strip(),
            "port": port,
            "auth": options.get("auth") or "",
            "identity": keys[0] if keys else "",
        })
    return profiles


def make_alias(name, hostname, user, used):
    candidates = []
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]+", "-", name or "").strip("-._")
    if cleaned and deck.ALIAS_RE.fullmatch(cleaned):
        candidates.append(cleaned)
    host_clean = re.sub(r"[^A-Za-z0-9_.:-]+", "-", hostname or "").strip("-._")
    if host_clean and deck.ALIAS_RE.fullmatch(host_clean):
        candidates.append(host_clean)
        if user and re.fullmatch(r"[A-Za-z0-9_.-]+", user):
            candidates.append(f"{user}-{host_clean}")
    base = candidates[0] if candidates else "tabby-host"
    alias = base
    index = 2
    while alias in used:
        alias = f"{base}-{index}"
        index += 1
    used.add(alias)
    return alias


def imported_tabby_ids(path):
    found = set()
    if not os.path.isfile(path):
        return found
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            match = re.match(r"^# host-deck tabby:\s*(\S+)", line.strip())
            if match:
                found.add(match.group(1))
    return found


def tabby_password_targets(hostname, port, user):
    port = port or "22"
    targets = []
    if user:
        targets.append(f"ssh@{hostname}:{port}/{user}")
        targets.append(f"ssh@{hostname}/{user}")
    targets.append(f"ssh@{hostname}:{port}")
    targets.append(f"ssh@{hostname}")
    return targets


def lookup_tabby_password(hostname, port, user, getter=None):
    getter = getter or secrets.get_windows_secret
    for target in tabby_password_targets(hostname, port, user):
        secret = getter(target)
        if secret:
            return secret
    return None


def import_from_tabby(path=None, copy_passwords=True, getter=None):
    path = path or default_tabby_config()
    if not path or not os.path.isfile(path):
        raise FileNotFoundError("找不到 Tabby 配置 config.yaml")
    profiles = load_tabby(path)
    used = set(deck.discover_aliases())
    seen_ids = imported_tabby_ids(deck.SSH_CONFIG)
    stats = {
        "source": path,
        "found": len(profiles),
        "imported": 0,
        "skipped": 0,
        "passwords": 0,
        "password_missing": 0,
        "names": [],
    }
    for profile in profiles:
        if profile["id"] and profile["id"] in seen_ids:
            stats["skipped"] += 1
            continue
        alias = make_alias(profile["name"], profile["hostname"], profile["user"], used)
        host = {
            "alias": alias,
            "hostname": profile["hostname"],
            "user": profile["user"],
            "port": profile["port"],
            "identity": profile["identity"],
            "name": profile["name"],
            "group": profile["group"],
            "color": color_for(profile["group"]),
            "via": "windows",
        }
        comment = f"# host-deck tabby: {profile['id']}" if profile["id"] else "# host-deck tabby"
        deck.append_ssh_block(host, comment=comment)
        deck.append_host_meta(host)
        if copy_passwords:
            secret = lookup_tabby_password(
                profile["hostname"], profile["port"], profile["user"], getter=getter)
            if secret:
                secrets.store_password(alias, secret, user=profile["user"])
                stats["passwords"] += 1
            elif profile["auth"] == "password":
                stats["password_missing"] += 1
        stats["imported"] += 1
        stats["names"].append((profile["name"], alias, profile["group"]))
        if profile["id"]:
            seen_ids.add(profile["id"])
    return stats


def print_stats(stats):
    print(f"Tabby 配置：已读取 {stats['found']} 条 SSH")
    print(f"新导入 {stats['imported']} 台，跳过 {stats['skipped']} 台（已经导过）")
    print(f"密码从凭据库复制 {stats['passwords']} 个；"
          f"{stats['password_missing']} 个密码登录没找到凭据，连接时会再问")
    for name, alias, group in stats["names"]:
        extra = f"  [{group}]" if group else ""
        print(f"  {name}{extra}  ->  {alias}")
    print("Tabby 本身没有改。")
