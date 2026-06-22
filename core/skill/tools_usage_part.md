所有工具均在 `$worker_workspace/tools/` 下，可直接以 `./tools/<工具名>` 用法调用。

## 状态工具

| 工具 | 说明 |
|------|------|
| `bb-get-status` | 查当前状态，返回 IDLE / ACTIVE / BUSY |
| `bb-set-active` | 设为 ACTIVE（工作中） |
| `bb-set-busy` | 设为 BUSY（忙碌中，防干扰） |
| `bb-set-idle` | 设为 IDLE（空闲） |
| `bb-get-mission` | 看当前任务描述 |
| `bb-set-mission <描述>` | 记录当前任务 |

## 留言工具

| 工具 | 说明 |
|------|------|
| `bb-worker-post` | worker 发普通留言。支持 echo 管道和 argv 两种用法 |
| `bb-worker-post-execute` | worker 发留言，内容自动加 [计划执行] 前缀 |
| `bb-worker-post-new-mission` | worker 发留言，内容自动加 [新建计划] 前缀 |
| `bb-worker-post-update-mission` | worker 发留言，内容自动加 [更新计划] 前缀 |
| `bb-worker-post-no-mission` | worker 发留言，内容自动加 [无任务] 前缀 |
| `bb-leader-post` | 上级发留言，发完后自动设为 ACTIVE |

## 留言查询

| 工具 | 说明 |
|------|------|
| `bb-recent [行数] [--grep <关键词>]` | 看最近留言（默认 20 行） |
| `bb-history <日期>` | 按日期查留言 |
| `bb-around <时间> <前N> <后N> [--grep <关键词>]` | 以指定时间为锚点查留言 |

## 计划书工具

| 工具 | 说明 |
|------|------|
| `bb-plan-show-brief` | 只看总述 + 进度统计 |
| `bb-plan-show-next` | 看总述 + 当前待办子任务 |
| `bb-plan-update --index=N --done=true/false [--note="备注"]` | 更新子任务状态 |
| `bb-plan-format-check` | 格式检查 |
| `bb-plan-archive <计划名>` | 归档到 plan_archive/ 目录 |
| `bb-plan-validate` | 格式检查（同 bb-plan-format-check） |
