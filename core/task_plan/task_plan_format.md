# 计划书格式说明

## 顶层结构
是一个 json 文件：
```json
{
  "briefing": "...",
  "tasks": [
    { "index": 1, "desc": "...", "acceptance": "...", "done": false, "note": "" }
  ]
}
```

## 字段约束

| 字段 | 类型 | 约束 |
|------|------|------|
| `briefing` | string | **必填**。任务总述，≤200 字。含任务背景、发布时间、目的、行为约束。 |
| `tasks` | array | **必填**。task 列表，不可为空。 |
| `tasks[].index` | number | **必填**。编号，从 1 开始，与列表顺序一致，用于标识。 |
| `tasks[].desc` | string | **必填**。task 说明，≤100 字。 |
| `tasks[].acceptance` | string | **必填**。验收标准，≤100 字。说明什么情况下算完成。 |
| `tasks[].done` | boolean | **必填**。`true` 已完成，`false` 未完成。 |
| `tasks[].note` | string | **必填**。备注，≤100 字，无内容则为空字符串 `""`。 |
