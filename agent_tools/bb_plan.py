#!/usr/bin/env python3
"""
bb_plan.py — 计划书工具

提供计划书的验证与只读查询能力。

两种用法：

  1. 格式检查
     bb_plan.py <plan.json> validate
     返回 0（格式正确，打印结构性摘要）或 1（格式错误，打印错误原因）

  2. 展示最新未完成 task
     bb_plan.py <plan.json> show-next
     如果有一个或多个未完成 task，打印最新的一条（tasks 第一个 done=false）
     + 简要统计（总数/已完成/未完成/总周期消耗）。
     如果没有未完成 task 或 plan 不存在，打印合适消息并返回 0（非错误）。
"""

import json
import os
import sys


_EXIT_OK = 0
_EXIT_ERR = 1


def _err(msg: str):
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(_EXIT_ERR)


# ── 字段约束 ──────────────────────────────────────────────────────

_BRIEFING_MAX = 200
_DESC_MAX = 100
_ACCEPTANCE_MAX = 100
_NOTE_MAX = 100

_REQUIRED_TOP_LEVEL = {"briefing", "tasks"}
_REQUIRED_TASK_FIELDS = {"desc", "acceptance", "done", "cycles", "note"}


# ── 校验器 ────────────────────────────────────────────────────────


def _validate_string(val, field_path: str, max_len: int) -> list[str]:
    errors = []
    if not isinstance(val, str):
        errors.append(f"{field_path}: 应为字符串，实际为 {type(val).__name__}")
    elif len(val) > max_len:
        errors.append(f"{field_path}: 超出长度限制（{len(val)} > {max_len} 字）")
    return errors


def _validate_task(task, idx: int) -> list[str]:
    errors = []

    # 类型检查
    if not isinstance(task, dict):
        errors.append(f"tasks[{idx}]: 应为 object，实际为 {type(task).__name__}")
        return errors

    # 字段完整性
    missing = _REQUIRED_TASK_FIELDS - set(task.keys())
    extra = set(task.keys()) - _REQUIRED_TASK_FIELDS
    if missing:
        errors.append(f"tasks[{idx}]: 缺少字段 {sorted(missing)}")
    if extra:
        # 只 warning 不报错，允许 agent 临时加辅助字段
        print(f"⚠️  tasks[{idx}]: 额外字段 {sorted(extra)}——agent 自定义？", file=sys.stderr)

    # 字段类型与长度
    for field_path, validate_fn in [
        ("desc", lambda v: _validate_string(v, f"tasks[{idx}].desc", _DESC_MAX)),
        ("acceptance", lambda v: _validate_string(v, f"tasks[{idx}].acceptance", _ACCEPTANCE_MAX)),
        ("note", lambda v: _validate_string(v, f"tasks[{idx}].note", _NOTE_MAX)),
    ]:
        if field_path.replace(f"tasks[{idx}].", "") in task:
            errors.extend(validate_fn(task[field_path.replace(f"tasks[{idx}].", "")]))
        # else: 已通过 missing 检查覆盖，不重复报

    if "done" in task:
        if not isinstance(task["done"], bool):
            errors.append(f"tasks[{idx}].done: 应为 boolean，实际为 {type(task['done']).__name__}")

    if "cycles" in task:
        if not isinstance(task["cycles"], int) or task["cycles"] < 0:
            errors.append(f"tasks[{idx}].cycles: 应为非负整数，实际为 {type(task['cycles']).__name__}")

    return errors


