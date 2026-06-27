# bb-terminal — Bulletin Worker 交互式终端

## 背景

bb-worker 目前通过 bb_board.py 和 bb_status.py 等工具与留言板、状态机交互，但所有操作都在 agent 内部执行。作为 human leader，想要：

1. 实时查看留言板最新动态，不用 `cat` 翻文件
2. 快速做 leader post（批示），不用敲长命令
3. 看到 cron_daemon 的运行状态（上次执行、token 消耗等）
4. 一个统一的入口管理整个 bulletin worker 系统

bb-terminal 是一个 30Hz 循环的 TUI（终端用户界面），上半屏显示留言板，下半屏是输入区，常驻运行。

## 界面布局

```
┌─ bb-terminal v1 ════════ bulletin worker ─ 2026-06-26 18:22 ═══─┐
│ [STATUS]  IDLE  │  上次执行: 18:20:15  OK                     │
│────────────────────────────────────────────────────────────── │
│ 2026-06-26 17:50 [James] 翻译完成，检查中                      │   ← 留言区
│ 2026-06-26 18:00 [Danis] 继续执行                             │
│ 2026-06-26 18:15 [James] 遇到阻塞: 缺少参考文档                │  ← 最新在最下
│ 2026-06-26 18:20 [Danis] 已补充，继续                          │
│                                                                  │
│────────────────────────────────────────────────────────────── │
│ > 输入留言内容...                                               │   ← 输入区
│ [Ctrl+D 发送]  [Ctrl+C 退出]  [Tab 切换焦点]                   │
└────────────────────────────────────────────────────────────────┘
```

**两分屏设计：**

- **上半屏（~70%）** — 留言板 + 状态栏，只读，自动滚动
- **下半屏（~30%）** — 用户输入区，支持多行输入，Ctrl+D 提交

## 核心数据流

```
┌─────────────┐     30Hz 循环      ┌──────────────┐
│  bb_board   │ ←────────────→    │  bb-terminal │
│  status.json│  读文件系统         │              │
│  cron_log/  │                    │  计算显示     │
└─────────────┘                    │  处理输入     │
                                  └──────────────┘
                                          ↑
                                   OnUserInput(string)
                                   Ctrl+D 提交 → post
```

## 主要逻辑

### 1. ComputeDisplayString() → str

每次循环调用，计算当前帧应该显示什么。

```python
def ComputeDisplayString(config) -> str:
    """
    计算当前帧的完整 TUI 字符串。
    读取以下数据源（30Hz 刷新，但文件读太频繁没必要，建议 1Hz 重读）：
    1. bb_board.recent(N) — 最近 N 条留言
    2. status.json — 当前状态 (IDLE/ACTIVE/BUSY)
    3. cron_log/ 最新日志 — 上次执行状态
    4. 当前时间 + 系统负载（可选）
    
    返回格式化后的完整帧。包含 ANSI 清屏/光标定位序列。
    """
    # 1. 状态栏
    # 2. 分隔线
    # 3. 留言区（自动滚动到底部——最新留言可见）
    # 4. 分隔线
    # 5. 输入区 + 提示
```

- **留言区**：始终显示最新 N 条（默认 10 条），超长行自动截断或换行
- **状态栏**：读取 status.json 字段，显示状态、上次执行时间、上次执行结果（OK/FAIL）
- **滚动**：留言区可以向上翻看历史，用 ↑/↓ 或 PageUp/PageDown

### 2. OnUserInput(text: str)

用户按 Ctrl+D 或 Enter（多行模式）后触发。

```python
def OnUserInput(text: str) -> None:
    """
    消费用户输入文本。
    行为：
    1. 去除首尾空白，空输入忽略
    2. 特殊命令检查（见下方）
    3. 默认为 leader post：调用 bb_board.post(board_dir, "Leader", text)
    4. 写入后重绘界面，输入区清空
    """
```

**特殊命令（以 / 开头）：**

