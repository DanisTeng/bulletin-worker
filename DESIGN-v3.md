# Bulletin Worker — 设计文档 v3

> v3 定稿：方案 A（一次 isolated cron session 走完全部流程）
> 极简模式：无 think.log，无唤醒脚本，纯 cron 驱动。
>
> v2 见 `DESIGN-v2.md`（状态机与流程定型页），v1 见 `DESIGN.md`（已弃用）。

## 核心理念

Bulletin Worker 是一个极简的 cron worker 模式。Worker 通过 cron 周期性醒来，以**留言板**作为与上级的唯一通信渠道，完成跨 session 的长周期任务。

> 不是 public session，不是 PM 框架，就是一个会醒会睡的工人。

## 部署模式：方案 A

选择方案 A 而非 Orchestrator-Worker（方案 B）的理由：

| 维度 | 方案 A（一次 session） | 方案 B（编排器） |
|------|----------------------|-------------------|
| token 消耗 | 每轮 1 次 agent 调用 | 每轮 3-5 次 agent 调用 |
| 实现复杂度 | 纯 prompt，零代码 | 需维护 `worker.py` 编排器 |
| 纠偏机制 | cron 轮次间异步反射 + 领导监督 | 实时阻断 + 单步测试 |
| 适用场景 | 长周期任务，容忍偶发偏差 | 高可靠短任务 |

方案 A 的纠偏依赖 cron 周期间的留言板反射（详见下文"纠偏机制"），通过短周期醒来的异步检查补偿单次 session 内的控制力不足。

## 技术选型：isolated cron session

通过 OpenClaw cron job 的 `--session isolated` 模式运行：

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

## Cron 内流程

每次 cron 唤醒 worker 后，严格按照以下步骤执行。

### 第 0 步：IDLE 检查

```
读 status.json
if status == "IDLE":
    直接退出本轮 cron
if status == "ACTIVE" | "BUSY":
    设 status = "BUSY"
    继续第 1 步
```

### 第 1 步：检查是否有未完成任务

```
读留言板最近 N 条记录
判断 status.mission 是否存在未完成任务：
  无任务 → 回话 → 设 IDLE → 退出
  有任务 → 继续第 2 步
```

"回话"：根据留言板上下文回复领导（汇报/回答/告知无事）。

### 第 2 步：方向明确性检查

```
判断 mission.description 是否清晰可执行：
  方向不明确 → 留言困惑 → 回话 → IDLE → 退出
  方向明确 → 继续第 3 步
```

### 第 3 步：任务拆分

```
if mission.steps 非空 → 跳过，继续第 4 步
if mission.steps 为空：
    → 拆分为适配一次 cron 周期的子步骤
    → 写入 status.json
    → 留言板记录拆分结果
    → 继续第 4 步
```

### 第 4 步：执行子任务

```
确定本轮子步骤：steps[current_step_index]
执行子步骤
无论成败：
  - 留言板更新结果
  - 更新 status.json（progress / index / failed_attempts）
继续第 5 步
```

### 第 5 步：多轮失败检测

```
if failed_attempts >= 7:
    → 留言告知领导
    → 回话
    → IDLE → 退出
else:
    继续第 6 步
```

### 第 6 步：本轮退出 & 总结合理

```
还有剩余步骤？→ 回话 → ACTIVE
全部完成？→ 回话 → IDLE
```

## 纠偏机制

方案 A 不设编排器，纠偏通过两层机制实现：

### 第一层：cron 轮次间反射（纠偏差）
```
Cron N: agent 走歪了 → 留言板上留下错误结果或困惑
Cron N+1: 醒来读留言板 + status.json →
          第 2 步方向检查发现不对劲 →
          方向不明确 → 留言困惑 + 设 IDLE → 等领导
```

### 第二层：领导监督（人工纠偏）
领导路过看到留言板 → 回复纠正 → `bb-set-active` → 下轮 cron 按纠正后的方向执行

## 唤醒机制

上级留言后，手动设置 ACTIVE 即可触发下轮 cron 执行：

```bash
scripts/bb-set-active
# 或直接写 status.json
bb-status set status ACTIVE
```

上级的常规操作流：留言 → `bb-set-active` → 等 cron 回应 → 看留言板 → 再留言...

## 工作区结构

render_agent_tools.py 将 agent 工具渲染到 `$worker_workspace/tools/` 目录下。agent 在 session 中通过 `./tools/bb-*` 路径访问这些工具。

```
$worker_workspace/
├── tools/                    ← 渲染生成的工具（shell wrapper + 独立 ELF）
│   ├── bb-get-status
│   ├── bb-set-active
│   ├── bb-set-busy
│   ├── bb-set-idle
│   ├── bb-set-mission
│   ├── bb-get-mission
│   ├── bb-worker-post
│   ├── bb-leader-post
│   ├── bb-recent
│   └── bb-history
├── SKILL.md                  ← agent 执行流程（由 core/workflow_part.md 渲染）
└── PROMPT.md                 ← cron prompt 模板（由 core/prompt.md 渲染）
```

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

## 与 v2 的关键区别

| 对比项 | v2 | v3 |
|--------|----|----|
| 部署模式 | 未明确 | 方案 A：isolated cron session |
| 纠偏设计 | 隐含在流程中 | 显式两层（反射 + 监督） |
| task 残留 | 未考虑 | isolated session 不留垃圾 |
