# Agent Deck

A keyboard-first command deck for Claude Code, Codex CLI, Grok, Kimi, and other AI coding agents.

用一个 `ai` 命令选择 Agent 和工作目录，再调用本机已经安装的原生 CLI。启动器统一管理菜单、目录历史、代理、环境变量、PATH 与 Windows Terminal 页签交接，不替代各家 Agent 本身。

## 平台状态

| 平台 | 状态 |
|---|---|
| WSL 2 + Windows Terminal | 当前支持 |
| Linux terminal | 核心流程可用；无 Windows Terminal 页签交接 |
| Windows native | 规划中 |
| macOS | 规划中 |

仓库名称有意保持平台中立。当前版本不会提前声称 Windows 原生或 macOS 已适配。

同仓库还提供兄弟工具 **Host Deck**（命令 `host`）：用同一套 TUI 选择 SSH 主机，再交给本机 OpenSSH。连接参数仍以 `~/.ssh/config` 为准。

## 功能

- 键盘、数字键和鼠标选择 Agent
- 可点击行提供实时鼠标悬停高亮反馈
- 最近目录、Git 分支与工作区状态
- 手动输入路径时实时展示和筛选下级目录
- TOML 驱动的 Agent 注册表
- 每个 Agent 独立的颜色、环境变量、PATH、代理策略和默认目录
- 按 Agent 各自的 CLI 语法打开原生 Resume 会话选择页
- Windows Terminal 隐藏 Profile 页签交接
- CLI 直达和参数透传
- Agent 退出后保留工作 Shell，便于查看错误与继续操作

## 要求

- Python 3.11 或更高版本（使用标准库 `tomllib`）
- Bash
- 至少安装一个要调用的 Agent CLI
- Windows Terminal 仅在需要页签交接时使用

## 安装

```bash
git clone https://github.com/Tingschen287/ai-launcher.git
cd ai-launcher
./scripts/install.sh
```

安装脚本会：

- 安装共享 TUI、Agent Deck 和 Host Deck 到 `~/.local/lib/deck/`
- 把 `~/.local/bin/ai` 和 `~/.local/bin/host` 链过去
- 仅在配置不存在时创建 `agents.toml` 与 `hosts.toml`
- 升级时保留已有配置

确认 `~/.local/bin` 已加入 `PATH`，然后运行：

```bash
ai --list
ai
host --list
host
```

## 使用

```text
ai                         选择 Agent，再选择目录
ai codex                   固定 Agent，只选择目录
ai cco ~/dev/project       直接在指定目录启动
ai --resume codex ~/dev/project
ai kimi ~/dev/project -- --help
ai --shell                 选择目录后进入纯 Shell
ai --list                  查看 Agent 注册表
ai --version               查看版本
```

`--` 后面的参数会原样传给 Agent CLI。

### 恢复会话

目录页标题栏提供 `New | Resume` 两个模式 Tab，默认选择 `New`：

- `New`：选择任意路径后启动新会话
- `Resume`：选择任意路径后打开当前 Agent 的原生 Resume 会话选择页
- `Tab`：切换模式；也可以按 `n` / `r`，或直接用鼠标点击标题栏 Tab

所选目录用于限定当前工作区，具体恢复哪个会话由 Agent 自己的 Picker 决定。

各 Agent 使用自己的续接语法，统一由 `resume_args` 配置：

| Agent | 实际命令 |
|---|---|
| Claude 官方 / CC-Switch | `claude --resume` |
| Codex | `codex resume` |
| Grok | `grok "/resume"` |
| Kimi | `kimi --session` |

### 路径补全

在目录菜单按 `/` 会从根目录开始输入，按 `e` 会从当前选中的目录开始浏览。输入过程中，下方会实时列出匹配的直接子目录：

- `↑` / `↓`：选择候选目录
- `Tab` / `→`：补全候选并进入下一级
- `Enter`：确认当前有效路径或选中的候选
- 鼠标单击：直接确认候选目录
- `Ctrl+U`：清空输入
- `Esc`：回到最近目录菜单

## 配置

默认配置位于 `~/.config/ai-launcher/agents.toml`。可从 [`config/agents.example.toml`](config/agents.example.toml) 开始修改。

```toml
default_dir = "$HOME/dev"

[[agent]]
key = "codex"
name = "Codex"
color = "#e8e6e1"
note = "OpenAI"
cmd = "codex"
resume_args = ["resume"]
proxy = true
path_prepend = ["$HOME/.npm-global/bin"]
env = {}
unset = []
wt_profile = "Codex (WSL)"
```

字段说明：

