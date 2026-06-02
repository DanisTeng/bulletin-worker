# Bulletin Worker — 设计文档 v2

> v2 对工作流进行了重新设计：精简状态机，明确 cron 内流程，引入 BUSY 保护态。
> v1 见 `DESIGN.md`（旧版 5 态状态机 + flag 唤醒机制，已弃用）。

## 核心理念

Bulletin Worker 是一个极简的 cron worker 模式。Worker 通过 cron 周期性醒来，以**留言板**作为与上级（人类或 agent）的唯一通信渠道，完成跨 session 的长周期任务。

> 不是 public session，不是 PM 框架，就是一个会醒会睡的工人。

## 状态机

三个状态，由 worker 自身或上级写入 status.json：

| 状态 | 含义 | 谁写入 |
|------|------|--------|
| IDLE | 空闲，什么也不做（几乎不消耗 token） | worker / 上级 |
| ACTIVE | 有任务待执行（一般由上级触发） | 上级 |
| BUSY | worker 正在 cron 内流程中，请勿干扰 | worker |

### 状态变更规则

```
IDLE ──[上级 set ACTIVE]──→ ACTIVE
                               │
                    [cron 第0步判断非 IDLE]
                               │
                               ▼
                             BUSY
                               │
                    [cron 流程结束]
                     ┌─────────┼─────────┐
                     ▼         ▼         ▼
                    IDLE      IDLE     ACTIVE
                  (没事做)  (阻塞)   (完成任务仍有工作)
                              ↑
             上级解除阻塞后重新 set ACTIVE
```

- **IDLE → ACTIVE**: 上级手动触发（通过 `bb-wake` 或直接 `bb-status set status ACTIVE`）
- **ACTIVE → BUSY**: cron 第 0 步自动设置
- **BUSY → IDLE**: 无事可做、方向不明确、阻塞退出时
- **BUSY → ACTIVE**: 完成本轮子步骤且任务未结束时
- **上级不要在 BUSY 时写留言或改状态**

## 留言板结构

```
board_path/
├── status.json           ← 状态文件，仅 worker 写入（flag 机制已移除）
├── 2026-06-01.md         ← 留言记录，按日期分文件
├── 2026-06-02.md
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

字段说明：
- `status`: IDLE | ACTIVE | BUSY
- `mission.description`: 任务描述（上级写入的原始描述）
- `mission.steps`: 拆分后的子步骤列表（worker 按步骤 3 拆分后写入）
- `mission.current_step_index`: 当前正在执行的子步骤索引（从 0 开始）
- `mission.failed_attempts`: 当前子步骤已失败的 cron 轮次计数（≥7 时阻塞）
- `progress`: 进度说明（自由文本）
- `blocker`: 阻塞原因（非空时表示 blocked，上级解除后清空）

不设 `flag` 字段。上级唤醒 worker 的唯一方式：`bb-status set status ACTIVE`。

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
- 纯文本，agent 直接通过 API 读取（`bb-recent`）

## Cron 内流程

每次 cron 唤醒 worker 后，严格按照以下步骤执行：

### 第 0 步：IDLE 检查

```
读 status.json
if status == "IDLE":
    直接退出本轮 cron（不做任何操作）
if status == "ACTIVE" | "BUSY":
    设 status = "BUSY"（告知上级不要干扰）
    继续第 1 步
```

`ACTIVE` 和 `BUSY` 统一视为有任务待处理。如果上级不小心在 BUSY 时设置了 ACTIVE，不影响运行。

### 第 1 步：检查是否需要执行任务

```
读留言板最近 N 条记录（N = max_recent_lines）
判断 status.json 中是否存在未完成任务：
  - mission.description 为空 → 无任务 → 需要回话 → 根据留言历史回复领导 → 设 IDLE → 退出
  - mission.description 非空 → 继续第 2 步
