#!/usr/bin/env python3
"""
cron_daemon — OpenClaw cron 替代品

独立进程，代替有 bug 的 OpenClaw 原生 cron（原为 v4.5 设计）。
特性:
  - 每隔 X 秒跑一次，任务不重叠
  - 每次创建隔离 session，跑完通过 Gateway RPC 安全清理
  - 超时自行管理
  - agent 回复存日志文件
  - gateway 崩了就跟着崩（不自动重启）
  - 启动时自动清理同前缀 session 残留

用法:
  ./cron_daemon -p PROMPT.md -i 300 -t 600
  ./cron_daemon -p PROMPT.md -i 300 -t 600 -s ../bb-get-status

所有路径/参数由 sh wrapper（run_cron_daemon.sh）自动填充。

特性:
  - 可选前置检查 worker 状态：非 ACTIVE 时自动跳过本轮，不浪费 token
  - 单例锁（fcntl.flock）：同一工作区只能运行一个实例
  - 状态文件 .cron_daemon.status.json 供外部只读监控
  - session 前缀 {worker_name}-cron-xxx，启动时扫清同前缀残留
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
_DEFAULT_WORKER = "James"


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
        metavar="SECONDS",
        help="执行间隔，秒（必填）",
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
        "-w", "--worker-name",
        type=str,
        default=_DEFAULT_WORKER,
        metavar="NAME",
        help=f"worker 名称，用于 session 前缀（默认 {_DEFAULT_WORKER}）",
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


def _session_key_for(agent_id: str, session_id: str) -> str:
    """openclaw agent --session-id 对应的 session key。"""
    return f"agent:{agent_id}:explicit:{session_id}"


def _session_key_prefix_for(agent_id: str, worker_name: str) -> str:
    """获取 session key 前缀，用于匹配指定 worker 的所有 session。"""
    return f"agent:{agent_id}:explicit:{worker_name}-"


def _call_gateway(method: str, params: dict, timeout: int = 10) -> dict | None:
    """调用 OpenClaw Gateway RPC，返回解析后的 JSON 或 None。
    
    调试：失败时打 stderr 日志方便远程定位。
    """
    cmd = [
        _OPENCLAW_PATH, "gateway", "call", method,
        "--params", json.dumps(params),
    ]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            print(f"[DEBUG cleanup] gateway call failed rc={r.returncode} stderr={r.stderr[-200:]}", file=sys.stderr)
            return None
        text = r.stdout.strip()
        for prefix in ("{", "["):
            pos = text.find(prefix)
            if pos >= 0:
                result = json.loads(text[pos:])
                print(f"[DEBUG cleanup] {method} ok: {json.dumps(result, ensure_ascii=False, default=str)[:300]}", file=sys.stderr)
                return result
        print(f"[DEBUG cleanup] no JSON in stdout (len={len(r.stdout)}), key={params.get('key','?')}", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        print(f"[DEBUG cleanup] {method} timeout after {timeout}s", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"[DEBUG cleanup] {method} JSON parse error: {e}", file=sys.stderr)
        return None
    except OSError as e:
        print(f"[DEBUG cleanup] {method} OS error: {e}", file=sys.stderr)
        return None


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
    用完即焚 — 通过 Gateway RPC sessions.delete 安全删除单条 session。
    静默失败（不打断主流程）。
    """
    key = _session_key_for(agent_id, session_id)
    print(f"[DEBUG cleanup] cleansing session key={key}", file=sys.stderr)
    result = _call_gateway("sessions.delete", {
        "key": key,
        "agentId": agent_id,
    })
    if result is None:
        print(f"[DEBUG cleanup] ⚠️ sessions.delete returned None for key={key}", file=sys.stderr)


