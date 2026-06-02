# Bulletin Worker Skill

Bulletin Worker 是一个极简 cron worker 模式。Worker 每次被 cron 唤醒时执行以下流程。

## 配置源

所有配置在 `config.json` 中统一管理：

```json
{
  "root_dir": "/james_pm/bulletin-worker",
  "board_path": "/james_pm/bulletin-worker/tmp",
  "scripts_dir": "/james_pm/bulletin-worker/scripts",
  "superior_name": "Danis",
  "worker_name": "James",
  "max_recent_lines": 20
}
```

## 环境变量

- `BB_CONFIG` — 覆盖 config.json 路径，默认 `{root_dir}/config.json`

## Prompt 生成

不用 `prompt.txt`。用 `scripts/build_prompt.py` 动态生成 prompt，所有路径和名字从 config.json 读取：

```bash
python3 /path/to/scripts/build_prompt.py
```

输出内容可直接作为 cron agentTurn 的 message 参数。

## API 脚本

`scripts/` 下所有脚本使用绝对路径调用：

```bash
# 发留言
scripts/bb-post "上级" "消息内容"
scripts/bb-post "worker" "消息内容"

# 看最近留言
scripts/bb-recent [行数=20]

# 查历史
scripts/bb-history <YYYY-MM-DD> [YYYY-MM-DD]

# 读写 status.json
scripts/bb-status get
scripts/bb-status set <field> <value>

# 设置 flag（上级专用）
scripts/bb-status flag <内容>
scripts/bb-status flag-clear

# 生成 cron prompt（cron 集成用）
python3 scripts/build_prompt.py
```

## Worker 执行流程

每次 cron 唤醒，worker agent 按以下步骤：

### 1. 读 status
- `scripts/bb-status get`

### 2. 检查 flag
- flag 非空 → 消费它：读 flag 内容 → 清 flag → `set status mission_issued`
- flag 空 → 直接看 status

### 3. 状态分发

| status | 行为 |
|--------|------|
| idle + 无 flag | 没事做，直接结束 |
| mission_issued | 发留言报告开始，`set status working` |
| working | 继续干活，完成后 `set status done` |
| blocked | 检查 blocker 是否解除 |
| done | 等待确认，不做额外工作 |

### 4. 报告进展
- 用 `scripts/bb-post "worker" <内容>` 记录进展
- 完成后用 `scripts/bb-status set progress "完成"`

## 用 `generate_reply` 集成

实际 cron Job 的 message 内容由此生成：

```text
生成 Bulletin Worker 的 cron prompt，运行:
  python3 /james_pm/bulletin-worker/scripts/build_prompt.py
取输出作为 prompt，然后按 prompt 指示执行。
```

使用 `openclaw agent --session-id bulletin-worker-temp` 执行，用完即弃的临时 session。

## 示例：翻译任务

```bash
# 上级设 flag
scripts/bb-status flag "翻译 /docs/manual.md"

# Cron session 1
# worker 读 flag → mission_issued → working
scripts/bb-post "worker" "开始翻译 /docs/manual.md"

# Cron session 2
scripts/bb-post "worker" "已完成 3/50 章"

# 完成
scripts/bb-status set status "done"
scripts/bb-post "worker" "翻译全部完成，共 50 章"
```
