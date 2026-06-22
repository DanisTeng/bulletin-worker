所有工具均在 `$worker_workspace/tools/` 下，可直接以 `./tools/<工具名>` 用法调用。

## 状态工具

| 工具 | 说明 |
|------|------|
| `bb-get-status` | 查当前状态，返回 IDLE / ACTIVE / BUSY |
| `bb-set-active` | 设为 ACTIVE（工作中） |
| `bb-set-busy` | 设为 BUSY（忙碌中，防干扰） |
| `bb-set-idle` | 设为 IDLE（空闲） |

## 留言工具

每个 post 工具都支持 argv 和管道两种传参方式。argv 用法：直接跟在命令后面。管道用法：用 `echo` 传内容。

| 工具 | 说明 | 示例 |
|------|------|------|
| `bb-worker-post` | worker 发普通留言 | `./tools/bb-worker-post 遇到了一个依赖问题` |
| `bb-worker-post-execute` | 内容自动加 `[计划执行]` 前缀 | `./tools/bb-worker-post-execute 完成了数据清洗模块` |
| `bb-worker-post-new-mission` | 内容自动加 `[新建计划]` 前缀 | `./tools/bb-worker-post-new-mission 计划名：数据标注工具` |
| `bb-worker-post-update-mission` | 内容自动加 `[更新计划]` 前缀 | `./tools/bb-worker-post-update-mission 将验收标准改为纯色背景` |
| `bb-worker-post-no-mission` | 内容自动加 `[无任务]` 前缀 | `./tools/bb-worker-post-no-mission 当前没有待办任务` |
| `bb-leader-post` | 上级留言，发完后自动设为 ACTIVE | `./tools/bb-leader-post 请检查图片格式要求` |

> 提示：留言内容中的 `\n` 会被解析为换行，支持多行消息。管道用法：`echo "一行内容\n另一行内容" | ./tools/bb-worker-post`

## 留言查询

| 工具 | 说明 | 示例 |
|------|------|------|
| `bb-recent [条数] [--grep <关键词>]` | 看最近留言（按条数计，续行不计），可过滤关键词 | `./tools/bb-recent`（默认 20 条）/ `./tools/bb-recent 30 --grep 依赖` |
| `bb-history <日期>` | 查指定日期的留言 | `./tools/bb-history 2026-06-22` |
| `bb-around <时间> <前N> <后N> [--grep <关键词>]` | 以某个留言时间为锚点查前后文 | `./tools/bb-around "2026-06-22 14:30" 3 3`（查那条留言前后各 3 行） |

## 计划书工具

| 工具 | 说明 |
|------|------|
| `bb-plan-show-brief` | 只看总述 + 进度统计 |
| `bb-plan-show-next` | 看当前待办子任务（描述 + 验收标准），不展示总述 |
| `bb-plan-update --index=N --done=true/false [--note="备注"]` | 更新子任务状态 |
| `bb-plan-format-check` | 格式检查 |
| `bb-plan-validate` | 格式检查（同 bb-plan-format-check） |
| `bb-plan-archive` | 归档当前 plan 到 `plan_archive/` 目录，计划名从 plan.json 内部 `name` 字段读取 |
| `bb-plan-clear` | 删除 `current_plan.json`，用于任务完结后清理 |
