# 评审回复：Agent Deck 与 Host Deck 要不要合成一个 TUI

> 评审人：Claude (Opus 5)　日期：2026-08-27
> 基线：`7f144ff`，`python3 -m unittest discover -s tests` → 55 项全绿

---

## 1. 结论

**反对方案 A 的当前形态。走第三方案（下称 B+）。**

B+ = 保持两个命令、两个进程、两个可见 Profile 不动；只做三件低风险的事：
一是产品叙事统一叫 Terminal Deck；二是两个 TUI 各加一个「跳到另一个 deck」的键；
三是配置目录**新增**首选位，但不搬家。

一句话：你想要的那一下「点一下就换」，用一个键就能拿到，不需要合并进程、不需要
搬配置、不需要顶层 Tab。

---

## 2. 最强理由（5 条）

1. **A 想省的那一步，日常路径上不存在。**
   开一个新工作页签的动作是「下拉 → 选 Profile」。A 之后还是这两步。真正被省掉的
   只有「人已经在一个 deck 里，想去另一个」——这是低频路径。用 Tabby 时省掉的是
   *跨应用窗口*切换，量级完全不同。

2. **你自己的代码已经否决了「一个可见 Profile」。**
   `src/ai_launcher.py:492`：*Windows Terminal 的 tab 图标只能由 profile 决定*。
   要一眼分清「这个页签是 Agent」还是「这个页签是 SSH」，就必须留两个可见 Profile。
   两个 Profile 一留，A 在入口层面的优势就归零了，只剩下内部重构。

3. **一层 Tab 套一层 Tab 是真的坏，不是「认知成本低」。**
   外层 `Agent | Host` 和内层 `New | Resume` 用的是同一套 `tab_header` 渲染，长得
   一模一样，含义完全不同；而且外层一切，内层的语义整个换掉（`New|Resume` 变
   `Connect|Attach`）。这是模式混淆。用户点错的代价还不对称：点错 Agent 只是多开个
   会话，点错 Host 是发起一次真实 SSH 连接。

4. **进程隔离你高估了，但你漏了真正的耦合点。**
   高估的部分：两边 `launch()` 最后都是 `os.execv`，环境变量是拼成 bash 脚本文本交给
   子进程的（`build_script`），从来没有写进 TUI 自己的 `os.environ`。合并外壳不会
   合并运行时环境，泄漏风险约等于零。
   漏掉的部分：**`host_deck.py` 本身就是 SSH_ASKPASS 助手**。
   `askpass_path()`（`src/host_deck.py:1219`）返回 `realpath(__file__)`，
   `main()` 第一句（`:1349`）就检查 `HOST_DECK_ASKPASS=1`。合并之后，那个新入口
   同时是「TUI」和「密码助手」。启动路径上多任何一步（读 agents.toml、画一帧、
   判断落地页），都可能让 ssh 的密码提示卡住或走空。收益不值这个改动。

5. **配置搬家是从零开始，而且是唯一不可逆的部分。**
   `scripts/install.sh` 现在**没有任何迁移代码**——只有「存在就保留、不存在就装样例」。
   治理原则第 5 条目前是写下来的意愿，不是已实现的能力。
   同时 `scripts/uninstall.sh --purge` 写死了旧路径；配置相关的环境变量有 7 个
   （`AI_LAUNCHER_CONFIG` / `AI_LAUNCHER_HISTORY` / `HOST_DECK_CONFIG` /
   `HOST_DECK_HISTORY` / `HOST_DECK_FAVORITES` / `HOST_DECK_SSH_CONFIG` /
   `HOST_DECK_COLLAPSED`），**一条测试都没覆盖**（`grep` 过 `tests/`，零命中）。
   拿最不可逆的一块，换一个纯命名上的整齐，不划算。

---

## 3. 逐条表态（对应你的第 4 节）

### 1. 切换成本是否真实？——**反对（是过度设计）**

不真实，或者说被高估了一个量级。Tabby↔Terminal 是跨应用窗口切换：两套 UI、两套配置、
alt-tab。Profile↔Profile 是同一个窗口里的一次下拉。你已经把大头解决了，剩下这点不值
一次架构合并。

而且 A 在最常见的路径上**一步都没省**：新开页签仍是「下拉 → 选」。

### 2. 一层 Tab 套一层 Tab——**反对**

见第 2 节第 3 条。更好的信息架构：**deck 切换不该长得像二级 Tab**。
它是「我在哪个产品里」，不是「我在这个产品的哪个模式里」。层级不同，控件就该不同。
如果非做不可，把它放进标题行做成前缀 + 快捷键（见第 5 节线框），永远只留一排 Tab。

### 3. 一个进程 vs 两个进程——**部分同意你，但结论仍是别合**

- 环境变量泄漏风险：**你高估了**。`execv` + 脚本文本传环境，天然隔离。
- 崩溃隔离：**你高估了**。TUI 阶段崩溃只影响还没起会话的那一刻。
- 但 askpass 双重身份：**你漏了**，这是真风险。见第 2 节第 4 条。
- 还有一个不对称：`ai` 交接后直接退出，`host` 交接后回到列表继续循环
  （`host_deck.py:main` 里 `if result != "handoff": return`）。合并后要在一个循环里
  同时表达这两种生命周期，壳没有你想的那么薄。

