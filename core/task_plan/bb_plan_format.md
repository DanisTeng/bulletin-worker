# Plan JSON 格式说明

## 文件位置

`$BOARD_DIR/plan.json`

## 顶层结构

```json
{
  "briefing": "...",
  "tasks": [
    { "desc": "...", "acceptance": "...", "done": false, "cycles": 0, "note": "" }
  ]
}
```

## 字段约束

| 字段 | 类型 | 约束 |
|------|------|------|
| `briefing` | string | **必填**。任务总述，≤200 字。含任务背景、目的、行为约束。 |
| `tasks` | array | **必填**。task 列表，可为空 `[]`。 |
| `tasks[].desc` | string | **必填**。task 说明，≤100 字。 |
| `tasks[].acceptance` | string | **必填**。验收标准，≤100 字。什么情况下算完成。 |
| `tasks[].done` | boolean | **必填**。`true` 已完成，`false` 未完成。 |
| `tasks[].cycles` | number | **必填**。为该 task 消耗的 cron 周期次数，≥0。 |
| `tasks[].note` | string | **必填**。备注，≤100 字，无内容则空字符串 `""`。 |

## 编辑规则

1. **不删除字段**：上述 7 个字段每个 task 都必须有。不要删 `note` 或留 `null`。
2. **不擅自添加字段**：只能在上述结构内编辑。不要在 task 上加自定义字段。
3. **排序保持**：tasks 数组按添加顺序排列。新 task 追加到末尾。
4. **done = true 的 task 不删**：保留在 tasks 里供回溯。需用 `bb-plan-show-next` 只看未完成的。
5. **cycles 只增不减**：每次为一个 task 消耗了一个 cron 周期后，自增 1。不要清零或减。
6. **完成一个 task 后改下一个**：做完 task[0]，标 `done: true`，切到 task[1]（如果还存在且未完成）。

## 编辑示例

### 创建 plan.json

```json
{
  "briefing": "翻译 /docs/manual.md 前 20 章。背景：文档需要中英双语。约束：每章翻译后运行 md-check 检查格式。",
  "tasks": [
    {
      "desc": "搭建翻译环境",
      "acceptance": "能在本机运行翻译脚本，输出符合 markdown 格式",
      "done": false,
      "cycles": 0,
      "note": ""
    }
  ]
}
```

### 完成 task 并追加新 task

修改后：

```json
{
  "briefing": "翻译 /docs/manual.md 前 20 章。背景：文档需要中英双语。约束：每章翻译后运行 md-check 检查格式。",
  "tasks": [
    {
      "desc": "搭建翻译环境",
      "acceptance": "能在本机运行翻译脚本，输出符合 markdown 格式",
      "done": true,
      "cycles": 1,
      "note": "用了 pip install mdit 方案"
    },
    {
      "desc": "翻译第 1-5 章",
      "acceptance": "输出 5 个 .md 文件，md-check 全部通过",
      "done": false,
      "cycles": 0,
      "note": ""
    }
  ]
}
```
