# Bulletin Worker — 设计文档

## 核心理念

Bulletin Worker 是一个极简的自动化模式：让 OpenClaw 作为 worker，通过 cron 周期性醒来，以**留言板**作为与上级（人类或 agent）的唯一通信渠道，完成跨 session 的长周期任务。

> 不是 public session，不是 PM 框架，就是一个会醒会睡的工人。

## 三件事

Worker 每次被 cron 唤醒后，只做三件事：

0. **检查标志位** — 看看有没有任务等着我（idle / mission_issued）
1. **读写留言板** — 读上级指示，写工作进展（通过 API，不要手改文件）
2. **更新状态** — 告诉上级我现在是 working / blocked / done

做完就睡，下次 cron 再醒。

## 架构

```
┌─────────────────────────────────┐
│          上级 (Human/Agent)       │
└────────────┬────────────────────┘
             │ 读写留言板（通过 API）
             ▼
┌─────────────────────────────────┐
│       留言板 (Bulletin Board)     │
│                                 │
│  ┌─────────────────────────┐    │
│  │ status.json              │    │  ← 状态文件
│  │   - 当前状态             │    │
│  │   - 当前任务描述         │    │
│  │   - 进度信息             │    │
│  └─────────────────────────┘    │
│                                 │
│  ┌─────────────────────────┐    │
│  │ board/2026-06-01.md      │    │  ← 原始留言记录，按日期分
│  │ board/2026-06-02.md      │    │     只追加，不删除不修改
│  │ board/2026-06-03.md      │    │
│  └─────────────────────────┘    │
└────────────┬────────────────────┘
             │ cron 周期性读取
             ▼
┌─────────────────────────────────┐
│     OpenClaw (Worker Agent)      │
│  cron session → 读留言板 →       │
│  干活 → API写留言板 → 更新状态  │
│  → 睡                           │
└─────────────────────────────────┘
```

## 留言板数据结构

### 1. 状态文件: `status.json`

仅由 worker 写入。上级通过 flag 间接触发 worker。

```json
{
  "status": "idle",
  "mission_id": null,
  "mission_title": null,
  "progress": null,
  "blocker": null,
  "flag": null
}
```

- **status**: idle | mission_issued | working | blocked | done
- **flag**: 上级写入，worker 读取并消费。取值如 `"新任务：翻译xxx"`、`"术语表在 glossary.md"`
- 除 flag 外所有字段由 worker 维护
- flag 由上级维护，worker 消费后清空

### 2. 原始留言记录: `board/YYYY-MM-DD.md`

纯文本格式，每次追加一条，格式：

```markdown
2026-06-01 22:15 [上级] 开始翻译这个文档
2026-06-01 22:20 [worker] 收到，已翻译第1-5章
2026-06-01 22:30 [worker] 第6章遇到术语问题，请确认
2026-06-01 22:35 [上级] 术语表已更新
```

- 按日期分文件，每天一个
- **只追加**，不允许修改或删除历史
- 日期时间戳、发言人由 API 自动写入
- 纯文本，可被 agent 直接读取（`tail -20` 看最近消息）

## 状态机

```
idle ──[上级设 flag]──→ mission_issued
                            │
                 [worker 消费 flag]
                            │
                            ▼
                        working ──[设 blocker]──→ blocked
                            │                         │
                 [worker 完成]              [上级解 blocker + 设 flag]
                            │                         │
                            ▼                         ▼
                          done ◄────────────────── working
                            │
                 [上级检查后清 flag]
                            │
                            ▼
                          idle
```

### 状态变更规则

| 字段 | 谁写 | 谁读 |
|------|------|------|
| `status` | worker | 双方 |
| `mission_*` | worker | 双方 |
| `progress` | worker | 上级 |
| `blocker` | worker | 上级 |
| `flag` | 上级 | worker（消费后清空） |
| 留言记录 | 双方（通过API） | 双方 |

## Bulletin Board API

所有对留言板的操作必须通过 API。agent 和上级不能直接编辑文件。

### 接口定义

```bash
# 1. 发留言（双方共用）
bb_post <发言人> <内容>
# 自动添加时间戳，追加到今日的 board/YYYY-MM-DD.md
# 示例: bb_post "worker" "已翻译 10-15 章"

# 2. 看最近消息
bb_recent [行数]
# 默认 tail -20，从今天的 board 文件读取
# 若今天无记录，从最近一天的文件读

# 3. 按日期查历史
bb_history <YYYY-MM-DD> [YYYY-MM-DD]
# 查一天或一个区间的留言

# 4. 更新状态（worker 专用）
bb_status set <field> <value>
# 示例: bb_status set status "working"

# 5. 读状态
bb_status get
# 返回 status.json 内容

# 6. 设 flag（上级专用）
bb_flag set <内容>
bb_flag clear
```

### 使用约定

- **Worker agent**: 只能通过 `bb_post` 和 `bb_status` 写数据
- **上级（人/agent）**: 通过 `bb_post` 和 `bb_flag` 交互
- **无人直接编辑 board/ 下的文件或 status.json**
- Agent 在 cron session 中的提示词需声明此约束

## 与现存模式的关键区别

| | Public Session | Bulletin Worker |
|---|---|---|
|通信|飞书实时 WS + REST|纯本地文件留言板|
|复杂度|高（WS线程、消息队列、表情管理）|极低（读文件→干活→写文件）|
|状态持久化|JSON 文件 + DB 混合|一个 JSON 文件|
|多 session|复杂 session 管理|cron 自然隔离|
|可维护性|代码膨胀后难维护|核心逻辑 < 50 行 shell|

## Cron 触发机制

通过 OpenClaw cron 设置：

```bash
# 每 10 分钟检查一次
openclaw cron add --name "bulletin-worker" \
  --schedule "*/10 * * * *" \
  --kind agentTurn \
  --message "$(cat /james_pm/bulletin-worker/prompt.txt)"
```

具体 prompt 内容放在 `prompt.txt` 中，skill 不硬编码。

## 大任务分片机制

以 100 万字翻译为例的工作流程：

1. 上级设 flag: `bb_flag set "翻译 documents/book.md，共 50 章"`
2. Cron session 1: worker 读 flag → status=mission_issued → 改为 working → `bb_post "worker" "开始翻译"` → 翻第 1 章 → `bb_post "worker" "完成第1章"` → `bb_status set progress "1/50"`
3. Cron session 2: 读 status → 续翻第 2 章...
4. ...重复
5. 第 50 章: status=done → `bb_post "worker" "全部翻译完成"`
6. 上级检查后清 flag

## 后续 TODO

- [ ] 实现 bulletin-board CLI (`bb_*` 脚本)
- [ ] 搭建 skill 目录：SKILL.md + prompt.txt + config.json
- [ ] 实现 cron 集成示例
- [ ] 大任务分片测试（翻译场景）
- [ ] 飞书集成（可选阶段）
