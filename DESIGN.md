# Bulletin Worker — 设计文档

> 当前设计汇总。本项目经历了多次迭代，这个文档反映项目和设计的当前状态。

## 核心理念

Bulletin Worker 是一个极简的 cron worker 模式。Worker 通过 cron 周期性醒来，以**留言板**作为与上级的唯一通信渠道，完成跨 session 的长周期任务。

> 不是 public session，不是 PM 框架，就是一个会醒会睡的工人。

## 部署模式：isolated cron session

通过 OpenClaw cron job 的 isolated session 模式运行：

```bash
openclaw cron add \
  --name "bulletin-worker" \
  --cron "*/10 * * * *" \
  --session isolated \
  --message "$(cat /path/to/prompt.txt)" \
  --timeout-seconds 180
```

- 每次醒来是全新的独立 session，用完即焚
- 不绑定 session ID，不在 dashboard 留下垃圾 task 记录
- 前一轮与后一轮之间**没有任何上下文延续**——所有状态靠 status.json + 留言板传递

## 状态机

三态，由 worker 自身或上级写入 status.json：

| 状态 | 含义 | 谁写入 |
|------|------|--------|
| IDLE | 空闲，什么也不做 | worker / 上级 |
| ACTIVE | 有任务待执行（一般由上级触发） | 上级 |
| BUSY | worker 正在 cron 内流程中，请勿干扰 | worker |

### 状态变更规则

```
IDLE ──[上级 bb-set-active]──→ ACTIVE
                                    │
                         [cron 第0步：非 IDLE]
                                    │
                                    ▼
                                  BUSY
                                    │
                         [cron 流程结束]
                          ┌─────────┼─────────┐
                          ▼         ▼         ▼
                         IDLE      IDLE     ACTIVE
                       (无事/阻塞) (超时)  (还有工作)
```

## 留言板结构

```
board_path/
├── status.json           ← 状态文件
├── 2026-06-02.md         ← 留言记录
└── ...
```

### status.json

```json
{
  "status": "IDLE",
  "mission": {
    "description": "翻译 /docs/manual.md，共 50 章",
    "steps": [
      "翻译第 1-5 章",
      "翻译第 6-10 章"
    ],
    "current_step_index": 0,
    "failed_attempts": 0
  },
  "progress": null,
  "blocker": null
}
```

### 留言记录格式

```
2026-06-02 14:43 [Danis] 翻译 /docs/manual.md，共 50 章
2026-06-02 14:43 [James] 收到任务，已拆分子步骤并开始翻译
2026-06-02 14:53 [James] 已完成 1/50 章
2026-06-02 15:00 [Danis] 术语表在 glossary.md
2026-06-02 15:01 [James] 收到，继续翻译
```

- 按日期分文件，每天一个
- 只追加，不允许修改或删除历史

## Cron 内工作流

Worker 每次 cron 唤醒后执行的流程由 `SKILL.md` 定义。SKILL.md 由 `core/skill/` 下的多个 part 文件渲染而成。

目前有两个版本的工作流：

### v1 工作流（当前默认）

三条线性步骤流：

1. **IDLE 检查** → 读 status，IDLE 则直接退出
2. **任务检查** → 读留言板，有任务就设 mission，无任务则回话退出
3. **可行性判断 & 执行** → 判断任务是否可执行，执行或报告阻碍

### v2 工作流（开发中）

四种互斥的 cron 轮次类型：

1. **计划执行** — 有 plan.json 且无需更新时，按计划执行子任务
2. **更新计划** — 领导修改了任务或子任务反复失败时，更新计划书
3. **新建计划** — 无 plan 但有新任务时，拆分子任务创建计划书
4. **无任务** — 闲置状态，回复留言后设 IDLE

v2 引入了结构化的 `plan.json` 计划书系统，支持多子任务编排、格式校验、归档和进度追踪。

> v2 是 v1 的替代升级，切换总开关在 `core/prompt/prompt.md` 中的 SKILL.md 引用路径。

## 计划书（plan.json，v2 引入）

### 格式

```json
{
  "briefing": "任务总述，≤200字。含背景、目的、行为约束",
  "tasks": [
    {
      "index": 1,
      "desc": "子任务说明，≤100字",
      "acceptance": "验收标准，≤100字",
      "done": false,
      "note": ""
    }
  ]
}
```

### 设计原则

- **子任务体量**：控制在上下文上限的一半以下
- **验收原则**：每个子任务需要验收型子任务接力检查
- **整洁原则**：子任务间低耦合，结尾加战场打扫子任务

## 纠偏机制

不设编排器，纠偏通过两层机制实现：

