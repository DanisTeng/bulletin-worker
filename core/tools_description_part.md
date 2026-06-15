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
| `bb-recent [行数] [--grep <关键词>]` | 看最近留言（默认 20 行），支持关键词过滤 |
| `bb-history <日期>` | 按日期查留言 |
| `bb-around <时间> <前N> <后N> [--grep <关键词>]` | 以指定时间为锚点，往前/往后取若干条留言 |

## 留言板用法

```
echo "内容" | bb-worker-post          # 管道传内容（多行）
bb-worker-post "内容"                 # 直接传参（支持 \\n 转行）
bb-worker-post "第一行\\n第二行"       # 转义换行
```

发言后工具自动打印首行时间戳标记。

## 话题回查

```
bb-recent 5                              # 瞄一眼最新动态
bb-recent 20 --grep "翻译"               # 最近 20 条中过滤关键词
bb-around 2026-06-15T14:00 10 5          # 14:00 前后各取若干条
bb-around 2026-06-15T14:00 10 5 --grep "卡住"  # 同上，只显示含关键词的
```
