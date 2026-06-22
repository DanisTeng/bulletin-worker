# Bulletin Worker 安装指南

## 前置条件

- Python 3.8+
- PyInstaller（`pip install pyinstaller`）
- OpenClaw（cron 功能）

## 安装步骤

### 1. 编辑配置

打开 `config.json`，填写你的环境信息：

```json
{
  "superior_name": "Danis",               // 上级（领导）姓名
  "worker_name": "James",                 // worker 名称
  "worker_workspace": ".../workspace",    // worker session 的工作区目录
  "board_path": ".../board",              // 留言板目录
  "identity_files": ["..."],              // 需加载的身份文件
  "max_recent_lines": 20,                 // bb-recent 默认行数
  "blocking_number": 3,                   // 连续阻塞轮次阈值
  "max_num_sub_task": 5                   // 单个计划书最大子任务数
}
```

### 2. 一键安装

```bash
cd bulletin-worker/
./setup.sh
```

安装脚本自动完成：

| 步骤 | 产出 | 说明 |
|------|------|------|
| 1️⃣ 打包 agent 工具 | `$worker_workspace/tools/` | pyinstaller 将 `agent_tools/*.py` 打成独立 ELF，并渲染 shell wrapper（共 13 个） |
| 2️⃣ 渲染 SKILL.md | `$worker_workspace/SKILL.md` | v2 工作流说明书：四态互斥分流（计划执行/更新计划/新建计划/无任务） |
| 3️⃣ 渲染 TOOLS_USAGE.md | `$worker_workspace/tools/TOOLS_USAGE.md` | 工具使用说明（agent 从 SKILL.md 中得知此文件位置） |
| 4️⃣ 部署 v2 工作流脚本 | `$worker_workspace/scripts/` | 子任务级说明：`update_task_plan.md`、`execute_task_plan.md`、`new_task_plan.md` |
| 5️⃣ 部署计划书设计文档 | `$worker_workspace/scripts/` | 格式/策略说明：`task_plan_format.md`、`task_plan_strategy.md` |
| 6️⃣ 创建 data/ 目录 | `$worker_workspace/data/` | agent 的持久化工作产物存放目录 |
| 7️⃣ 渲染 cron prompt | stdout + `$worker_workspace/PROMPT.md` | 带替换后 config 变量的 prompt 文本，供创建 cron 使用 |

### 3. 创建 cron 任务

复制上一步输出的 cron prompt，创建 OpenClaw cron job：

```bash
openclaw cron add \
  --name "bulletin-worker" \
  --cron "*/10 * * * *" \
  --session isolated \
  --message "<粘贴上一步的 cron prompt>" \
  --timeout-seconds 180
```

> 提示：cron prompt 也在 `$worker_workspace/PROMPT.md` 文件中。

### 4. 测试

上级留言 → 设置状态为 ACTIVE。本工程根目录下的 `scripts/` 有调试用 shell 脚本
（与工作区的 `tools/` 功能相同，但无需运行 setup 即可直接使用）：

```bash
echo "帮我查一下这个数据" | scripts/bb-leader-post
scripts/bb-set-active
```

等待下一个 cron 周期（或手动触发），然后查看留言板确认：

```bash
scripts/bb-recent
```

## 工作区结构

安装完成后，`$worker_workspace/` 内容如下：

```
worker_workspace/
├── tools/                  ← agent 可直接调用的工具
│   ├── TOOLS_USAGE.md      ← 工具使用说明
│   ├── bb-get-status       → 读状态
│   ├── bb-set-active       → 设为 ACTIVE
│   ├── bb-set-busy         → 设为 BUSY
│   ├── bb-set-idle         → 设为 IDLE
│   ├── bb-worker-post      → worker 发普通留言
│   ├── bb-worker-post-execute       → 发留言（[计划执行] 前缀）
│   ├── bb-worker-post-new-mission   → 发留言（[新建计划] 前缀）
│   ├── bb-worker-post-update-mission→ 发留言（[更新计划] 前缀）
│   ├── bb-worker-post-no-mission    → 发留言（[无任务] 前缀）
│   ├── bb-leader-post      → 上级发留言（自动 ACTIVE）
│   ├── bb-recent           → 最近留言
│   ├── bb-history          → 按日期查留言
│   ├── bb-around           → 锚点查留言
│   ├── bb-plan-show-brief  → 只看计划书概要
│   ├── bb-plan-show-next   → 看总述 + 当前待办子任务
│   ├── bb-plan-update      → 更新子任务状态
│   ├── bb-plan-format-check→ 格式检查
│   ├── bb-plan-validate    → 格式检查（同 format-check）
│   ├── bb-plan-archive     → 归档计划书到 plan_archive/
│   ├── bb_board            → ELF（框架入口）
│   ├── bb_plan             → ELF（计划书入口）
│   └── bb_status           → ELF（状态入口）
├── scripts/                ← agent 工作流子任务说明
│   ├── update_task_plan.md
│   ├── execute_task_plan.md
│   ├── new_task_plan.md
│   ├── task_plan_format.md
│   └── task_plan_strategy.md
├── plan/                   ← 计划书持久化
│   └── current_plan.json
├── data/                   ← agent 工作产物
├── SKILL.md                ← v2 工作流说明书
└── PROMPT.md               ← cron prompt
```

## 更新

重新运行 `./setup.sh` 即可增量更新，已有文件不会被删除（ELF 会重新打包）。

## 文件清单

| 路径 | 说明 |
|------|------|
| `config.json` | 用户配置（安装前编辑） |
| `setup.sh` | 一键安装脚本 |
| `core/skill/` | SKILL.md 渲染模板和脚本 |
| `core/task_plan/` | 任务计划渲染脚本和设计文档 |
| `core/prompt/` | cron prompt 渲染脚本和模板 |
| `core/agent_tools/` | 工具定义（tools_def.json） |
| `agent_tools/` | 独立工具源码（.py） |
| `output/` | 渲染中间产物 |
| `test/` | 测试套件 |
