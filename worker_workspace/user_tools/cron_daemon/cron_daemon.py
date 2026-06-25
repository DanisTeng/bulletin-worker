#!/usr/bin/env python3
"""
cron_daemon — OpenClaw cron 替代品

独立进程，代替有 bug 的 OpenClaw 原生 cron。
特性:
  - 每隔 X 分钟跑一次，任务不重叠
  - 每次创建隔离 session，跑完即焚（刷掉 sessions.json + transcript）
  - 超时自行管理
  - agent 回复存日志文件
  - gateway 崩了就跟着崩（不自动重启）

用法:
  python3 cron_daemon.py --prompt PROMPT.txt --interval 5 --timeout 900
  python3 cron_daemon.py -p PROMPT.txt -i 5 -t 900 -o ./cron_log
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。不依赖环境变量。"""
    p = argparse.ArgumentParser(
        description="cron_daemon — OpenClaw cron 替代品",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "-p", "--prompt",
        type=str,
        default="PROMPT.txt",
        help="提示词文件路径（默认同目录下 PROMPT.txt）",
    )
    p.add_argument(
        "-i", "--interval",
        type=int,
        default=5,
        help="执行间隔（分钟），默认 5",
    )
    p.add_argument(
        "-t", "--timeout",
        type=int,
        default=600,
        help="单次 agent 超时（秒），默认 600",
    )
    p.add_argument(
        "-o", "--output-dir",
        type=str,
        default="./cron_log",
        help="agent 回复日志目录（默认 ./cron_log）",
    )
    p.add_argument(
        "--openclaw-path",
        type=str,
        default="openclaw",
        help="openclaw CLI 路径（默认 PATH 中的 openclaw）",
    )
    p.add_argument(
        "--sessions-json",
        type=str,
        default=None,
        help="sessions.json 路径（默认自动推导）",
    )
    p.add_argument(
        "--state-dir",
        type=str,
        default=None,
        help="~/.openclaw 路径（默认 ~/.openclaw，自动推导 sessions 路径）",
    )
    p.add_argument(
        "--agent-id",
        type=str,
        default="main",
        help="agent ID，默认 main",
    )
    return p.parse_args(argv)


def resolve_sessions_json(args: argparse.Namespace) -> str:
    """推导 sessions.json 路径。"""
    if args.sessions_json:
        return args.sessions_json

    state_dir = args.state_dir or os.path.expanduser("~/.openclaw")
    return os.path.join(state_dir, "agents", args.agent_id, "sessions", "sessions.json")


def load_prompt(prompt_path: str) -> str:
    """读取提示词文件。"""
    path = Path(prompt_path)
    if not path.exists():
        print(f"[FATAL] 提示词文件不存在: {path.resolve()}", file=sys.stderr)
        sys.exit(1)
    return path.read_text(encoding="utf-8").strip()


def run_agent(
    message: str,
    session_id: str,
    timeout: int,
    openclaw_path: str,
) -> tuple[bool, str]:
    """
    执行一次 openclaw agent 调用。
    返回 (成功与否, agent 回复文本)。
    """
    cmd = [
        openclaw_path,
        "agent",
        "--session-id", session_id,
        "--message", message,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        success = result.returncode == 0
        output = result.stdout.strip() or result.stderr.strip()
        if not output:
            output = f"(exit code {result.returncode})"
        return success, output
    except subprocess.TimeoutExpired:
        return False, f"(timeout after {timeout}s)"
    except FileNotFoundError:
        return False, f"(openclaw not found: {openclaw_path})"
    except Exception as e:
        return False, f"(error: {e})"


def cleanse_session(session_id: str, sessions_json_path: str) -> None:
    """
    用完即焚 — 从 sessions.json 删掉对应条目，并删除 transcript 文件。
    静默失败（不打断主流程）。
    """
    # 1. 从 sessions.json 删除条目
    if os.path.exists(sessions_json_path):
        try:
            with open(sessions_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            modified = False
            if isinstance(data, dict):
                if session_id in data:
                    del data[session_id]
                    modified = True
            elif isinstance(data, list):
                before = len(data)
                data = [s for s in data if s.get("key") != session_id]
                modified = len(data) < before

            if modified:
                with open(sessions_json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass  # 静默

    # 2. 删除 transcript 文件
    sessions_dir = os.path.dirname(sessions_json_path)
    for ext in [".jsonl", ".trajectory.json", ".jsonl.lock"]:
        fpath = os.path.join(sessions_dir, f"{session_id}{ext}")
        try:
            if os.path.exists(fpath):
                os.remove(fpath)
        except Exception:
            pass


def save_log(output_dir: str, session_id: str, success: bool, text: str) -> None:
    """将 agent 回复写入日志文件。文件名即时间戳。"""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:-3]
    status = "OK" if success else "FAIL"
    filename = f"{timestamp}_{status}.txt"
    filepath = out_path / filename

    content = (
        f"# cron_daemon log\n"
        f"# triggered_at:  {datetime.now(timezone.utc).isoformat()}\n"
        f"# session:       {session_id}\n"
        f"# success:       {success}\n"
        f"# {'=' * 40}\n"
        f"{text}\n"
    )

    filepath.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    prompt = load_prompt(args.prompt)
    sessions_json_path = resolve_sessions_json(args)

    interval_minutes = args.interval
    interval_seconds = interval_minutes * 60
    timeout_seconds = args.timeout

    print(
        f"[cron_daemon] 启动\n"
        f"  prompt:      {args.prompt}\n"
        f"  interval:    {interval_minutes} 分钟 ({interval_seconds}s)\n"
        f"  timeout:     {timeout_seconds}s\n"
        f"  log dir:     {args.output_dir}\n"
        f"  sessions:    {sessions_json_path}\n"
        f"  openclaw:    {args.openclaw_path}\n"
    )

    round_num = 0
    while True:
        round_num += 1
        session_id = f"cron-{int(time.time())}-{os.getpid()}"

        trigger_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{trigger_time}] [轮次 {round_num}] 开始 ...")

        # 执行 agent turn
        success, output = run_agent(
            message=prompt,
            session_id=session_id,
            timeout=timeout_seconds,
            openclaw_path=args.openclaw_path,
        )

        # 存日志
        save_log(args.output_dir, session_id, success, output)

        # 焚毁 session
        cleanse_session(session_id, sessions_json_path)

        print(f"  session: {session_id} | {'OK' if success else 'FAIL'} | 日志已保存")

        # 等下一轮（从上一次执行完开始算）
        print(f"  等待 {interval_minutes} 分钟 ...\n")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