### 第一层：cron 轮次间反射（纠偏差）
上一轮 agent 走歪 → 留言板留下错误结果 → 下一轮醒来读留言板发现自己走偏 → 回话等待领导纠正

### 第二层：领导监督（人工纠偏）
领导路过看到留言板 → 回复纠正 → `bb-set-active` → 下轮 cron 按纠正后的方向执行

## 工具链

Agent 工具位于 `agent_tools/`（Python 源码），通过 `setup.sh` 打包成独立 ELF 并部署到工作区 `tools/` 目录。

### 当前工具清单

| 工具 | 功能 | 状态 |
|------|------|------|
| `bb-get-status` | 读状态（IDLE/ACTIVE/BUSY） | ✅ 就绪 |
| `bb-set-active` | 设为 ACTIVE | ✅ 就绪 |
| `bb-set-busy` | 设为 BUSY | ✅ 就绪 |
| `bb-set-idle` | 设为 IDLE | ✅ 就绪 |
| `bb-set-mission` | 写任务描述 | ✅ 就绪 |
| `bb-get-mission` | 读当前任务 | ✅ 就绪 |
| `bb-worker-post` | worker 发留言 | ✅ 就绪 |
| `bb-leader-post` | 上级发留言 | ✅ 就绪 |
| `bb-recent` | 最近留言 | ✅ 就绪 |
| `bb-history` | 按日期查留言 | ✅ 就绪 |
| `bb-around` | 按时间锚点查留言 | ✅ 就绪 |
| `bb-plan-validate` | 计划书格式检查 | ✅ 就绪 |
| `bb-plan-show-next` | 显示下个未完成任务 | ✅ 就绪 |
| `bb-plan-update` | 更新子任务状态 | ✅ 就绪 |

## 渲染链

`setup.sh` 执行以下渲染步骤：

```
setup.sh
 ├── core/agent_tools/render.py   → 打包 ELF + 渲染 sh wrapper → $WORKSPACE/tools/
 ├── core/skill/render.py         → 组装 SKILL.md              → output/ → $WORKSPACE/
 ├── core/prompt/render.py        → 渲染 PROMPT.md             → output/ → $WORKSPACE/
 └── core/task_plan/render.py     → 打包 bb_plan + wrapper + 格式说明
```

SKILL.md 组装顺序：

```
self_recognition_part.md  → 身份认知
tools_description_part.md → 工具使用说明书
workflow_part.md          → 工作流程（v1 或 v2）
```

## 目录结构

```
bulletin-worker/
├── config.json              ← 用户配置
├── setup.sh                 ← 一键安装脚本
├── DESIGN.md                ← 本设计文档
├── SETUP.md                 ← 安装指南
├── agent_tools/             ← 独立工具源码 (.py)
│   ├── bb_board.py          ← 留言板操作（post/recent/history/around）
│   ├── bb_status.py         ← 状态读写（status/mission）
│   └── bb_plan.py           ← 计划书操作（validate/show-next/update）
├── core/
│   ├── agent_tools/         ← 工具渲染模板
│   │   ├── tools_def.json   ← 工具定义（name/description/template）
│   │   └── render.py        ← pyinstaller + sh wrapper 渲染
│   ├── skill/               ← SKILL.md 组件
│   │   ├── self_recognition_part.md
│   │   ├── tools_description_part.md
│   │   ├── workflow_part.md       ← v1 工作流
│   │   ├── workflow_v2_part.md    ← v2 工作流（开发中）
│   │   ├── new_task_plan.md       ← 新建计划流程图（v2）
│   │   ├── update_task_plan.md    ← 更新计划流程图（v2）
│   │   ├── execute_task_plan.md   ← 执行计划流程图（v2）
│   │   └── render.py
│   ├── prompt/              ← cron prompt 模板
│   │   ├── prompt.md
│   │   └── render.py
│   └── task_plan/           ← 计划书系统渲染
│       ├── task_plan_format.md    ← 计划书格式说明（供 agent 参考）
│       ├── task_plan_strategy.md  ← 计划书设计原则（供 agent 参考）
│       ├── bb_plan_format.md      ← 计划书格式说明（部署到 scripts/）
│       └── render.py
├── scripts/                 ← 手动调试用 shell 脚本
├── test/                    ← 黑盒测试套件
│   ├── run.sh
│   ├── helpers.sh
│   └── cases/
├── terminal/                ← bb-terminal：交互式终端
│   └── DESIGN.md            ← 终端详细设计文档
├── tmp/                     ← pyinstaller 构建中间产物
├── output/                  ← 渲染中间产物
└── tools/                   ← 部署产物（work in progress）
```
