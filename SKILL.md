# Bulletin Worker — Worker 指南

> v3：isolated cron session + think.log + 三层纠偏

## 📋 配置

_config.json 位于根目录，当前值：_

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

所有路径使用配置值，**不要硬编码**。

## 🔧 API 工具

所有工具位于 `scripts/` 目录，使用绝对路径调用：

```bash
# 发留言
/james_pm/bulletin-worker/scripts/bb-post "上级" "消息内容"
/james_pm/bulletin-worker/scripts/bb-post "worker" "消息内容"

# 看最近留言（默认 20 行）
/james_pm/bulletin-worker/scripts/bb-recent [行数]

# 查历史留言
/james_pm/bulletin-worker/scripts/bb-history <YYYY-MM-DD> [YYYY-MM-DD]

# 读 status.json
/james_pm/bulletin-worker/scripts/bb-status get

# 写 status.json 字段
/james_pm/bulletin-worker/scripts/bb-status set <字段名> <值>

# 唤醒上级（标记当前状态可用）
/james_pm/bulletin-worker/scripts/bb-wake
```

## 📊 状态机

（待填充）

## 📝 思考日志 think.log

（待填充）

## ⚙️ 执行流程

每次 cron 唤醒，严格按以下 7 步执行。**每步开始前，将判断依据写入 think.log。**

### 第 0 步：IDLE 检查

```bash
# 读当前状态
/james_pm/bulletin-worker/scripts/bb-status get
```

**判断逻辑：**
- `status == "IDLE"` → 无事可做，写入 think.log 后直接退出本轮 cron
- `status == "ACTIVE"` 或 `status == "BUSY"` → 设 `status = "BUSY"`，继续第 1 步

**写 think.log：**
```
Step 0: status=<当前值> → <决策结果（退出/继续）>
```

### 第 1 步：检查是否有未完成任务

```bash
# 读留言板最近记录
/james_pm/bulletin-worker/scripts/bb-recent

# 读 status 看 mission 信息
/james_pm/bulletin-worker/scripts/bb-status get
```

**判断逻辑：**
- `status.mission` 不存在或为空 → 无任务 → 写 think.log → `bb-post` 回话给上级 → 设 `status = "IDLE"` → 退出
- `status.mission` 存在且有未完成工作 → 写 think.log → 继续第 2 步

**写 think.log：**
```
Step 1: mission.description="<描述>" → <有任务/无任务，决策>
```

### 第 2 步：方向明确性检查

**判断逻辑：**
- `mission.description` 不够清晰，无法执行 → 写 think.log（困惑原因）→ `bb-post` 留言困惑 → 回话 → 设 `status = "IDLE"` → 退出
- 方向明确 → 写 think.log（确认依据）→ 继续第 3 步

**写 think.log：**
```
Step 2: 方向<不明确/明确> — <依据>
```

### 第 3 步：任务拆分

**判断逻辑：**
- `mission.steps` 非空 → 跳过，继续第 4 步
- `mission.steps` 为空 → 拆分为适配一次 cron 周期的子步骤
  - 写 think.log（拆分结果 + 理由）
  - 写入 `status.json` 更新 `steps` 和 `current_step_index`
  - `bb-post` 留言告知拆分结果
  - 继续第 4 步

**写 think.log：**
```
Step 3: steps 空/非空 → <拆分结果>
```

### 第 4 步：执行子任务

```bash
# 确定本轮子步骤
current_step_index = status.mission.current_step_index
step_description = status.mission.steps[current_step_index]
```

**执行步骤：**
1. 写 think.log 记录即将执行的步骤
2. 执行子任务（读文件、翻译、查资料等）
3. `bb-post` 更新进展
4. 更新 `status.json`（`progress`、`current_step_index`、必要时更新 `failed_attempts`）
5. 写 think.log 记录完成摘要

**写 think.log：**
```
Step 4: 执行 <步骤描述> → <完成/失败摘要>
```

### 第 5 步：多轮失败检测

**判断逻辑：**
- `failed_attempts >= 7` → 超出上限，标记阻塞
  - 写 think.log: `Step 5: 超过 7 轮，阻塞:<原因>`
  - `bb-post` 留言告知领导
  - 回话
  - 设 `status = "IDLE"` → 退出
- `failed_attempts < 7` → 未超限，继续

**写 think.log：**
```
Step 5: failed_attempts=<值>，<未超限/超限阻塞> → 决策
```

### 第 6 步：本轮退出 && 回话

**判断逻辑：**
- 还有剩余步骤 → 写 think.log → 回话汇报进展 → 设 `status = "ACTIVE"` → 退出
- 全部完成 → 写 think.log → 回话汇报完成 → 设 `status = "IDLE"` → 退出

**写 think.log：**
```
Step 6: <还有 N 步/全部完成> → status=<ACTIVE/IDLE>
----------------------------------------
```

每轮 think.log 以 `=== <时间戳> [cron-<short_hash>] ===` 开头，以 `----------------------------------------` 结尾。

## 🔄 纠偏机制

（待填充）

## 🚨 异常处理

（待填充）
