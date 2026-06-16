#!/usr/bin/env python3
"""
bb_plan.py — 计划书工具

三种用法：

  1. 格式检查
     bb_plan.py <plan.json> validate
     返回 0（格式正确，打印结构性摘要）或 1（格式错误，打印错误原因）

  2. 展示状态
     bb_plan.py <plan.json> show-next
     展示总述 + 最新一条未完成 task + 简要统计。
     如果无未完成 task 或 plan 不存在，打印合适消息并返回 0。

  3. 更新 task
     bb_plan.py <plan.json> update --index=N [--done=true|false] [--note="..."]
     更新指定编号的 task。不会自动触发 cycles++。
"""

import argparse
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
_REQUIRED_TASK_FIELDS = {"index", "desc", "acceptance", "done", "cycles", "note"}


# ── IO ──────────────────────────────────────────────────────────────


def _read_plan(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        _err(f"JSON 解析失败: {e}")
    except PermissionError:
        _err(f"权限不足: {path}")
    return {}


def _write_plan(path: str, plan: dict):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
    except (OSError, PermissionError) as e:
        _err(f"写入失败: {e}")


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

    if not isinstance(task, dict):
        errors.append(f"tasks[{idx}]: 应为 object，实际为 {type(task).__name__}")
        return errors

    missing = _REQUIRED_TASK_FIELDS - set(task.keys())
    extra = set(task.keys()) - _REQUIRED_TASK_FIELDS
    if missing:
        errors.append(f"tasks[{idx}]: 缺少字段 {sorted(missing)}")
    if extra:
        print(f"⚠️  tasks[{idx}]: 额外字段 {sorted(extra)}——agent 自定义？", file=sys.stderr)

    for key, max_len in [("desc", _DESC_MAX), ("acceptance", _ACCEPTANCE_MAX), ("note", _NOTE_MAX)]:
        if key in task:
            errors.extend(_validate_string(task[key], f"tasks[{idx}].{key}", max_len))

    if "index" in task:
        if not isinstance(task["index"], int):
            errors.append(f"tasks[{idx}].index: 应为整数")

    if "done" in task:
        if not isinstance(task["done"], bool):
            errors.append(f"tasks[{idx}].done: 应为 boolean，实际为 {type(task['done']).__name__}")

    if "cycles" in task:
        if not isinstance(task["cycles"], int) or task["cycles"] < 0:
            errors.append(f"tasks[{idx}].cycles: 应为非负整数，实际为 {type(task['cycles']).__name__}")

    return errors


def _fixup_indexes(plan: dict):
    """根据 tasks 列表顺序重写 index 字段（从 1 开始）。"""
    tasks = plan.get("tasks", [])
    for i, t in enumerate(tasks, start=1):
        t["index"] = i


def _validate_and_output(plan: dict):
    """验证 plan，输出结构报告。返回是否通过。"""
    errors = []

    if not isinstance(plan, dict):
        _err(f"plan.json: 顶层应为 object，实际为 {type(plan).__name__}")

    missing_top = _REQUIRED_TOP_LEVEL - set(plan.keys())
    if missing_top:
        errors.append(f"缺少顶层字段 {sorted(missing_top)}")

    if "briefing" in plan:
        errors.extend(_validate_string(plan["briefing"], "briefing", _BRIEFING_MAX))

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


# ── show-next ─────────────────────────────────────────────────────


def show_next(plan: dict):
    """打印总述 + 最新一条未完成 task + 简要统计。"""
    tasks = plan.get("tasks", [])

    # 总述总是显示
    briefing = plan.get("briefing", "")
    if briefing:
        print(f"📌 {briefing}")
        print()

    if not tasks:
        print("📋 plan.json 中无 task。")
        return

    done_count = sum(1 for t in tasks if t.get("done"))
    total_cycles = sum(t.get("cycles", 0) for t in tasks)

    print(f"📊 进度: {len(tasks)} tasks | ✅ {done_count} 完成 | ⏱ {total_cycles} 周期")
    print()

    # 找第一个 done=false 的 task
    next_task = None
    next_idx = -1
    for i, t in enumerate(tasks):
        if not t.get("done"):
            next_task = t
            next_idx = i
            break

    if next_task is None:
        print("🎉 所有 task 已完成！")
        return

    idx = next_task.get("index", next_idx + 1)
    print(f"▶️  当前 task [#{idx}]:")
    print(f"   任务: {next_task.get('desc', '?')}")
    print(f"   验收: {next_task.get('acceptance', '?')}")
    print(f"   周期: {next_task.get('cycles', 0)} 次")
    note = next_task.get("note", "")
    if note:
        print(f"   备注: {note}")
    print()

    remaining = len(tasks) - done_count
    if remaining > 1:
        print(f"   还有 {remaining - 1} 个未完成 task 在后面。")


# ── update ──────────────────────────────────────────────────────


def update(plan: dict, path: str, index: int, done: bool | None, note: str | None):
    """更新指定 index 的 task。写回文件。"""
    tasks = plan.get("tasks", [])

    if not tasks:
        _err("plan.json 中无 task，无法更新")

    # 找到匹配 index 的 task
    target = None
    for t in tasks:
        if t.get("index") == index:
            target = t
            break

    if target is None:
        _err(f"未找到 index={index} 的 task")

    changed = []
    if done is not None:
        old = target.get("done")
        if old != done:
            target["done"] = done
            changed.append(f"done: {old} → {done}")
    if note is not None:
        if len(note) > _NOTE_MAX:
            _err(f"note 超出长度限制（{len(note)} > {_NOTE_MAX} 字）")
        target["note"] = note
        changed.append(f"note 已更新")

    if not changed:
        print(f"ℹ️  task #{index} 无变更（参数值与当前值一致）")
        return

    _write_plan(path, plan)
    for c in changed:
        print(f"   • {c}")
    print(f"✅ task #{index} 已更新")


# ── 主流程 ────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="计划书工具 — validate / show-next / update",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("path", help="plan.json 路径")
    parser.add_argument("mode", choices=["validate", "show-next", "update"],
                        help="操作模式")
    parser.add_argument("--index", type=int, default=None,
                        help="task 编号（从 1 开始），仅 update 模式使用")
    parser.add_argument("--done", type=str, default=None,
                        choices=["true", "false"],
                        help="标记完成状态，仅 update 模式使用")
    parser.add_argument("--note", type=str, default=None,
                        help="添加备注，仅 update 模式使用")

    args = parser.parse_args()

    # ── 校验 update 参数 ──
    if args.mode == "update":
        if args.index is None:
            _err("update 模式需要 --index")
        if args.done is None and args.note is None:
            _err("update 模式需要 --done 和/或 --note")

    # ── read ──
    if not os.path.exists(args.path):
        if args.mode == "validate":
            _err(f"文件不存在: {args.path}")
        elif args.mode == "show-next":
            print("📋 尚无计划书。")
            sys.exit(_EXIT_OK)
        elif args.mode == "update":
            _err(f"文件不存在: {args.path}")

    plan = _read_plan(args.path)
    if not plan:
        if args.mode == "validate":
            print("📋 plan.json 为空。")
            sys.exit(_EXIT_OK)
        elif args.mode == "show-next":
            print("📋 尚无计划书。")
            sys.exit(_EXIT_OK)
        elif args.mode == "update":
            _err("plan.json 为空，无法更新")

    # ── 执行 ──
    if args.mode == "validate":
        ok = _validate_and_output(plan)
        sys.exit(_EXIT_OK if ok else _EXIT_ERR)

    elif args.mode == "show-next":
        show_next(plan)
        sys.exit(_EXIT_OK)

    elif args.mode == "update":
        done_val = {"true": True, "false": False, None: None}[args.done]
        update(plan, args.path, args.index, done_val, args.note)
        sys.exit(_EXIT_OK)


if __name__ == "__main__":
    main()
