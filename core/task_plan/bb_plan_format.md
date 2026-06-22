# Plan JSON 格式说明

## 文件位置

`$WORKSPACE_DIR/plan/current_plan.json`

## 顶层结构

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
| `briefing` | string | **必填**。任务总述，≤200 字。含任务背景、目的、行为约束。 |
| `tasks` | array | **必填**。task 列表，可为空 `[]`。 |
| `tasks[].index` | number | **必填**。编号，从 1 开始，与列表顺序一致。`bb-plan-update` 用这个编号定位 task。 |
| `tasks[].desc` | string | **必填**。task 说明，≤100 字。 |
| `tasks[].acceptance` | string | **必填**。验收标准，≤100 字。什么情况下算完成。 |
| `tasks[].done` | boolean | **必填**。`true` 已完成，`false` 未完成。 |
| `tasks[].note` | string | **必填**。备注，≤100 字，无内容则空字符串 `""`。 |

## 编辑规则

1. **index 自动维护**：`validate` 模式下会自动按列表顺序重写全部 index（从 1 开始）。不要手动改 index。
2. **不删除字段**：上述 7 个字段每个 task 都必须有。不要删 `note` 或留 `null`。
3. **不擅自添加字段**：只能在上述结构内编辑。不要在 task 上加自定义字段。
4. **排序保持**：tasks 数组按添加顺序排列。新 task 追加到末尾。
5. **done = true 的 task 不删**：保留在 tasks 里供回溯。
6. **完成一个 task 后改下一个**：做完 index=N 的 task，标 `done: true`，切到 index=N+1（如果还存在且未完成）。

## 更新 task（推荐用 bb-plan-update）

不要直接编辑 JSON 修改 task 的 done 或 note。用：

```sh
bb-plan-update --index=1 --done=true
bb-plan-update --index=3 --note="卡在第三关"
bb-plan-update --index=2 --done=true --note="md-check 全过"
```

## 编辑示例

### 创建 plan.json

```json
{
  "briefing": "翻译 /docs/manual.md 前 20 章。背景：文档需要中英双语。约束：每章翻译后运行 md-check 检查格式。",
  "tasks": [
    {
      "index": 1,
      "desc": "搭建翻译环境",
      "acceptance": "能在本机运行翻译脚本，输出符合 markdown 格式",
      "done": false,
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
      "index": 1,
      "desc": "搭建翻译环境",
      "acceptance": "能在本机运行翻译脚本，输出符合 markdown 格式",
      "done": true,
      "note": "用了 pip install mdit 方案"
    },
    {
      "index": 2,
      "desc": "翻译第 1-5 章",
      "acceptance": "输出 5 个 .md 文件，md-check 全部通过",
      "done": false,
      "note": ""
    }
  ]
}
```
