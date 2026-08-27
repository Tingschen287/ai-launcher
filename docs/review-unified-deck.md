# 评审稿：Agent Deck 与 Host Deck 要不要合成一个 TUI

请评审「两个独立入口」vs「一个 TUI、两个 Tab」。不要帮我改代码。先给结论，再给理由。如果你不同意作者倾向，直接反对。

---

## 1. 背景（事实）

仓库：`https://github.com/Tingschen287/terminal-deck`  
运行时：WSL 2 + Windows Terminal。Python 3.11+，无第三方依赖。

两个产品已经在同一仓库、共用 `src/deck_tui.py`：

| | Agent Deck | Host Deck |
|---|---|---|
| 命令 | `ai` | `host` |
| 主程序 | `src/ai_launcher.py` | `src/host_deck.py` |
| 用户动作 | 选 Agent → 选目录 → 起原生 CLI | 选主机 → 起原生 OpenSSH |
| 配置 | `~/.config/ai-launcher/agents.toml` | `~/.config/host-deck/hosts.toml` |
| 连接真相 | Agent 注册表（cmd/env/proxy） | `~/.ssh/config`（HostName/User/Port/Key） |
| 凭据 | 不存；各 CLI 自己管 | Windows 凭据库；不进文件、不进 Git |
| WT | 可见 Profile `Agent Deck` + 各 Agent 隐藏 Profile | 可见 Profile `Host Deck` + 隐藏 `SSH (WSL)` |

治理原则（已验证，不要轻易推翻）：

1. 统一入口编排分散的原生命令，不替代原生工具。
2. 配置驱动；机器配置与仓库分离。
3. 共享交互，目标专属策略。
4. 每个 WT 页签独立进程。
5. 非破坏升级：安装/改路径时必须搬走现有配置，不能覆盖成空样例。

用户痛点不是「Tabby 不好用」，是懒得在 Windows Terminal 和 Tabby 之间切。Host Deck 已经把 SSH 收回 Terminal。现在的问题是：`ai` 和 `host` 还是两个入口，会不会又变成同一种切换成本。

---

## 2. 待评方案

### 方案 A：一个 TUI，顶部 Tab（作者倾向）

```text
Terminal Deck  ›  Agent | Host
```

- 点 Agent：现有 `ai` 流程（选 Agent → 选目录 → New/Resume）。
- 点 Host：现有 `host` 流程（分组折叠、▶ 连接、✎ 编辑、Connect/Attach）。
- 记住上次停在哪一边。
- `ai` / `host` 命令保留，进去直接落到对应 Tab。
- WT 可以只留一个可见 Profile，也可以两个都留。
- 配置收到一套目录，并迁移现有文件：

```text
~/.config/terminal-deck/agents.toml   ← 从 ~/.config/ai-launcher/agents.toml 搬
~/.config/terminal-deck/hosts.toml    ← 从 ~/.config/host-deck/hosts.toml 搬
```

列表不混排。Agent 要选目录，Host 要选机器，混成一张表会乱。

### 方案 B：继续两个界面，只整理名字

- `ai` 和 `host` 仍是两套 TUI。
- 配置目录改成语义化名字，例如：
  - `~/.config/agent-deck/`
  - `~/.config/host-deck/`（已存在）
- 同样要做一次非破坏迁移。
- WT 仍是两个可见 Profile。

---

## 3. 作者为什么倾向 A

1. 用户已经用 Tab 习惯了（`New | Resume`、`Connect | Attach`）。再加一层 `Agent | Host` 认知成本低。
2. 共用 TUI 已经抽出来了，合成一个进程主要是壳，不是重写领域逻辑。
3. 切换成本会从「另开一个 Profile / 另敲一个命令」变成「点一下 Tab」。
4. 命令和 Profile 可以当快捷方式，不是必须删掉。

---

## 4. 请重点攻击这些点

请逐条表态：同意 / 反对 / 要改，并说明为什么。

1. **切换成本是否真实？**  
   WT 里两个 Profile 已经是两个页签，和 Tabby↔Terminal 的窗口切换不是一回事。会不会过度设计？

2. **一层 Tab 套一层 Tab。**  
   顶栏会变成 `Agent | Host`，进 Agent 还有 `New | Resume`，进 Host 还有 `Connect | Attach`。会不会挤、会不会点错？有没有更好的信息架构？

3. **一个进程 vs 两个进程。**  
   现在 `ai` 和 `host` 故障隔离。合成后，Host 的 SSH 代理/凭据逻辑和 Agent 的 proxy/env 在同一二进制。崩溃、误操作、环境变量泄漏风险有多大？页签交接仍然是新进程的话，这个风险是否被高估？

4. **配置目录。**  
   `~/.config/terminal-deck/` 一套，还是 `agent-deck` + `host-deck` 两套？请比较：迁移成本、卸载、和环境变量覆盖（`AI_LAUNCHER_CONFIG` 已有用户）。

5. **默认落地页。**  
   「记住上次」会不会让用户进错上下文（想开 Claude 却落到 SSH 列表）？默认 Agent / 默认 Host / 记住上次，哪个更安全？

6. **WT Profile 数量。**  
   一个可见 Profile 还是两个？隐藏 SSH Profile、各 Agent 隐藏 Profile 还要不要？图标目前只能跟 Profile 走。

7. **不做的边界。**  
   不要把 Agent 和 Host 合成一张列表。不要把 SSH 配置写进 agents.toml。不要把密码写进 toml。如果你主张更激进的融合，请明确哪些原则要改，以及代价。

8. **工作量与回归。**  
   方案 A 的最小切片是什么？哪些测试必须先绿？失败时如何回退到现在的两个命令？

---

## 5. 请给出的评审产出

请按这个结构回答，短句，不要空话：

1. **结论**：A / B / 第三方案（一句话描述）。
2. **最强理由**：不超过 5 条。
3. **方案 A 的主要风险**：每条附缓解办法。
4. **若选 A，UI 怎么排**：顶栏、二级 Tab、默认页、键盘/鼠标；给一个 ASCII 线框即可。
5. **若选 A，迁移清单**：配置路径、环境变量、安装脚本、WT 示例、文档。标明哪些必须做、哪些可延后。
6. **明确反对作者的地方**（如果有）。

约束：当前支持面仍是 WSL + Windows Terminal，不要建议引入新依赖，除非收益非常清楚。
