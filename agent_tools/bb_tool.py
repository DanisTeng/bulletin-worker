#!/usr/bin/env python3
"""
bb_tool.py — Agent Tools for Bulletin Worker

可独立运行的命令行工具集，供 agent 在工作区中直接使用。
所有配置通过 --board / --worker / --leader 参数传入（渲染器注入）。

用法:
  bb_tool.py --board <dir> --worker <name> --leader <name> <命令> [参数...]

命令:
  status                   → 输出状态: IDLE / ACTIVE / BUSY
  status set <v>           → 设置状态
  mission                  → 输出当前任务
  mission set <text>       → 设置任务
  recent [N]               → 最近 N 行留言（默认 20）
  post <role> <text>       → 发留言 (角色)
  worker-post <text>       → 以 worker 身份留言
  leader-post <text>       → 以领导身份留言
  get-mission              → 同 mission
  set-mission <text>       → 同 mission set
  set-active               → 状态设为 ACTIVE
  set-busy                 → 状态设为 BUSY
  set-idle                 → 状态设为 IDLE
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path


def _die(msg):
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(1)


# ── 全局配置（由 parse_global_opts 填充） ──────────────────────

class _Cfg:
    def __init__(self):
        self.board_dir: Path = Path()
        self.worker_name: str = ""
        self.leader_name: str = ""

    def require_board(self) -> Path:
        assert self.board_dir and self.board_dir.exists(), f"board_dir 未配置或不存在: {self.board_dir}"
        return self.board_dir

    def require_worker(self) -> str:
        assert self.worker_name, "worker_name 未配置"
        return self.worker_name

    def require_leader(self) -> str:
        assert self.leader_name, "leader_name 未配置"
        return self.leader_name


_cfg = _Cfg()


def parse_global_opts(argv: list[str]) -> list[str]:
    """从 argv 中解析 --board / --worker / --leader，返回剩余参数。"""
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--board" and i + 1 < len(argv):
            _cfg.board_dir = Path(argv[i + 1])
            i += 2
        elif argv[i] == "--worker" and i + 1 < len(argv):
            _cfg.worker_name = argv[i + 1]
            i += 2
        elif argv[i] == "--leader" and i + 1 < len(argv):
            _cfg.leader_name = argv[i + 1]
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    return rest


# ── 状态操作 ───────────────────────────────────────────────────

def _status_path() -> Path:
    return _cfg.require_board() / "status.json"


def _load_status() -> dict:
    p = _status_path()
    if not p.exists():
        return {"status": "IDLE"}
    with open(p) as f:
        return json.load(f)


def _write_status(data: dict):
    p = _status_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def cmd_status(args: list):
    """status [get|set <v>]"""
    if not args or args[0] == "get":
        print(_load_status().get("status", "IDLE"))
    elif args[0] == "set":
        assert len(args) >= 2, "用法: status set <值>"
        data = _load_status()
        data["status"] = args[1]
        _write_status(data)
    else:
        _die(f"未知子命令: {args[0]}")


# ── 任务操作 ───────────────────────────────────────────────────

def cmd_mission(args: list):
    """mission [set <text>]"""
    data = _load_status()
    if not args or args[0] == "get":
        m = data.get("mission")
        if m:
            print(m if isinstance(m, str) else json.dumps(m, ensure_ascii=False))
        else:
            print("(no mission)")
    elif args[0] == "set":
        text = " ".join(args[1:])
        data["mission"] = text
        _write_status(data)
    else:
        _die(f"未知子命令: {args[0]}")


# ── 留言板读写 ─────────────────────────────────────────────────

def cmd_recent(args: list):
    """recent [N]"""
    n = 20
    if args:
        assert args[0].isdigit(), f"参数不是数字: {args[0]}"
        n = int(args[0])
    board = _cfg.require_board()

    all_lines: list[str] = []
    dates = sorted(board.glob("????-??-??.md"), reverse=True)
    for fp in dates:
        with open(fp) as f:
            entries = [l.rstrip("\n") for l in f if l.rstrip("\n")]
        content = [l for l in entries if not l.startswith("# ")]
        all_lines = content + all_lines

    for line in all_lines[-n:]:
        print(line)


def _post(speaker: str, content: str):
    """内部留言写入"""
    board = _cfg.require_board()
    board.mkdir(parents=True, exist_ok=True)
    today = board / f"{datetime.now():%Y-%m-%d}.md"

    ts = f"{datetime.now():%Y-%m-%d %H:%M}"
    lines = content.split("\n")
    prefix = f"{ts} [{speaker}] "
    indent = " " * len(prefix)

    with open(today, "a") as f:
        for i, line in enumerate(lines):
            f.write(f"{prefix}{line}\n" if i == 0 else f"{indent}{line}\n")

    print(f"{prefix}{lines[0]}")


def cmd_post(args: list):
    """post <role> <text>"""
    assert len(args) >= 2, "用法: post <角色> <内容>"
    role = args[0]
    text = " ".join(args[1:])
    _post(role, text)


def cmd_worker_post(args: list):
    _post(_cfg.require_worker(), " ".join(args))


def cmd_leader_post(args: list):
    _post(_cfg.require_leader(), " ".join(args))


# ── 快捷别名 ───────────────────────────────────────────────────

_ALIASES = {
    "get-mission":     (cmd_mission, ["get"]),
    "set-mission":     (cmd_mission, ["set"]),
    "set-active":      (cmd_status, ["set", "ACTIVE"]),
    "set-busy":        (cmd_status, ["set", "BUSY"]),
    "set-idle":        (cmd_status, ["set", "IDLE"]),
    "worker-post":     (cmd_worker_post, []),
    "leader-post":     (cmd_leader_post, []),
    "post":            (cmd_post, []),
    "status":          (cmd_status, []),
    "mission":         (cmd_mission, []),
    "recent":          (cmd_recent, []),
}


def main():
    rest = parse_global_opts(sys.argv[1:])

    if not rest:
        print("用法: bb_tool.py --board <dir> --worker <name> --leader <name> <命令> [参数...]\n", file=sys.stderr)
        print("命令:", file=sys.stderr)
        for name in _ALIASES:
            print(f"  {name}", file=sys.stderr)
        sys.exit(1)

    cmd = rest[0]
    args = rest[1:]

    if cmd not in _ALIASES:
        _die(f"未知命令: {cmd}")

    # extra_args 用于别名注入前置参数，如 set-active → cmd_status(["set","ACTIVE"])
    handler, extra_args = _ALIASES[cmd]
    handler(extra_args + args)


if __name__ == "__main__":
    main()
