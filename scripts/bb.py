#!/usr/bin/env python3
"""
Bulletin Board API — 留言板操作核心库

所有留言板操作必须通过此模块，禁止直接编辑 board/* 文件。
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path


def load_config(config_path=None):
    """加载配置，支持环境变量或默认路径覆盖"""
    if config_path is None:
        config_path = os.environ.get(
            "BB_CONFIG",
            str(Path(__file__).resolve().parent.parent / "config.json"),
        )
    with open(config_path) as f:
        return json.load(f)


def _ensure_board_dir(board_path):
    Path(board_path).mkdir(parents=True, exist_ok=True)


def _today_path(board_path):
    return Path(board_path) / f"{datetime.now():%Y-%m-%d}.md"


def post(role, content, config_path=None):
    """
    发一条留言。role = "上级" 或 "worker" 或具体名字。
    自动加时间戳和发言人，追加到今日文件。
    """
    cfg = load_config(config_path)
    board_path = cfg["board_path"]
    _ensure_board_dir(board_path)

    # 确定发言人名字
    if role == "上级":
        speaker = cfg["superior_name"]
    elif role == "worker":
        speaker = cfg["worker_name"]
    else:
        speaker = role  # 允许自定义

    ts = f"{datetime.now():%Y-%m-%d %H:%M}"
    line = f"{ts} [{speaker}] {content}\n"

    today = _today_path(board_path)
    with open(today, "a") as f:
        f.write(line)

    return line.strip()


def recent(lines=20, config_path=None):
    """读最近的留言，默认 20 行"""
    cfg = load_config(config_path)
    board_path = Path(cfg["board_path"])
    today = _today_path(board_path)

    if today.exists():
        result = _tail(today, lines)
        if result:
            return result

    # 今天没消息，找最近一天的
    dates = sorted(board_path.glob("????-??-??.md"), reverse=True)
    for f in dates:
        result = _tail(f, lines)
        if result:
            return result

    return []


def history(start_date, end_date=None, config_path=None):
    """按日期查留言。日期格式 YYYY-MM-DD"""
    cfg = load_config(config_path)
    board_path = Path(cfg["board_path"])

    if end_date is None:
        end_date = start_date

    results = []
    d = start_date
    while d <= end_date:
        fp = board_path / f"{d}.md"
        if fp.exists():
            results.append(f"--- {d} ---")
            results.extend(fp.read_text().rstrip().split("\n"))
        d = _next_date(d)

    return results


def _status_path(board_path):
    return Path(board_path) / "status.json"


def status_get(config_path=None):
    """读 status.json"""
    cfg = load_config(config_path)
    sp = _status_path(cfg["board_path"])
    if not sp.exists():
        return {"status": "idle", "flag": None}
    with open(sp) as f:
        return json.load(f)


def status_set(field, value, config_path=None):
    """写 status.json 某个字段"""
    cfg = load_config(config_path)
    sp = _status_path(cfg["board_path"])
    _ensure_board_dir(cfg["board_path"])
    data = {}
    if sp.exists():
        with open(sp) as f:
            data = json.load(f)
    data[field] = value
    with open(sp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def flag_set(value, config_path=None):
    """上级写入 flag（上层专用）"""
    return status_set("flag", value, config_path)


def flag_clear(config_path=None):
    """上级或 worker 消费后清除 flag"""
    return status_set("flag", None, config_path)


_status_field_keys = {"status", "mission_id", "mission_title", "progress", "blocker", "flag"}


def _tail(path, n):
    """简单的 tail 实现"""
    with open(path) as f:
        lines = f.readlines()
    return [l.rstrip("\n") for l in lines[-n:]]


def _next_date(d):
    y, m, day = d.split("-")
    from datetime import date, timedelta

    dt = date(int(y), int(m), int(day)) + timedelta(days=1)
    return dt.isoformat()


def post_cli():
    """CLI 入口: bb-post"""
    # argv: [script.py, "bb-post", "上级", "内容..."] 或 [script.py, "上级", "内容..."]
    args = [a for a in sys.argv[1:] if not a.startswith("bb-") and not a.endswith(".py")]
    if len(args) < 2:
        print("用法: bb-post <角色> <内容>", file=sys.stderr)
        sys.exit(1)
    role = args[0]
    content = " ".join(args[1:])
    result = post(role, content)
    print(result)


def recent_cli():
    """CLI 入口: bb-recent"""
    args = [a for a in sys.argv[1:] if not a.startswith("bb-") and not a.endswith(".py")]
    n = int(args[0]) if args else 20
    for line in recent(lines=n):
        print(line)


def status_cli():
    """CLI 入口: bb-status"""
    args = [a for a in sys.argv[1:] if not a.startswith("bb-") and not a.endswith(".py")]
    if not args:
        print("用法: bb-status get | set <field> <value> | flag <value> | flag-clear", file=sys.stderr)
        sys.exit(1)
    cmd = args[0]
    if cmd == "get":
        data = status_get()
        print(data.get("status", "IDLE"))
    elif cmd == "set":
        if len(args) < 3:
            print("用法: bb-status set <field> <value>", file=sys.stderr)
            sys.exit(1)
        field = args[1]
        value = " ".join(args[2:])
        result = status_set(field, value)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif cmd == "flag":
        if len(args) < 2:
            print("用法: bb-status flag <内容>", file=sys.stderr)
            sys.exit(1)
        value = " ".join(args[1:])
        result = flag_set(value)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif cmd == "flag-clear":
        result = flag_clear()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"未知子命令: {cmd}", file=sys.stderr)
        sys.exit(1)


def history_cli():
    """CLI 入口: bb-history <日期> [结束日期]"""
    args = [a for a in sys.argv[1:] if not a.startswith("bb-") and not a.endswith(".py")]
    if len(args) < 1:
        print("用法: bb-history <YYYY-MM-DD> [YYYY-MM-DD]", file=sys.stderr)
        sys.exit(1)
    start = args[0]
    end = args[1] if len(args) > 1 else None
    for line in history(start, end):
        print(line)


if __name__ == "__main__":
    # 从 sys.argv 找命令名: 去掉路径前缀，匹配 bb-*
    cmd_candidates = [a for a in sys.argv if a.startswith("bb-")]
    cmd = cmd_candidates[0] if cmd_candidates else Path(sys.argv[0]).stem
    routings = {
        "bb-post": post_cli,
        "bb-recent": recent_cli,
        "bb-history": history_cli,
        "bb-status": status_cli,
    }
    handler = routings.get(cmd)
    if handler:
        handler()
    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(f"支持: {', '.join(routings.keys())}", file=sys.stderr)
        sys.exit(1)
