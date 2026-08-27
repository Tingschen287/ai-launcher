# Handoff — 从 Agent Deck 延伸到 Host Deck

> 接手对象：Grok / 后续开发 Agent  
> 日期：2026-08-26  
> 当前目标：沿用 Agent Deck 的治理方式，设计并实现一个基于 Windows Terminal 的 SSH 连接管理器，暂定名 **Host Deck**。

## 1. 用户真正想解决的问题

用户目前使用 Tabby 管理 SSH，但感觉连接管理不顺手，而且在 Tabby 与 Windows Terminal 之间切换割裂。用户倾向把本地 Agent 与远程服务器工作流都收敛进 Windows Terminal。

这不是简单“再写一个 SSH 菜单”，重点是延续本次 Agent Deck 的治理原则：

> 用统一入口治理分散的原生命令，但不取代原生工具本身。

Agent Deck 负责选择和编排，Claude/Codex/Grok/Kimi 仍是原生 CLI；同理，Host Deck 应负责发现、选择、分组和启动连接，底层仍使用原生 OpenSSH。

## 2. Agent Deck 当前状态

- 产品名：**Agent Deck**
- 版本：`0.5.1`
- CLI：`ai`
- Git 仓库：`git@github.com:Tingschen287/terminal-deck.git`
- 本地仓库：`/home/linux/dev/ai/05.code/github/Tingschen287/terminal-deck`
- 当前提交：`8a1e760 chore: rename launcher to Agent Deck`
- 主程序：`src/ai_launcher.py`
- 已安装程序：`/home/linux/.local/bin/ai`
- 实际配置：`/home/linux/.config/ai-launcher/agents.toml`
- 配置样例：`config/agents.example.toml`
- Windows Terminal 示例：`integrations/windows-terminal/profiles.example.jsonc`
- 测试：`python3 -m unittest discover -s tests -v`，当前 19 项通过

Windows Terminal 当前可见 Profile：

- 名称：`Agent Deck`
- GUID：`{2aa2b4a4-7902-5cc2-b67d-cb7db394ba3f}`
- 命令：`wsl.exe -d Ubuntu-24.04 -u linux --cd ~ -- bash -lc "AI_LAUNCHER_HANDOFF=1 ~/.local/bin/ai"`
- 图标：`C:\Users\admin\.claude\icons\claude-com.ico`
- Terminal 配置文件：`C:\Users\admin\AppData\Local\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json`

不要把用户的真实 Terminal 配置、私钥、token 或机器专属配置提交进仓库。

## 3. 已形成的产品与交互偏好

- 深色、克制、居中的终端 TUI，不要花哨 ASCII Logo。
- Agent 列表主要靠各自颜色区分，不显示粗糙的小 Logo。
- 可操作区域在鼠标悬停时必须有背景色反馈。
- 同时支持方向键、数字键、Enter 和鼠标。
- 目录页标题使用可点击的 `New | Resume` Tab，默认 `New`。
- 用户喜欢 Windows Terminal 页签使用 Claude 的菊花图标。
- 页面信息应直接、紧凑，不堆叠无用状态。
- 修改后要真实启动 Windows Terminal 页签验证，不能只跑单元测试。

Agent Deck 当前原生恢复入口：

- Claude 官方 / CC-Switch：`claude --resume`
- Codex：`codex resume`
- Grok：`grok "/resume"`
- Kimi：`kimi --session`

## 4. 本次治理的关键技术原则

1. **配置驱动**：目标、命令、环境、颜色、代理等策略不散落在 UI 分支中。
2. **原生 CLI 不被替代**：启动器只编排，交互和能力仍由原生程序提供。
3. **共享交互，目标专属策略**：选择页面共用；每个 Agent/Host 的启动参数可独立配置。
4. **进程隔离**：每个 Windows Terminal 页签启动独立进程；代理和环境变量只影响该进程。
5. **机器配置与项目源码分离**：仓库只放模板；真实配置留在用户目录。
6. **可维护**：README、AGENTS.md、示例配置、安装脚本、测试、版本和 Git 提交同步更新。
7. **非破坏升级**：安装或升级时保留用户已有配置。

## 5. Host Deck 的建议定位

建议先做成 Agent Deck 的兄弟工具，而不是立即塞进同一个大程序：

- 产品名：`Host Deck`
- 建议 CLI：`host`（实现前检查是否与本机命令冲突；冲突则考虑 `hosts` 或 `hdeck`）
- Windows Terminal 可见 Profile：`Host Deck`
- 运行后选择服务器，再在同一 Windows Terminal 窗口打开连接页签
- 底层优先使用 WSL 内的 OpenSSH，复用用户现有 Linux SSH 配置和 agent

建议链路：

```text
Windows Terminal
  -> Host Deck TUI
  -> 选择分组 / 主机 / 连接方式
  -> WSL OpenSSH
  -> ~/.ssh/config
  -> 目标服务器
```

