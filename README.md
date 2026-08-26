# AI Launcher

A keyboard-first terminal launcher for Claude Code, Codex CLI, Grok, Kimi, and other AI coding agents.

用一个 `ai` 命令选择 Agent 和工作目录，再调用本机已经安装的原生 CLI。启动器统一管理菜单、目录历史、代理、环境变量、PATH 与 Windows Terminal 页签交接，不替代各家 Agent 本身。

## 平台状态

| 平台 | 状态 |
|---|---|
| WSL 2 + Windows Terminal | 当前支持 |
| Linux terminal | 核心流程可用；无 Windows Terminal 页签交接 |
| Windows native | 规划中 |
| macOS | 规划中 |

仓库名称有意保持平台中立。当前版本不会提前声称 Windows 原生或 macOS 已适配。

## 功能

- 键盘、数字键和鼠标选择 Agent
- 最近目录、Git 分支与工作区状态
- 手动输入路径时实时展示和筛选下级目录
- TOML 驱动的 Agent 注册表
- 每个 Agent 独立的颜色、环境变量、PATH、代理策略和默认目录
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

- 安装 `src/ai_launcher.py` 到 `~/.local/bin/ai`
- 仅在配置不存在时创建 `~/.config/ai-launcher/agents.toml`
- 升级时保留已有配置

确认 `~/.local/bin` 已加入 `PATH`，然后运行：

```bash
ai --list
ai
```

## 使用

```text
ai                         选择 Agent，再选择目录
ai codex                   固定 Agent，只选择目录
ai cco ~/dev/project       直接在指定目录启动
ai kimi ~/dev/project -- --help
ai --shell                 选择目录后进入纯 Shell
ai --list                  查看 Agent 注册表
ai --version               查看版本
```

`--` 后面的参数会原样传给 Agent CLI。

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
| `color` | 菜单强调色，格式为 `#RRGGBB` |
| `note` | 菜单备注 |
| `proxy` | `true` 显式运行 `proxy-on --quiet`；`false` 清除继承的代理变量，保证当前 Agent 直连 |
| `path_prepend` | 启动前追加到 PATH 前端的目录 |
| `env` / `unset` | 启动前设置或清除的环境变量 |
| `default_dir` | Agent 专属默认目录 |
| `wt_profile` | Windows Terminal 交接使用的隐藏 Profile 名称 |

可用 `AI_LAUNCHER_CONFIG` 和 `AI_LAUNCHER_HISTORY` 覆盖默认配置及历史文件路径。

## Windows Terminal

[`integrations/windows-terminal/profiles.example.jsonc`](integrations/windows-terminal/profiles.example.jsonc) 提供一个可见的 `AI` Profile 和每个 Agent 的隐藏 Profile 示例。

1. 将示例对象合并进 Windows Terminal `settings.json` 的 `profiles.list`。
2. 把 `<DISTRO>`、`<WSL_USER>` 替换为本机值。
3. 如 GUID 与本机配置冲突，请重新生成。
4. `icon` 是可选字段，可按机器自行添加，不需要提交到仓库。

从 `AI` Profile 进入时，启动器会尝试在同一窗口创建对应 Agent 页签；没有匹配 Profile 或 `wt.exe` 时，会留在当前终端启动。

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
