# 新建计划轮次

## Step 1. 了解计划书格式
- 阅读 ./scripts/task_plan_format.md

## Step 2. 了解计划书设计原则
- 阅读 ./scripts/task_plan_strategy.md

## Step 3. 或许计划书无法构建
如果遇到以下情况：
- leader 的任务说明有重要细节未能明确
- leader 批示的任务的可行性未卜，关键依赖项不明确
- leader 批示的任务体量远超 $max_num_sub_task 个子任务
则：
- 使用 `./tools/bb-worker-post-new-mission` 回复留言板内容，说明所遇障碍与建议
- 运行 `./tools/bb-set-idle`
- 跳转至最终步
否则执行下一步。

## Step 4. 尝试构建计划书
- 将计划书构建到 `plan/current_plan.json`
- 使用 `./tools/bb-plan-format-check` 进行检查，修复格式问题确保格式检查通过
- 运行 `./tools/bb-plan-archive` 进行计划书归档，计划名要尽可能防止与其他 plan 文件中的计划混淆
- 使用 `./tools/bb-worker-post-new-mission` 回复留言板内容
- 运行 `./tools/bb-set-active`

## 最终步
结束
