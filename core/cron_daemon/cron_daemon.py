#!/usr/bin/env python3
"""
cron_daemon — OpenClaw cron 替代品（v4.5 专用）

独立进程，代替有 bug 的 OpenClaw 原生 cron。
特性:
  - 每隔 X 分钟跑一次，任务不重叠
  - 每次创建隔离 session，跑完即焚（刷掉 sessions.json + transcript）
  - 超时自行管理
  - agent 回复存日志文件
  - gateway 崩了就跟着崩（不自动重启）

用法:
  ./cron_daemon -p PROMPT.md -i 5 -t 600
  ./cron_daemon -p PROMPT.md -i 5 -t 600 -s ../bb-get-status

所有路径/参数由 sh wrapper（run_cron_daemon.sh）自动填充。

特性:
  - 可选前置检查 worker 状态：非 ACTIVE 时自动跳过本轮，不浪费 token
  - 单例锁（fcntl.flock）：同一工作区只能运行一个实例
  - 状态文件 .cron_daemon.status.json 供外部只读监控
"""

import argparse
import fcntl
import json
import os
import select
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── v4.5 默认路径 ────────────────────────────────────────────
# 可通过 --agent 覆盖，适配 agent id 非 main 的用户
_OPENCLAW_PATH = "openclaw"
_DEFAULT_AGENT = "main"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="cron_daemon — OpenClaw v4.5 cron 替代品",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "-p", "--prompt",
        type=str,
        required=True,
        metavar="PATH",
        help="提示词文件路径（必填）",
    )
    p.add_argument(
        "-i", "--interval",
        type=int,
        required=True,
        metavar="MINUTES",
        help="执行间隔，分钟（必填）",
    )
    p.add_argument(
        "-t", "--timeout",
        type=int,
        required=True,
        metavar="SECONDS",
        help="单次 agent 超时，秒（必填）",
    )
    p.add_argument(
        "-o", "--output-dir",
        type=str,
        default="./cron_log",
        metavar="DIR",
        help="agent 回复日志目录（默认 ./cron_log）",
    )
    p.add_argument(
        "-a", "--agent",
        type=str,
        default=_DEFAULT_AGENT,
        metavar="ID",
        help=f"OpenClaw agent ID（默认 {_DEFAULT_AGENT}）",
    )
    p.add_argument(
        "-s", "--bb-status-cmd",
        type=str,
        default=None,
        metavar="PATH",
        help="bb-get-status 可执行路径（不传则不启用前置检查）",
    )
    p.add_argument(
        "--no-skip-if-idle",
        action="store_true",
        help="跳过——即使 worker 非 ACTIVE 也执行 agent turn",
    )
    p.add_argument(
        "--stop-file",
        type=str,
        default=".cron_daemon.stop",
        metavar="PATH",
        help="停止标记文件路径（存在该文件时本轮完成后退出）",
    )
    return p.parse_args(argv)


def _sessions_json_for(agent_id: str) -> str:
    """v4.5 的 sessions.json 路径，按 agent id 推导。"""
    return os.path.expanduser(
        f"~/.openclaw/agents/{agent_id}/sessions/sessions.json"
    )


def _actual_key_for(agent_id: str, session_id: str) -> str:
    """openclaw agent --session-id 在 sessions.json 里实际存的 key。"""
    return f"agent:{agent_id}:explicit:{session_id}"


def load_prompt(prompt_path: str) -> str:
    """读取提示词文件。"""
    path = Path(prompt_path)
    if not path.exists():
        print(f"[FATAL] 提示词文件不存在: {path.resolve()}", file=sys.stderr)
        sys.exit(1)
    return path.read_text(encoding="utf-8").strip()


