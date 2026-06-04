#!/usr/bin/env python3
"""
bb_status.py — JSON 状态域读写工具

提供对 status.json 中任意字段的读/写/置空能力。
默认字段是 status（保持"开机即用"）。

路径无关：status.json 的路径作为唯一参数传入。
环境变量无关：不读取任何环境变量。

用法:
  bb_status.py <路径>                     → 读 status 字段
  bb_status.py <路径> <值>                 → 写 status 字段（输出新值）
  bb_status.py <路径> -f <域>              → 读指定字段
  bb_status.py <路径> -f <域> <值>         → 写指定字段（输出新值）
  bb_status.py <路径> -f <域> --clear      → 置空指定字段（设为 null）
  bb_status.py <路径> --help               → 字段列表等额外信息
  bb_status.py                              → 显示此帮助

示例:
  bb_status.py /tmp/board/status.json                 → IDLE
  bb_status.py /tmp/board/status.json ACTIVE           → ACTIVE
  bb_status.py /tmp/board/status.json -f mission       → (no mission)
  bb_status.py /tmp/board/status.json -f mission "翻译"  → 翻译
  bb_status.py /tmp/board/status.json -f progress --clear  → null
"""

import json
import sys
from pathlib import Path

_EXIT_OK = 0
_EXIT_ERR = 1

_DEFAULT_FIELD = "status"


def _help(doc: str):
    print(doc.strip())
    sys.exit(_EXIT_OK)


def _err(msg: str):
    print(f"❌ {msg}", file=sys.stderr)
    print(f"💡 执行 bb_status.py 查看用法", file=sys.stderr)
    sys.exit(_EXIT_ERR)


# ── JSON 文件 IO ──────────────────────────────────────────────


def _read_json(path: Path) -> dict:
    """读 JSON 文件，文件不存在则返回空字典。"""
    if not path.exists():
        return {}
    if not path.is_file():
        _err(f"路径存在但不是文件: {path}")
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, PermissionError) as e:
        _err(f"读取 JSON 失败: {e}")
    return {}  # unreachable


def _write_json(path: Path, data: dict):
    """原子写入 JSON 文件。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except (OSError, PermissionError) as e:
        _err(f"写入 JSON 失败: {e}")


# ── 参数解析 ──────────────────────────────────────────────────


def _validate_path(raw: str) -> Path:
    """校验并返回路径对象。"""
    if not raw:
        _err("路径不能为空")
    if not raw.endswith(".json"):
        _err(f"路径应以 .json 结尾: {raw}")
    return Path(raw)


def _parse_args(argv: list[str]) -> tuple:
    """
    解析命令行参数，返回 (path, field, value_or_none, show_help_flag)。

    支持的参数风格：
      <path>                       → 读默认字段
      <path> <value>               → 写默认字段
      <path> -f <field>            → 读指定字段
      <path> -f <field> <value>    → 写指定字段
      <path> -f <field> --clear    → 置空指定字段
      <path> --help                → 打印字段信息
    """
    path = None
    field = _DEFAULT_FIELD
    value = None  # None = 读模式
    show_help_flag = False

    i = 1  # skip argv[0] (script name)
    n = len(argv)

    # 参数 1：路径
    if i < n:
        path = _validate_path(argv[i])
        i += 1

    while i < n:
        arg = argv[i]
        if arg == "-f" or arg == "--field":
            i += 1
            if i >= n:
                _err("-f / --field 后需要字段名")
            field = argv[i]
            if not field:
                _err("字段名不能为空")
            i += 1
        elif arg == "--clear":
            value = None  # 显式清除（写入 null）
            i += 1
        elif arg == "--help":
            show_help_flag = True
            i += 1
        elif arg.startswith("-"):
            _err(f"未知选项: {arg}")
        else:
            # 裸值 = 要写入的内容（此时 value 已设置过则为异常）
            if value is not None:
                _err(f"多余的参数: {arg}")
            value = arg
            i += 1

    return path, field, value, show_help_flag


# ── 字段操作 ──────────────────────────────────────────────────


def _field_info(field: str) -> str:
    """返回字段用途说明，用于 --help 信息。"""
    _known = {
        "status": "当前状态 (IDLE/ACTIVE/BUSY)",
        "mission": "当前任务描述",
        "flag": "上级写入的标识/信号",
        "progress": "进度描述",
        "blocker": "阻塞原因",
    }
    return _known.get(field, "自定义字段（无文档）")


# ── 主流程 ────────────────────────────────────────────────────


def main():
    if len(sys.argv) == 1:
        _help(__doc__)

    path, field, new_value, show_help = _parse_args(sys.argv)

    if path is None:
        _err("缺少 status.json 路径参数")

    # ── 读 ──
    data = _read_json(path)

    # ── 写（new_value 非 None 或 --clear 标记） ──
    if new_value is not None or sys.argv[-1] == "--clear":
        if new_value is None:
            # --clear：字段置空
            data[field] = None
        else:
            # 如果写 status 字段，校验合法性
            if field == "status":
                upper = new_value.upper()
                if upper not in {"IDLE", "ACTIVE", "BUSY"}:
                    allowed = ", ".join(["ACTIVE", "BUSY", "IDLE"])
                    _err(f"无效状态: {new_value}，允许: {allowed}")
                new_value = upper
            data[field] = new_value
        _write_json(path, data)

    # ── 输出 ──
    current = data.get(field)

    if show_help:
        print(f"📋 字段: {field}")
        print(f"📝 说明: {_field_info(field)}")
        print(f"📊 当前值: {repr(current)}")
        print()
        _known_keys = [k for k in data.keys() if not k.startswith("_")]
        if _known_keys:
            print(f"📌 status.json 中所有字段 ({len(_known_keys)} 个):")
            for k in _known_keys:
                v = data[k]
                if v is None:
                    v_repr = "null"
                elif isinstance(v, str):
                    v_repr = f'"{v}"'
                    if len(v_repr) > 60:
                        v_repr = v_repr[:57] + '..."'
                else:
                    v_repr = json.dumps(v, ensure_ascii=False)
                print(f"   {k}: {v_repr}")
    else:
        if current is None:
            print("null")
        elif isinstance(current, str):
            print(current)
        else:
            print(json.dumps(current, ensure_ascii=False))


if __name__ == "__main__":
    main()
