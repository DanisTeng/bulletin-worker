# 工具说明

所有工具在 `$worker_workspace/tools/` 下。

| 工具 | 说明 |
|------|------|
| `bb-get-status` | 查当前状态，返回 IDLE/ACTIVE/BUSY |
| `bb-set-active` | 设为 ACTIVE（工作中） |
| `bb-set-busy` | 设为 BUSY（忙碌中，防干扰） |
| `bb-set-idle` | 设为 IDLE（空闲） |
| `bb-get-mission` | 看当前任务描述 |
| `bb-set-mission <描述>` | 记录当前任务 |
| `bb-worker-post` | 以 worker 身份留言。支持 echo 管道和 argv 两种用法 |
| `bb-leader-post` | 以领导身份留言。用法同上 |
| `bb-recent [行数]` | 看最近留言（默认 20 行） |
| `bb-history <日期>` | 按日期查留言 |

## 留言板用法

```
echo "内容" | bb-worker-post          # 管道传内容（多行）
bb-worker-post "内容"                 # 直接传参（支持 \\n 转行）
bb-worker-post "第一行\\n第二行"       # 转义换行
```

发言后工具自动打印首行时间戳标记。
