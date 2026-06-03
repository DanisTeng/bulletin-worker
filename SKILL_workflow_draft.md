# 核心工作流程

## 第一步
运行: ./scripts/bb-status get, 如果结果是IDLE, 跳过以下所有步骤并退出.

## 第二步
运行: ./scripts/bb-set-busy
运行: ./scripts/bb-recent 获取留言版近期消息, 根据返回值判断是否你有未完成的任务.
情形1： 没有未完成的任务:
- 使用 ./scripts/bb-worker-post 命令回复留言板中内容.  
- 运行 ./scripts/bb-set-idle
- 跳转至最终步.

情形2： 有未完成的任务:
- 使用 ./scripts/bb-set-mission 命令将发布时间最晚的任务描述完整记录. 这个就是当前任务
- 跳转至第三步

## 第三步
- 根据留言板近期消息， 和当前任务描述, 综合以下维度判断该任务是否可执行：
1. 目标是否清晰.
2. 你是否有能力完成它.
3. 关键步骤是否做法明确.
情形1：上述维度中存在阻碍，任务可执行性受阻:
- 使用 ./scripts/bb-worker-post 命令回复留言板中内容并描述任务执行的障碍.
- 运行 ./scripts/bb-set-idle
- 跳转至最终步.
情形2：任务可执行:
- 执行任务.
- 使用 ./scripts/bb-worker-post 命令在留言板上更新任务进度.

# 最终步
运行: ./scripts/bb-set-active