## 6. SSH 配置的来源边界

连接事实应继续以 `~/.ssh/config` 为唯一真相，包括：

- `HostName`
- `User`
- `Port`
- `IdentityFile`
- `ProxyJump`
- 其他 OpenSSH 参数

Host Deck 不要复制或重写这些字段。需要解析最终连接参数时，优先考虑调用 `ssh -G <alias>`，不要自行实现不完整的 OpenSSH 配置解析器。

Host Deck 自己的配置只保存 UI/编排元数据，例如：

- 显示名称
- 分组（生产、测试、开发、家庭等）
- 颜色
- 收藏
- 默认远程目录
- 默认连接后命令
- tmux 会话策略
- 是否在菜单隐藏

不得保存密码、私钥、token 或任何凭据。认证继续交给 SSH key、ssh-agent 或用户现有认证设施。

## 7. 建议的第一版范围

第一版优先完成：

- 从 `~/.ssh/config` 发现可连接 Host alias
- 最近连接与收藏
- 按分组显示和搜索
- 键盘、数字键、鼠标选择
- 可点击行的 hover 高亮
- 一个共享的连接页面
- `Connect | Attach` 标题 Tab：
  - `Connect`：普通新 SSH 连接
  - `Attach`：连接后进入或选择远程 tmux 会话
- 新页签标题显示服务器别名/环境
- 参数透传给原生 `ssh`
- 连接失败后保留 Shell，方便查看错误
- 安装脚本、示例配置、README、测试

第一版暂不做：

- 密码或私钥管理
- 自研 SSH 协议栈
- SFTP 文件管理器
- 端口转发的复杂图形化编排
- 自动修改大量现有 SSH 配置
- 一开始就把 Agent Deck 与 Host Deck 合成一个巨型应用

## 8. Windows Terminal 集成建议

初期只需要：

- 一个可见的 `Host Deck` Profile，用于进入服务器选择器。
- 一个隐藏的通用 SSH Profile，用于承载实际连接页签。
- 连接时动态设置页签标题；不要为每台服务器手工创建 Profile。
- 如果 Windows Terminal 的图标只能由 Profile 决定，所有 SSH 页签先共用一个 Host Deck 图标即可。

每个 SSH 页签是独立的 `ssh` 进程，因此连接、退出和局部环境互不影响；但它们仍会共享文件系统中的 `~/.ssh/config`、known_hosts，以及用户配置的 ssh-agent。

## 9. 接手后的执行顺序

1. 先完整阅读当前仓库的 `AGENTS.md`、`README.md`、`src/ai_launcher.py` 和测试，理解可复用的 TUI、鼠标、Windows Terminal handoff 与安装结构。
2. 只读盘点本机 `~/.ssh/config` 的结构、include 关系、Host alias 和现有 SSH 工具；输出中不得泄露私钥或敏感连接信息。
3. 确认 `host` CLI 是否冲突，并向用户报告建议命令名。
4. 给出 Host Deck v1 的文件结构和配置边界后直接实施；不要只停留在方案，也不要直接复制 Agent Deck 后形成两个难以同步的大文件。
5. 未经用户授权，不要创建 GitHub 远程仓库；可以先准备本地项目方案，或让用户提供新仓库 URL。
6. 实现时复用已经验证的交互模式，但把通用 TUI 与 SSH 领域逻辑清楚分开。
7. 单元测试通过后，必须真实打开 Windows Terminal 新页签验证 Profile、鼠标、连接失败提示和 tab 标题。
8. 保留 Tabby 作为迁移期兜底；在 Host Deck 覆盖常用工作流前，不要卸载或破坏 Tabby 配置。

## 10. 给 Grok 的一句话任务

> 基于 Agent Deck 已验证的“统一入口 + 配置驱动 + 原生 CLI + Windows Terminal 页签交接”模式，先只读盘点本机 SSH 配置，再设计并直接实现 Host Deck v1；以 `~/.ssh/config` 为连接真相，Host Deck 只管理选择、分组、最近记录、Connect/Attach 与 Windows Terminal 交接，不保存凭据、不破坏 Tabby，并在实施前确认 CLI 名称与新仓库位置。

## 11. 实施状态（2026-08-26）

- Host Deck v1 已作为本仓库兄弟工具落地：`src/host_deck.py`，CLI 为 `host`。
- 共享 TUI 抽到 `src/deck_tui.py`，避免两份难以同步的大文件。
- 未创建新的 GitHub 远程仓库；需要独立仓库时由用户提供 URL。
- 本机 WSL `~/.ssh/config` 当时不存在；Windows 侧 OpenSSH 与 Tabby 配置只读盘点，未改动 Tabby。
- 安装后需把示例里的 `Host Deck` / `SSH (WSL)` Profile 合并进本机 Windows Terminal。
