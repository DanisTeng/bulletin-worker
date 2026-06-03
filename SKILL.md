# Bulletin Worker — Worker 指南




> v3：isolated cron session + think.log + 三层纠偏

## 状态机

三态，由 worker 自身或上级写入 status.json：

| 状态 | 含义 | 谁写入 |
|------|------|--------|
| IDLE | 空闲，什么也不做 | worker / 上级 |
| ACTIVE | 有任务待执行（一般由上级触发） | 上级 |
| BUSY | worker 正在 cron 内流程中，请勿干扰 | worker |

### 状态变更规则

```
IDLE ──[上级 bb-wake]──→ ACTIVE
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

### 用到的文件

```
board_path/
├── status.json        ← 状态 + 任务信息
├── think.log          ← 每轮 cron 的结构化思考日志
├── 2026-06-02.md      ← 留言记录（按日期分文件）
└── ...
```

#### status.json 结构

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

#### think.log 格式

每轮 cron 追加一条，agent 将每步判断依据结构化输出：

```
=== 2026-06-02 15:30 [cron-abc123] ===
Step 0: status=ACTIVE → BUSY
Step 1: mission.description="翻译 /docs/manual.md" → 有任务，继续
Step 2: 方向明确（文件存在，内容清晰，需要翻译 50 章）→ 继续
Step 3: steps 为空 → 拆分如下：["翻译第 1-5 章","翻译第 6-10 章",...]
Step 4: 执行 steps[0]="翻译第 1-5 章"...已完成
Step 5: failed_attempts=0 → 未超限
Step 6: 还有 49 章 → status=ACTIVE
----------------------------------------
```

#### 留言记录格式

```
2026-06-02 14:43 [Danis] 翻译 /docs/manual.md，共 50 章
2026-06-02 14:43 [James] 收到任务，已拆分子步骤并开始翻译
2026-06-02 14:53 [James] 已完成 1/50 章
```

- 按日期分文件，只追加不修改不删除

## 执行流程

每次 cron 唤醒 worker 后，严格按照以下 7 步执行。**每步之前，将判断依据写入 think.log。**

> 留言统一用 `scripts/bb-worker-post`（以 worker 身份），不用 `bb-post`。
> 回话 = 根据留言板上下文，用 `bb-worker-post` 回复领导。

### 第 0 步：IDLE 检查

```bash
scripts/bb-get-status
```

**判断逻辑：**
- 返回 `IDLE` → 无事可做，写 think.log 后直接退出本轮 cron
- 返回 `ACTIVE` 或 `BUSY` → `scripts/bb-set-busy`，继续第 1 步

**写 think.log：**
```
Step 0: status=<当前值> → <退出/继续>
```

### 第 1 步：检查是否有未完成任务

```bash
scripts/bb-recent
scripts/bb-get-status
```

**判断逻辑：**
- `mission` 不存在或为空 → 无任务 → 写 think.log → 回话给上级 → `scripts/bb-set-idle` → 退出
- `mission` 存在且有未完成工作 → 写 think.log → 继续第 2 步

**写 think.log：**
```
Step 1: mission.description="<描述>" → <无任务/有任务>
```

### 第 2 步：方向明确性检查

**判断逻辑：**
- `mission.description` 不够清晰无法执行 → 写 think.log（困惑原因）→ 留言困惑 → 回话 → `scripts/bb-set-idle` → 退出
- 方向明确 → 写 think.log（确认依据）→ 继续第 3 步

**写 think.log：**
```
Step 2: 方向<不明确/明确> — <依据>
```

### 第 3 步：任务拆分

**判断逻辑：**
- `steps` 非空 → 跳过，继续第 4 步
- `steps` 为空 → 拆分为适配一次 cron 周期的子步骤
  - 写 think.log（拆分结果 + 理由）
  - 写入 status.json 更新 `steps` 和 `current_step_index`（用 `scripts/bb-status set`）
  - 留言告知拆分结果
  - 继续第 4 步

**写 think.log：**
```
Step 3: steps<空/非空> → <拆分结果>
```

### 第 4 步：执行子任务

**执行步骤：**
1. 从 `steps[current_step_index]` 确定本轮子步骤
2. 写 think.log 记录即将执行的步骤
3. 执行子任务（读文件、写代码等）
4. 留言记录进展
5. 更新 `status.json`（`progress`、`current_step_index`、必要时更新 `failed_attempts`）
6. 写 think.log 记录完成摘要

**写 think.log：**
```
Step 4: 执行 <步骤描述> → <完成/失败摘要>
```

### 第 5 步：多轮失败检测

**判断逻辑：**
- `failed_attempts >= 7` → 超出上限，标记阻塞
  - 写 think.log: `Step 5: 超过 7 轮，阻塞:<原因>`
  - 留言告知领导
  - 回话
  - `scripts/bb-set-idle` → 退出
- `failed_attempts < 7` → 未超限，继续

**写 think.log：**
```
Step 5: failed_attempts=<值> → <未超限/超限阻塞>
```

### 第 6 步：退出 & 回话

**判断逻辑：**
- 还有剩余步骤 → 写 think.log → 回话汇报进展 → `scripts/bb-set-active` → 退出
- 全部完成 → 写 think.log → 回话汇报完成 → `scripts/bb-set-idle` → 退出

**写 think.log：**
```
Step 6: <还有 N 步/全部完成> → status=<ACTIVE/IDLE>
----------------------------------------
```

每轮 think.log 以 `=== <时间戳> [cron-<short_hash>] ===` 开头，以 `----------------------------------------` 结尾。