| 命令 | 作用 | 例 |
|------|------|----|
| `/status` | 显示详细状态（status.json 全部字段） | `/status` |
| `/recent N` | 临时改留言条数 | `/recent 20` |
| `/exec` | 立即触发一次 cron_daemon 执行 | `/exec` |
| `/help` | 显示帮助 | `/help` |
| `/quit` | 退出 terminal | `/quit` |

无特殊前缀 → 当作 leader post 消息发到留言板。

### 3. Tick() / 30Hz 主循环

```python
def Tick():
    """
    一次 tick 循环：
    1. 计算显示帧（ComputeDisplayString）
    2. 渲染到终端
    3. 等待键盘输入（非阻塞，timeout=33ms → ~30Hz）
    4. 如果有输入行结束信号（Ctrl+D / Enter），触发 OnUserInput
    5. 如果有控制键（↑/↓/PageUp/PageDown），处理滚动
    6. Ctrl+C → 清理退出
    """
```

## 未来能力（预留接口）

### a. cron_daemon 集成

terminal 启动时可附带启动 cron_daemon 为子进程：

```
bb-terminal --with-daemon -i 10 -t 900
```

- terminal 退出时 kill 子进程 cron_daemon
- cron_daemon 每轮执行后往 `cron_log/last_run.json` 写状态摘要
- terminal 读取该文件实时显示状态

**cron_daemon 的实时状态文件格式（建议）**：`cron_log/last_run.json`

```json
{
  "round": 42,
  "timestamp": "2026-06-26T18:20:15Z",
  "success": true,
  "session_id": "cron-1748348415-12345",
  "input_tokens": 4520,
  "output_tokens": 1280,
  "duration_sec": 85
}
```

### b. Token 计量

cron_daemon 执行 agent 后，可通过 gateway API 或 `openclaw status` 差异计算 token 用量，写入文件供 terminal 读取。

### c. 多面板

后续可按 Tab 切换面板：留言板 / 系统日志 / cron_daemon 执行历史 / 配置编辑。

## 实现策略

**为什么不用现成的 TUI 库（Textual / urwid / curses）：**

- 减少依赖，避免 pyinstaller 打包额外负担
- 30Hz 简单循环手写维护成本更低
- 控制权完全在手，想加什么特殊能力随时加

**裸写 TUI 的做法：**

```
帧渲染:
  - sys.stdout.write("\033[2J\033[H")  # 清屏 + 光标归零
  - 逐行 output(帧内容)
  - sys.stdout.flush()

键盘输入:
  - sys.stdin.read(1)                  # 阻塞读一个字符（配合 select 设 timeout）
  - 识别控制序列: 箭头键 → \033[A / \033[B
  - Ctrl+D → \x04 → 触发 submit
  - Ctrl+C → \x03 → 清理退出

行编辑:
  - 简单字符收集，只支持退格
  - 多行: Enter 换行，Ctrl+D 结束输入
```

**文件路径：**
- `terminal/bb_terminal.py` — 主程序
- `terminal/DESIGN.md` — 本设计文档

## 文件组织

```
terminal/
├── DESIGN.md          ← 本文档
├── bb_terminal.py     ← 主程序（单文件，约 300-500 行）
└── README.md          ← 使用说明（可选）
```

## 潜在问题

1. **终端尺寸变化** — 需响应 SIGWINCH，重查终端宽高重新布局
2. **文件读频繁** — 30Hz 读 board 文件没必要，1Hz 即可；status.json 和 last_run.json 也一样
3. **中文对齐** — 中文字符宽度 2，英文 1，需自行处理 wcwidth（Python 的 `unicodedata.east_asian_width` 不够准，建议用 `wcwidth` 库或简单启发式处理，或者不追求完美对齐）
4. **Ctrl+D 与 stdin 缓冲** — 终端默认是 line-buffered，需要把 stdin 设成 raw mode（`tty.setraw()`）才能逐字符读取

## 后续步骤

1. 实现 `bb_terminal.py` v1 核心功能：留言显示 + leader post
2. 跟进 `cron_daemon` 添加 `last_run.json` 实时状态输出
3. 集成 token 计量
4. 可选：`bb-terminal --with-daemon` 模式
