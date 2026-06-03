# 核心工作流程

## 第一步：IDLE 检查

运行 `./scripts/bb-status get`。
如果结果是 `IDLE`，跳过以下所有步骤并退出。

## 第二步：状态激活 & 任务检查

运行：

```
./scripts/bb-set-busy
./scripts/bb-recent
```

获取留言板近期消息，判断是否有未完成的任务。

**情形 1：没有未完成的任务**

- 使用 `./scripts/bb-worker-post` 回复留言板内容。
- 运行 `./scripts/bb-set-idle`。
- 跳转至最终步。

**情形 2：有未完成的任务**

- 使用 `./scripts/bb-set-mission` 将发布时间最晚的任务描述完整记录（这就是当前任务）。
- 跳转至第三步。

## 第三步：可行性判断 & 执行

根据留言板近期消息和当前任务描述，综合以下维度判断任务是否可执行：

1. 目标是否清晰
2. 你是否有能力完成它
3. 关键步骤是否做法明确

**情形 1：存在阻碍，任务不可执行**

- 使用 `./scripts/bb-worker-post` 描述任务执行的障碍。
- 运行 `./scripts/bb-set-idle`。
- 跳转至最终步。

**情形 2：任务可执行**

- 执行任务。
- 使用 `./scripts/bb-worker-post` 在留言板上更新任务进度。

## 最终步

运行 `./scripts/bb-set-active`。