### 4. 配置目录——**要改**

不要「搬」。要「加」。

| | 一套 `terminal-deck/` | 两套 `agent-deck/` + `host-deck/` |
|---|---|---|
| 迁移成本 | 要新写迁移逻辑 + 回退兼容 | `host-deck` 已经是目标名；只有 `ai-launcher` 需要动 |
| 卸载 | 一个目录，干净 | 两个目录，`--purge` 各删一次 |
| 环境变量 | 7 个旧变量全部要保留兜底 | 同上 |
| 出错代价 | 一次搬两个产品的配置 | 一次只影响一个产品 |

我的建议：**读取顺序 = 环境变量 > 新路径 > 旧路径**，两个路径长期共存，
再给一条显式的 `--migrate-config`。绝不在 `install.sh` 里自动搬。
理由：自动搬是唯一一个「装错了回不去」的动作，而它换来的只是路径好看。

### 5. 默认落地页——**反对「记住上次」**

「记住上次」在启动器上是三个里最差的。启动器是**冷启动**的：你打开它的时候，
意图已经在脑子里了；而记住的那个状态是**上一次的**意图设的。两者不相关的概率很高，
错一次的代价是「想开 Claude，结果盯着一屏服务器列表」。

而且你自己说了 `ai` / `host` 命令保留——那 90% 的进入都自带上下文，「记住」根本用不上。

**固定默认 Agent**。固定 = 可预测 = 肌肉记忆。

### 6. WT Profile 数量——**两个可见，全部隐藏 Profile 保留**

理由就是 `ai_launcher.py:492` 那条注释：图标只能跟 Profile 走。两个可见 Profile 的
真实价值不是「入口」，是**页签上的图标能让你在一排页签里认出哪个是 SSH**。
各 Agent 的隐藏 Profile 和隐藏 `SSH (WSL)` 是 handoff 拿到正确图标的唯一手段，必须留。

### 7. 不做的边界——**全部同意**

不混排列表、SSH 配置不进 `agents.toml`、密码不进 toml。三条都同意，我不主张更激进的融合。
再补一条：**不要把两边的 `build_script` 合并**。它们的差异是本质的——一边是
proxy/env/PATH 编排，一边是 askpass/Windows 网络选路。硬抽公共层只会两边都变难读。

### 8. 工作量与回归——**要改（见第 6 节）**

A 的最小切片必须满足一个硬条件：**新入口是第三个入口，不是替换。**
`~/.local/bin/ai` 和 `~/.local/bin/host` 两个符号链接保持指向原文件不变。
这样回退就是「把 WT Profile 的命令行改回 `~/.local/bin/ai`」，零成本。
这条能成立的前提，还是**不搬配置**。

---

## 4. 方案 A 的主要风险与缓解

| 风险 | 具体位置 | 缓解 |
|---|---|---|
| 一边配置坏，两边都死 | `ai_launcher.py:554` 在解析参数**之前**就 `load_agents()`，缺文件直接 `sys.exit`。今天 `host` 不受影响，合并后会被拖下水 | 按 Tab 懒加载；单边失败只在该 Tab 内红字提示，不退出进程。补 1 条测试锁死 |
| askpass 被拖慢或走空 | 合并入口同时是 `SSH_ASKPASS` 目标 | `HOST_DECK_ASKPASS` 分支必须是 `main()` 第一条语句，早于任何配置加载和 TUI 初始化。补 1 条测试锁死 |
| 迁移不可逆 | `install.sh` 无迁移代码；`uninstall.sh --purge` 写死旧路径 | 只做「新优先、旧兜底」的读顺序 + 显式 `--migrate-config`；永不自动搬 |
| 环境变量断代 | 7 个 `*_CONFIG` / `*_HISTORY` 等，零测试覆盖 | 先补 7 条测试再动任何路径逻辑；旧变量永远保持最高优先级 |
| 落地页错上下文 | 「记住上次」 | 固定默认 Agent；`ai` / `host` 各自直达对应 Tab |
| handoff 路径写死 | 两处 `wt_handoff` 内联命令写死 `~/.local/bin/ai` 和 `~/.local/bin/host` | 合并时同步改，并**真开一次 WT 页签验证**（`HANDOFF.md` 已列为要求，单测覆盖不到） |
| 生命周期不对称 | `ai` 交接后退出，`host` 交接后回列表循环 | 顶层循环显式区分两种返回语义，不要靠隐式 fallthrough |

---

## 5. 若选 A，UI 怎么排

**只留一排 Tab。** deck 身份放进标题行做前缀，用一个键切，不做成第二排 Tab。

```text
   ╭────────────────────────────────────────────────────────────────╮
   │  Terminal Deck · Agent                        5 agents · v0.6  │
   ╰────────────────────────────────────────────────────────────────╯

                      New  |  Resume            ← 唯一一排 Tab

     ▸  ●  Claude Official        ~/dev/ai            2 分钟前
        ●  Claude CC-Switch       ~/dev/ai            昨天
        ●  Codex                  ~/dev/tools         3 天前
        ●  Grok                   ~/dev/ai            08-24
        ●  Kimi                   ~/dev               08-20

    ────────────────────────────────────────────────────────────────
      ↑↓ 选择   Enter 启动   Tab 切换 New/Resume   d 切到 Host   q 退出
```