def run_agent(message: str, session_id: str, timeout: int) -> tuple[bool, str]:
    """执行一次 openclaw agent 调用。返回 (成功与否, agent 回复文本)。"""
    cmd = [
        _OPENCLAW_PATH,
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
        return False, "(openclaw not found in PATH)"
    except Exception as e:
        return False, f"(error: {e})"


def cleanse_session(session_id: str, agent_id: str) -> None:
    """
    用完即焚 — 从 sessions.json 删掉对应条目。
    静默失败（不打断主流程）。
    """
    sessions_json = _sessions_json_for(agent_id)
    actual_key = _actual_key_for(agent_id, session_id)

    if os.path.exists(sessions_json):
        try:
            with open(sessions_json, "r", encoding="utf-8") as f:
                data = json.load(f)

            modified = False
            if isinstance(data, dict):
                if actual_key in data:
                    del data[actual_key]
                    modified = True
            elif isinstance(data, list):
                before = len(data)
                data = [s for s in data if s.get("key") != actual_key]
                modified = len(data) < before

            if modified:
                with open(sessions_json, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # 不删 transcript 文件——文件名是 UUID，不从 session_id 可推


def _write_status(status_path: str, status: str, round_num: int,
                   agent_status: str | None) -> None:
    """写 .cron_daemon.status.json，外部进程可只读读取。"""
    payload = {
        "pid": os.getpid(),
        "daemon_status": status,  # "RUNNING" | "SLEEPING" | "FATAL"
        "round": round_num,
        "latest_round_at": datetime.now(timezone.utc).isoformat(),
        "latest_agent_status": agent_status,  # "OK" | "FAIL" | "SKIP" | None
    }
    tmp = status_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.rename(tmp, status_path)


def _interruptible_sleep(seconds: int, status_path: str = "") -> None:
    """可中断的 sleep——每秒检查 stdin，按 'q' 则退出。"""
    for _ in range(seconds):
        try:
            if select.select([sys.stdin], [], [], 0)[0]:
                ch = sys.stdin.read(1)
                if ch == "q":
                    if status_path:
                        _write_status(status_path, "STOPPED", 0, None)
                    print("\n[cron_daemon] 已停止")
                    sys.exit(0)
        except (InterruptedError, OSError):
            pass
        time.sleep(1)


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


def _acquire_singleton_lock(lock_path: str) -> int:
    """获取单例文件锁。返回 fd，进程退出时 OS 自动释放。"""
    lf = Path(lock_path)
    lf.parent.mkdir(parents=True, exist_ok=True)
    fd = lf.open("w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, BlockingIOError):
        fd.close()
        print(
            f"[FATAL] 已有 cron_daemon 实例运行 (lock: {lock_path})",
            file=sys.stderr,
        )
        sys.exit(1)
    fd.write(f"{os.getpid()}\n")
    fd.flush()
    return fd


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if not args.interval:
        print("[FATAL] 执行间隔不能为 0", file=sys.stderr)
        sys.exit(1)

    prompt = load_prompt(args.prompt)
    agent_id = args.agent

    interval_minutes = args.interval
    interval_seconds = interval_minutes * 60
    timeout_seconds = args.timeout

    status_cmd = Path(args.bb_status_cmd) if args.bb_status_cmd else None

    # ── 单例锁（同一工作区只能跑一个实例）──
    daemon_dir = Path(args.output_dir).parent
    lock_path = daemon_dir / ".cron_daemon.lock"
    _acquire_singleton_lock(str(lock_path))

    # ── 状态文件路径（外部只读监控用）──
    status_path = str(daemon_dir / ".cron_daemon.status.json")
    _write_status(status_path, "RUNNING", 0, None)

    # ── 停止标记文件路径 ──
    stop_path = Path(args.stop_file)
    if not stop_path.is_absolute():
        stop_path = daemon_dir / args.stop_file

    # ── SIGTERM handler（向后兼容，转为创建 stop 文件）──
    def _sigterm_handler(signum, frame):
        print("\n[cron_daemon] 收到 SIGTERM，创建停止标记...")
        try:
            stop_path.touch()
        except OSError:
            pass
    signal.signal(signal.SIGTERM, _sigterm_handler)

    print(
        f"[cron_daemon] 启动\n"
        f"  prompt:      {args.prompt}\n"
        f"  interval:    {interval_minutes} 分钟 ({interval_seconds}s)\n"
        f"  timeout:     {timeout_seconds}s\n"
        f"  log dir:     {args.output_dir}\n"
        f"  sessions:    {_sessions_json_for(agent_id)}\n"
        f"  openclaw 4.5 | agent: {agent_id}\n"
        f"  status cmd:  {status_cmd or '(none)'}{'' if args.no_skip_if_idle else ' | 前置检查开启'}\n"
    )

    round_num = 0
    consecutive_skips = 0
    while True:
        # ── 检查停止标记文件（比信号更可控）──
        if stop_path.exists():
            _write_status(status_path, "STOPPED", round_num, None)
            stop_path.unlink(missing_ok=True)
            print("[cron_daemon] 停止标记文件存在，退出")
            break

        rn, cs = _loop_body(args, status_cmd, status_path, interval_seconds, interval_minutes, prompt, agent_id, timeout_seconds, round_num, consecutive_skips)
        round_num = rn
        consecutive_skips = cs


def _loop_body(
    args, status_cmd, status_path, interval_seconds, interval_minutes,
    prompt, agent_id, timeout_seconds,
    round_num: int, consecutive_skips: int,
) -> tuple[int, int]:
    """执行一轮 cron。返回 (next_round_num, next_consecutive_skips)。"""
    session_id = f"cron-{int(time.time())}-{os.getpid()}"
    trigger_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # ── 前置检查：worker 状态 ────────────────────────────────────
    if status_cmd and not args.no_skip_if_idle:
        try:
            r = subprocess.run(
                [str(status_cmd)],
                capture_output=True, text=True, timeout=10,
            )
            bb_status = r.stdout.strip()
            if bb_status != "ACTIVE":
                consecutive_skips += 1
                if consecutive_skips <= 3:
                    round_num += 1
                    _write_status(status_path, "SLEEPING", round_num, "SKIP")
                    print(f"[{trigger_time}] [轮次 {round_num}] SKIP — worker {bb_status}，非 ACTIVE（无日志写入）")
                # 超过 3 次连续 SKIP：静默，轮次不增，状态不写
                _interruptible_sleep(interval_seconds, status_path)
                return (round_num, consecutive_skips)  # 跳过本轮
        except FileNotFoundError:
            print(f"[WARN] bb-status-cmd 不存在: {status_cmd}，放行执行", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] bb-status-cmd 执行失败: {e}，放行执行", file=sys.stderr)

    # 执行 agent turn（或未启用前置检查）时才到这里
    round_num += 1
    consecutive_skips = 0
    print(f"[{trigger_time}] [轮次 {round_num}] 开始 ...")

    # 执行 agent turn
    success, output = run_agent(
        message=prompt,
        session_id=session_id,
        timeout=timeout_seconds,
    )

    agent_status = "OK" if success else "FAIL"

    # 写状态（agent 刚跑完 → RUNNING）
    _write_status(status_path, "RUNNING", round_num, agent_status)

    # 存日志
    save_log(args.output_dir, session_id, success, output)

    # 焚毁 session
    cleanse_session(session_id, agent_id)

    print(f"  session: {session_id} | {agent_status} | 日志已保存")

    # 等下一轮前标记为 SLEEPING
    _write_status(status_path, "SLEEPING", round_num, agent_status)
    print(f"  等待 {interval_minutes} 分钟 ...\n")
    _interruptible_sleep(interval_seconds, status_path)
    return (round_num, consecutive_skips)


if __name__ == "__main__":
    main()
