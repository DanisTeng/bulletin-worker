# 执行计划轮次

## Step 1. 或许没有子任务.
- 运行 ./tools/bb-plan-show-next 知悉当前子任务情况. 
如果返回结果为 null, 则:
- 使用 `./tools/bb-worker-post-execute` 回复留言板内容, 说明所有任务已经完成.
- 运行 `./tools/bb-set-idle`
- 跳转至最终步
否则执行下一步.

## Step 2. 执行子任务.
根据子任务描述的内容严格执行. 注意:
- 子任务的实施过程不能以破坏其它子任务或者任务的成果为代价.
- 子任务的执行过程如果遇到关键细节不明确， 则视为受阻，不要擅自决定.
- 子任务的执行追求高品质， 如果是代码开发， 自我review一遍的过程不可缺少.

如果子任务执行受阻或者未能完成(以acceptance作为完成判据), 则:
- 使用 `./tools/bb-worker-post-execute` 回复留言板内容, 说明所遇到的障碍.
- 运行 `./tools/bb-set-idle` 
否则:
- 使用 `./bb-plan-update` 更新任务状态为done=true. 在note中补充必要信息防止其它模块找不到你的工作结果.
- 使用 `./tools/bb-worker-post-execute` 回复留言板内容, 宣告该子任务已完成，宣告时注意带上任务index.
- 运行 `./tools/bb-set-active`

## 最终步
结束