| 字段 | 含义 |
|---|---|
| `key` | CLI 短名与数字菜单标识 |
| `name` | 菜单显示名称 |
| `cmd` | 最终执行的本机原生命令 |
| `resume_args` | 打开该 Agent 原生 Resume 选择页时追加到 `cmd` 后的参数数组 |
| `color` | 菜单强调色，格式为 `#RRGGBB` |
| `note` | 菜单备注 |
| `proxy` | `true` 显式运行 `proxy-on --quiet`；`false` 清除继承的代理变量，保证当前 Agent 直连 |
| `path_prepend` | 启动前追加到 PATH 前端的目录 |
| `env` / `unset` | 启动前设置或清除的环境变量 |
| `default_dir` | Agent 专属默认目录 |
| `wt_profile` | Windows Terminal 交接使用的隐藏 Profile 名称 |

可用 `AI_LAUNCHER_CONFIG` 和 `AI_LAUNCHER_HISTORY` 覆盖默认配置及历史文件路径。

## Windows Terminal

[`integrations/windows-terminal/profiles.example.jsonc`](integrations/windows-terminal/profiles.example.jsonc) 提供可见的 `Agent Deck` / `Host Deck` Profile，以及各 Agent 与通用 `SSH (WSL)` 的隐藏 Profile 示例。

1. 将示例对象合并进 Windows Terminal `settings.json` 的 `profiles.list`。
2. 把 `<DISTRO>`、`<WSL_USER>` 替换为本机值。
3. 如 GUID 与本机配置冲突，请重新生成。
4. `icon` 是可选字段，可按机器自行添加，不需要提交到仓库。

从 `Agent Deck` Profile 进入时，启动器会尝试在同一窗口创建对应 Agent 页签；没有匹配 Profile 或 `wt.exe` 时，会留在当前终端启动。

## Host Deck

`host` 负责发现、分组、选择和启动 SSH 连接，不替代 OpenSSH。连接参数仍写在 `~/.ssh/config`。密码进系统凭据库，不进配置文件。

```text
host                       选择主机后连接
host example-dev           直接连接该 Host 别名
host --attach example-dev  连接后进入或创建 tmux
host example-dev -- -v     额外参数原样传给 ssh
host --list                查看发现的主机
host --version             查看版本
```

选择器里按 `n`，或点 `+ 新连接`，可以追加一台主机：别名、主机、用户、端口、密钥路径、显示名、分组、密码。别名会写入 `~/.ssh/config` 末尾，不改已有 Host。密码写入 Windows 凭据库（测试可用 `HOST_DECK_SECRETS_DIR`），连接时通过 `SSH_ASKPASS` 交给 `ssh`。主机密钥确认不会自动点 yes。

标题栏提供 `Connect | Attach` 两个模式 Tab，默认 `Connect`：

- `Connect`：普通 SSH 连接
- `Attach`：连接后 `tmux attach`；若配置了 `tmux_session` 则 `tmux new-session -A -s <name>`
- `Tab`：切换模式；也可以按 `c` / `a`，或直接用鼠标点击标题栏 Tab

主机列表来自 `~/.ssh/config` 的 `Host` 别名（跟随 `Include`，跳过 `*` 等通配）。需要最终连接参数时调用 `ssh -G <alias>`，不自己解析 HostName/User/Port/IdentityFile。

Host Deck 自己的配置位于 `~/.config/host-deck/hosts.toml`，只保存显示名、分组、颜色、收藏、默认远程目录、连接后命令和 tmux 会话名。可用 `HOST_DECK_CONFIG` 覆盖。安装时若该文件不存在，会写入不含主机条目的 bootstrap 配置；完整字段见 [`config/hosts.example.toml`](config/hosts.example.toml)。不要把密码写进这个文件。

从 `Host Deck` Profile 进入时，会在同一窗口用隐藏的 `SSH (WSL)` Profile 打开连接页签，并设置页签标题。连接失败后保留 Shell。选择器页签会留下，方便再连下一台。

Tabby 仍可作为迁移期兜底，安装和卸载都不会改它。

## 更新与卸载

```bash
git pull --ff-only
./scripts/install.sh
```

普通卸载会保留配置和历史：

```bash
./scripts/uninstall.sh
```

同时清除配置和历史：

```bash
./scripts/uninstall.sh --purge
```

## 开发校验

```bash
python3 -m unittest discover -s tests -v
bash -n scripts/install.sh scripts/uninstall.sh
```

## Roadmap

- 抽离终端输入、进程启动和页签集成的 platform adapters
- Windows 原生 Terminal / PowerShell 支持
- macOS Terminal / iTerm2 支持
- 可选的配置初始化向导

## License

尚未指定开源许可证。公开分发前由仓库所有者选择。