def validate(plan: dict, path: str = "") -> bool:
    """验证 plan 结构。返回 True=通过，False=有错误。"""
    errors = []

    if not isinstance(plan, dict):
        _err(f"plan.json: 顶层应为 object，实际为 {type(plan).__name__}")

    # 顶层字段完整性
    missing_top = _REQUIRED_TOP_LEVEL - set(plan.keys())
    if missing_top:
        errors.append(f"缺少顶层字段 {sorted(missing_top)}")

    # briefing
    if "briefing" in plan:
        errors.extend(_validate_string(plan["briefing"], "briefing", _BRIEFING_MAX))

    # tasks
    if "tasks" in plan:
        if not isinstance(plan["tasks"], list):
            errors.append(f"tasks: 应为 array，实际为 {type(plan['tasks']).__name__}")
        else:
            for i, task in enumerate(plan["tasks"]):
                errors.extend(_validate_task(task, i))

    if errors:
        print(f"📋 plan.json 结构检查失败 ({len(errors)} 个问题):", file=sys.stderr)
        for e in errors:
            print(f"   • {e}", file=sys.stderr)
        return False

    # 通过后打印结构性摘要
    tasks = plan.get("tasks", [])
    done_count = sum(1 for t in tasks if t.get("done"))
    total_cycles = sum(t.get("cycles", 0) for t in tasks)
    briefing_preview = plan.get("briefing", "")
    if len(briefing_preview) > 50:
        briefing_preview = briefing_preview[:47] + "..."

    print(f"✅ plan.json 格式正确")
    print(f"📌 briefing: {briefing_preview}")
    print(f"📊 tasks: {len(tasks)} 个（已完成 {done_count}，未完成 {len(tasks) - done_count}）")
    print(f"⏱  总周期消耗: {total_cycles}")
    return True


# ── 展示最新未完成 task ───────────────────────────────────────────


def show_next(plan: dict, path: str = ""):
    """打印最新一条未完成 task + 简要统计。"""
    tasks = plan.get("tasks", [])

    if not tasks:
        print("📋 plan.json 中无 task。")
        return

    # 找第一个 done=false 的 task
    next_task = None
    next_idx = -1
    for i, t in enumerate(tasks):
        if not t.get("done"):
            next_task = t
            next_idx = i
            break

    done_count = sum(1 for t in tasks if t.get("done"))
    total_cycles = sum(t.get("cycles", 0) for t in tasks)

    print(f"📋 plan 概览: {len(tasks)} tasks | ✅ {done_count} 完成 | ⏱ {total_cycles} 周期")
    print()

    if next_task is None:
        print("🎉 所有 task 已完成！")
        return

    # 打印当前未完成 task
    print(f"▶️  当前 task [#{next_idx + 1}]:")
    print(f"   任务: {next_task.get('desc', '?')}")
    print(f"   验收: {next_task.get('acceptance', '?')}")
    print(f"   周期: {next_task.get('cycles', 0)} 次")
    note = next_task.get("note", "")
    if note:
        print(f"   备注: {note}")
    print()

    # 其他未完成提示
    remaining = len(tasks) - done_count
    if remaining > 1:
        print(f"   还有 {remaining - 1} 个未完成 task 在后面。")

    # 总述简报
    briefing = plan.get("briefing", "")
    if briefing:
        print(f"📌 {briefing}")


# ── 主流程 ────────────────────────────────────────────────────────


def main():
    if len(sys.argv) < 3 or len(sys.argv) > 3:
        print(__doc__.strip())
        sys.exit(_EXIT_OK)

    path = sys.argv[1]
    mode = sys.argv[2]

    if mode not in ("validate", "show-next"):
        _err(f"未知模式: {mode}。支持: validate, show-next")

    if not os.path.exists(path):
        if mode == "validate":
            _err(f"文件不存在: {path}")
        else:
            # show-next 不报错，直接说无计划
            print(f"📋 plan.json 不存在: {path}")
            print("尚无计划书。")
            sys.exit(_EXIT_OK)

    try:
        with open(path) as f:
            plan = json.load(f)
    except json.JSONDecodeError as e:
        _err(f"JSON 解析失败: {e}")
    except PermissionError:
        _err(f"权限不足: {path}")

    if mode == "validate":
        ok = validate(plan, path)
        sys.exit(_EXIT_OK if ok else _EXIT_ERR)
    elif mode == "show-next":
        show_next(plan, path)
        sys.exit(_EXIT_OK)


if __name__ == "__main__":
    main()