```

"回话"是给领导的通用回复，内容包括主动汇报、回答问题、告知无事可做。Agent 根据留言板上下文自行判断回复内容。

### 第 2 步：方向明确性检查

```
判断 mission.description 是否清晰、可执行：
  方向不明确（不清晰、缺上下文、缺输入文件等）：
    - 在留言板上写困惑与阻塞点
    - 根据留言历史给领导一个回话
    - 设 status = "IDLE"
    - 退出本轮 cron
  方向明确 → 继续第 3 步
```

### 第 3 步：任务拆分

```
if mission.steps 已存在且非空：
    → 跳过此步，继续第 4 步
if mission.steps 为空：
    → 将 mission.description 拆分为按顺序执行的子步骤
    → 每个子步骤的大小应适配一次 cron 周期的执行能力（上下文长度 + 时间预算）
    → 将拆分结果写入 status.json mission.steps
    → 在留言板上记录拆分结果
    → 继续第 4 步
```

### 第 4 步：执行子任务

```
确定本轮要执行的子步骤：
  - 读取 mission.steps 和 mission.current_step_index
  - 确定当前应执行的步骤

执行子步骤。执行完毕后：
  - 在留言板上更新执行结果（成功/失败/部分完成）
  - 更新 status.json：
    - progress: 进展说明
    - mission.current_step_index: 成功后 +1
    - mission.failed_attempts: 失败后 +1（成功后重置为 0）
  - 继续第 5 步
```

### 第 5 步：多轮失败检测

```
if mission.failed_attempts >= 7:
    - 在留言板上告知领导：当前阻塞在哪、已尝试轮次
    - 根据留言历史给领导一个回话
    - 设 status = "IDLE"
    - 退出本轮 cron
else:
    → 继续第 6 步
```

7 轮的限制来自配置 `max_consecutive_failures`。

### 第 6 步：本轮退出

```
检查是否还有剩余子步骤：
  - current_step_index < len(steps) → 还有工作：
    - 根据留言历史给领导一个回话（汇报本轮结果 + 下一步计划）
    - 设 status = "ACTIVE"（下轮 cron 继续工作）
    - 退出
  - current_step_index >= len(steps) → 全部完成：
    - 根据留言历史给领导一个回话（汇报完成）
    - 设 status = "IDLE"
    - 退出
```

## 唤醒机制

上级通过两种方式唤醒 worker：

1. **命令行**：`scripts/bb-wake`（封装 `bb-status set status ACTIVE`）
2. **手动**：`scripts/bb-status set status "ACTIVE"`

上级的一般做法：每次在留言板上写完留言后，顺手跑一次 `bb-wake`。

## 重新入队机制

worker 处于 ACTIVE 时（非 BUSY）可被多次唤醒而不产生竞态：
- 第 0 步统一设 BUSY，串行化执行
- ACTIVE 和 BUSY 在第 0 步均视为"有工作"
- 上级不小心在 BUSY 时 set ACTIVE 不影响运行

## 配置

```json
{
  "root_dir": "/james_pm/bulletin-worker",
  "board_path": "/james_pm/bulletin-worker/tmp",
  "scripts_dir": "/james_pm/bulletin-worker/scripts",
  "superior_name": "Danis",
  "worker_name": "James",
  "max_recent_lines": 20,
  "max_consecutive_failures": 7
}
```

新增配置项：
- `max_consecutive_failures`: 同一子步骤连续失败的最大次数（默认 7）

## 与 v1 的关键区别

| 对比项 | v1 | v2 |
|--------|----|----|
| 状态数 | 5（idle/mission_issued/working/blocked/done） | 3（IDLE/ACTIVE/BUSY） |
| 唤醒机制 | flag 字段 | 直接 set status=ACTIVE |
| BUSY 保护 | 无 | 有（防止竞态） |
| 方向检查 | 无 | 第 2 步 |
| 任务拆分 | 模糊分片 | 第 3 步标准化 |
| 多轮失败 | 无 | 第 5 步（7 轮上限） |
| 回话机制 | 无 | 每次 exit 前回复领导 |
| flag 字段 | 有 | 移除 |