- **顶栏**：`Terminal Deck · Agent`。`Agent` 用该 deck 的强调色，其余灰。
  它是一个可点区域（沿用 `hit_tab` 机制），点一下在 Agent / Host 之间切。
- **二级 Tab**：位置、渲染、键位全部不动。这是唯一一排 Tab。
- **默认页**：固定 `Agent`。`ai` 落 Agent，`host` 落 Host。不记忆。
- **键盘**：`Tab` 保持切二级（不改肌肉记忆）；新增 **`d`** 切 deck。
  必须是 `d`，不能是 `s`——`s` 在 Agent 列表里已经是「纯 Shell」
  （`src/ai_launcher.py:286`）。`d` 在两边都空闲：Agent 侧已占 `j k l s q` + 数字，
  Host 侧已占 `a c e f i j k l n q`。
- **鼠标**：只有标题行那个前缀是热区，面积小、离列表远，误点概率低。

**对比：B+ 里这块长什么样。** 一模一样，只是标题行不可点，footer 那句
`s 切到 Host` 触发的是「用已有的 `wt_handoff` 机制开 Host Deck 的 Profile 新页签」——
图标正确，进程隔离保留，配置不动。**同样一个键，风险少一个数量级。**

---

## 6. 若选 A，迁移清单

### 必须做（缺一条就不要上）

1. **新入口是第三个入口。** 新增 `src/deck.py`；`~/.local/bin/ai`、`~/.local/bin/host`
   的符号链接指向不变。`install.sh` 只多装一个文件、多建一个链接。
2. **askpass 分支置顶。** `deck.py:main()` 第一条语句检查 `HOST_DECK_ASKPASS`，
   早于一切配置加载。加测试。
3. **配置懒加载。** 进哪个 Tab 才读哪个 toml；读失败只在该 Tab 内报错。加测试。
4. **7 个环境变量先补测试再动。** `AI_LAUNCHER_CONFIG`、`AI_LAUNCHER_HISTORY`、
   `HOST_DECK_CONFIG`、`HOST_DECK_HISTORY`、`HOST_DECK_FAVORITES`、
   `HOST_DECK_SSH_CONFIG`、`HOST_DECK_COLLAPSED`。
5. **配置路径只加不搬。** 读取顺序：环境变量 > `~/.config/terminal-deck/` >
   现有路径。`install.sh` 不做任何 `mv`。
6. **必须先绿的测试**：现有 55 条 + 上面第 2/3/4 条新增的（约 10 条）。
7. **真机验证**：开一次 WT 页签，确认 handoff 后图标、标题、`--resume`、
   带密码的 SSH（走 askpass）四条路径都对。单测覆盖不到这些。

### 可延后

- WT `profiles.example.jsonc` 新增一个 `Terminal Deck` 可见 Profile（旧两个先都留着）。
- `README.md` / `CHANGELOG.md` / `AGENTS.md` 的叙事统一。
- `--migrate-config` 显式迁移命令。
- `uninstall.sh` 增加新目录的清理分支。

### 不要做

- 不要动 `install.sh` 里现有的两条 `install -m 0644` 配置分支。
- 不要在 `deck.py` 上线的同一个版本里改配置默认路径。分两个版本走。

### 回退

把 WT Profile 的命令行改回 `AI_LAUNCHER_HANDOFF=1 ~/.local/bin/ai`。就这一步。
前提是上面「必须做」的第 1 和第 5 条都遵守了。

---

## 7. 明确反对作者的地方

1. **反对「切换成本」这条理由本身。** 它是你倾向 A 的第 3 条理由，也是最弱的一条。
   A 在高频路径上一步都没省。你真正的动机应该是另外两条——统一叙事、给将来第三个
   deck 留位置——那两条是站得住的，但都不需要合并进程就能拿到。
2. **反对「WT 可以只留一个可见 Profile」。** 你自己代码里的注释就是反驳。
3. **反对「记住上次停在哪一边」。** 冷启动的工具不该记忆上下文。固定默认。
4. **反对把配置搬到 `~/.config/terminal-deck/`。** 这是全案里唯一不可逆的动作，
   换来的只是路径整齐。要就加一层读取优先级，别搬。
5. **反对「合成一个进程主要是壳，不是重写领域逻辑」这个判断。** 壳里有四处真实耦合：
   askpass 双重身份、启动期配置加载时机、两边不同的交接生命周期、写死的
   `~/.local/bin/*` 路径。都能解，但它不是「主要是壳」。

**同意你的地方**：不混排列表、SSH 不进 `agents.toml`、密码不进 toml——三条边界都对，
不要动。共用 `deck_tui.py` 这一步做得干净，`tab_header` / `draw` / `hit_*` 的抽象
是对的，继续这么复用就够了，不必再往上合。