def cleanse_all_by_prefix(agent_id: str, worker_name: str) -> int:
    """
    启动时扫雷：通过 Gateway RPC 清理所有以 {worker_name}- 为前缀的 session。
    返回清理的条目数。
    """
    prefix = _session_key_prefix_for(agent_id, worker_name)
    removed = 0
    try:
        r = subprocess.run(
            [_OPENCLAW_PATH, "sessions", "list", "--json", "--agent", agent_id],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return 0
        data = json.loads(r.stdout)
        for s in data.get("sessions", []):
            key = s.get("key", "")
            if key.startswith(prefix):
                _call_gateway("sessions.delete", {
                    "key": key,
                    "agentId": agent_id,
                })
                removed += 1
    except Exception:
        pass
    return removed


def _write_status(status_path: str, status: str, round_num: int,
                   agent_status: str | None) -> None:
    """写 .cron_daemon.status.json，外部进程可只读读取。"""
    payload = {
        "pid": os.getpid(),
        "daemon_status": status,  # "RUNNING" | "STANDBY" | "FATAL"
        "round": round_num,
        "latest_round_at": datetime.now(timezone.utc).isoformat(),
        "latest_agent_status": agent_status,  # "OK" | "FAIL" | "SKIP" | None
    }
    tmp = status_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.rename(tmp, status_path)


def _interruptible_sleep(seconds: int, status_path: str = "", stop_path: Path | None = None) -> None:
    """可中断的 sleep——每秒检查 stdin 或 stop 标记文件。

    按 'q'    → 立即退出。
    stop 文件 → 立即退出（由 _cleanup_lock / _stop_exit 统一处理）。
    """
    for _ in range(seconds):
        try:
            if select.select([sys.stdin], [], [], 0)[0]:
                ch = sys.stdin.read(1)
                if ch == "q":
                    _stop_exit(status_path)
        except (InterruptedError, OSError):
            pass

        # 检查 stop 标记文件（stop.sh 触发后秒退）
        if stop_path and stop_path.exists():
            _stop_exit(status_path)

        time.sleep(1)


_stop_file: Path | None = None


def _stop_exit(status_path: str = ""):
    """写 STOPPED 状态、删 stop 标记、清理锁、退出进程。"""
    if status_path:
        _write_status(status_path, "STOPPED", 0, None)
    if _stop_file:
        try:
            _stop_file.unlink(missing_ok=True)
        except OSError:
            pass
    print("\n[cron_daemon] 已停止")
    _cleanup_lock()
    sys.exit(0)


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


_lock_path: str | None = None


def _cleanup_lock():
    """清理 .cron_daemon.lock 文件。"""
    global _lock_path
    if _lock_path:
        try:
            Path(_lock_path).unlink(missing_ok=True)
        except OSError:
            pass


def _acquire_singleton_lock(lock_path: str) -> int:
    """获取单例文件锁。返回 fd，进程退出时 OS 自动释放。"""
    global _lock_path
    _lock_path = lock_path
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

    interval_seconds = args.interval
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
    global _stop_file
    _stop_file = stop_path

    # ── 启动扫雷：清理同前缀 session 残留 ──
    cleaned = cleanse_all_by_prefix(agent_id, args.worker_name)
    if cleaned:
        print(f"[cron_daemon] 清理 {cleaned} 个前代 {args.worker_name} session 残留")

    print(
        f"[cron_daemon] 启动\n"
        f"  prompt:      {args.prompt}\n"
        f"  interval:    {interval_seconds} 秒\n"
        f"  timeout:     {timeout_seconds}s\n"
        f"  worker:      {args.worker_name}\n"
        f"  log dir:     {args.output_dir}\n"
        f"  gateway:     sessions.delete RPC\n"
        f"  agent:       {agent_id}\n"
        f"  status cmd:  {status_cmd or '(none)'}{'' if args.no_skip_if_idle else ' | 前置检查开启'}\n"
    )

    round_num = 0
    while True:
        # ── 检查停止标记文件（比信号更可控）──
        if stop_path.exists():
            _write_status(status_path, "STOPPED", round_num, None)
            stop_path.unlink(missing_ok=True)
            print("[cron_daemon] 停止标记文件存在，退出")
            break

        round_num = _loop_body(
            args, status_cmd, status_path, stop_path,
            interval_seconds, prompt, agent_id, timeout_seconds, round_num,
        )


def _call_bb_set_status(bb_status_script: str, value: str) -> bool:
    """调用 bb-status set 写入状态值。成功返回 True。"""
    try:
        r = subprocess.run(
            [str(bb_status_script), "set", "status", value],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            print(f"[WARN] bb-status set 失败: {r.stderr.strip()}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"[WARN] bb-status set 异常: {e}", file=sys.stderr)
        return False


def _loop_body(
    args, status_cmd, status_path, stop_path,
    interval_seconds,
    prompt, agent_id, timeout_seconds,
    round_num: int,
) -> int:
    """执行一轮 cron。返回 next_round_num。

    简化逻辑：初始检查发现是 IDLE 时直接跳过本轮，不增加轮次、不输出。
    """
    session_id = f"{args.worker_name}-cron-{int(time.time())}-{os.getpid()}"
    trigger_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # 一进入循环就标记 RUNNING，不等 run_agent 跑完
    _write_status(status_path, "RUNNING", round_num, None)

    # ── 前置检查：worker 状态 ────────────────────────────────────
    if status_cmd and not args.no_skip_if_idle:
        try:
            r = subprocess.run(
                [str(status_cmd)],
                capture_output=True, text=True, timeout=10,
            )
            bb_status = r.stdout.strip()
            if bb_status == "IDLE":
                # IDLE → 跳过本轮
                _write_status(status_path, "STANDBY", round_num, "SKIP")
                _interruptible_sleep(interval_seconds, status_path, stop_path)
                return round_num  # 跳过本轮，轮次不变
            if bb_status == "BUSY":
                # BUSY → 上一轮异常退出，cron_daemon 直接修回 ACTIVE
                print(f"[cron_daemon] 检测到 BUSY（上一轮异常退出），自动置为 ACTIVE")
                _call_bb_set_status(str(status_cmd.resolve().parent / "bb-status"), "ACTIVE")
        except FileNotFoundError:
            print(f"[WARN] bb-status-cmd 不存在: {status_cmd}，放行执行", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] bb-status-cmd 执行失败: {e}，放行执行", file=sys.stderr)

    # 执行 agent turn（或未启用前置检查）时才到这里
    round_num += 1
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

    # 等下一轮前标记为 STANDBY（等待阶段，非 sleeping）
    _write_status(status_path, "STANDBY", round_num, agent_status)
    print(f"  等待 {interval_seconds} 秒 ...\n")
    _interruptible_sleep(interval_seconds, status_path, stop_path)
    return round_num


if __name__ == "__main__":
    main()
