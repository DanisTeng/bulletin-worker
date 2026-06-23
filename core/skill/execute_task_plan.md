# 执行计划轮次

## Step 1. 或许没有子任务
- 运行 `./tools/bb-plan-show-next` 知悉下个子任务情况
如果返回结果显示没有剩下的子任务，则：
- 使用 `./tools/bb-worker-post-execute <内容>` 回复留言板内容，说明所有任务已经完成
- 运行 `./tools/bb-plan-archive` 进行完成版的计划书归档
- 运行 `./tools/bb-plan-clear` 移除现有计划书
- 运行 `./tools/bb-set-idle`
- 跳转至最终步
否则执行下一步。

## Step 2. 执行下个子任务
根据这个子任务描述的内容严格执行。**注意本轮cron只执行这一个子任务**
- 子任务的实施过程不能以破坏其他子任务或任务的成果为代价
- 子任务的执行追求高品质，如果是代码开发，自我 review 一遍的过程不可缺少

如果子任务执行中遇到以下受阻情形:
- 遇到leader未确认的关键抉择: 该抉择会大幅影响后续工作的走向.
- 子任务体量超预期, 进度受阻.
- 子任务本身要求你征求leader的审核或者建议, 且此时留言板中尚未得到leader的确认性答复.
- 子任务不可行, 进度受阻（以 acceptance 作为完成判据）.
则：
- 使用 `./tools/bb-worker-post-execute <内容>` 回复留言板内容，说明所遇到的问题.
- 运行 `./tools/bb-set-idle`
否则：
- 使用 `./bb-plan-update` 更新任务状态为 done=true，在 note 中补充必要信息防止其他模块找不到你的工作结果
- 使用 `./tools/bb-worker-post-execute <内容>` 回复留言板内容，宣告该子任务已完成，宣告时注意带上任务 index
- 运行 `./tools/bb-set-active`

## 最终步
结束
