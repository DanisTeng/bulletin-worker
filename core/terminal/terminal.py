#!/usr/bin/env python3
"""
terminal — Bulletin Worker 交互式终端（打包 ELF）

职责:
  - 参数化: 接收 --daemon-dir 参数（渲染时由 sh wrapper 从 config.json 填入）
  - 自动拉起 cron_daemon 单例（检查 .cron_daemon.lock + pid）
  - 20Hz 读取 .cron_daemon.status.json 显示 daemon 状态
  - 预留 board 交互输入区

架构: 基于 Textual，core/terminal/render.py 打包为 ELF。
       sh wrapper (run_terminal.sh) 填充所有路径参数。

本身不推导任何路径。所有路径由渲染脚本在部署时确定。
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Label, Static, TextArea

TICK_INTERVAL = 1 / 20  # 20Hz


# ═══════════════════════════════════════════════════════
#  命令行参数
# ═══════════════════════════════════════════════════════

_args: argparse.Namespace | None = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="terminal — Bulletin Worker 交互式终端",
    )

    p.add_argument(
        "-d", "--daemon-dir",
        type=str,
        required=True,
        metavar="DIR",
        help="cron_daemon 部署目录（含 cron_daemon ELF、.cron_daemon.status.json）",
    )
    p.add_argument(
        "--timezone",
        type=str,
        default="Asia/Shanghai",
        metavar="TZ",
        help="显示时区（IANA 格式，如 Asia/Hong_Kong），默认 Asia/Shanghai",
    )

    return p.parse_args(argv)


# ═══════════════════════════════════════════════════════
#  cron_daemon 管理
# ═══════════════════════════════════════════════════════


_daemon_dir: Path | None = None
_status_json: Path | None = None
_tz: ZoneInfo = ZoneInfo("Asia/Shanghai")


def _ensure_daemon(daemon_dir: Path) -> bool:
    """确保 cron_daemon 单例在运行。"""
    elf = daemon_dir / "cron_daemon"
    wrapper = daemon_dir / "run_cron_daemon.sh"
    lock = daemon_dir / ".cron_daemon.lock"

    # 检查已有 daemon
    if lock.exists():
        try:
            pid_str = lock.read_text().strip()
            if pid_str:
                pid = int(pid_str)
                try:
                    os.kill(pid, 0)
                    return True  # daemon 活着
                except (OSError, ProcessLookupError):
                    pass  # daemon 死了，锁残留
        except (ValueError, OSError):
            pass

    # 启动新 daemon
    if not (elf.exists() or wrapper.exists()):
        print(f"[FATAL] 未找到 cron_daemon: {elf}", file=sys.stderr)
        return False

    cmd = [str(wrapper)] if wrapper.exists() else [str(elf)]
    try:
        subprocess.Popen(
            cmd,
            cwd=str(daemon_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(1)  # 等 daemon 初始化 + 写状态文件
        return True
    except Exception as e:
        print(f"[FATAL] 启动 cron_daemon 失败: {e}", file=sys.stderr)
        return False


def _stop_daemon():
    """停止 cron_daemon 进程。

    创建 .cron_daemon.stop 标记文件。daemon 每轮循环入口
    检查该文件，存在则等本轮完成后优雅退出。
    """
    if _daemon_dir is None:
        return
    stop_file = _daemon_dir / ".cron_daemon.stop"
    try:
        stop_file.touch()
        print(f"  → 已创建停止标记: {stop_file}")
    except OSError:
        pass


def _read_daemon_status() -> dict | None:
    """读取 .cron_daemon.status.json。"""
    if _status_json and _status_json.exists():
        try:
            return json.loads(_status_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return None


# ═══════════════════════════════════════════════════════
#  TUI App
# ═══════════════════════════════════════════════════════


class Terminal(App):
    CSS = """
    Screen { layout: vertical; }
    #header_bar { height: 1; background: $primary; color: $text; padding: 0 1; }
    #daemon_status_area { height: 3; border: solid $secondary; margin: 1 1 0 1; padding: 0 1; }
    #msg_area { height: 1fr; border: solid $secondary; margin: 0 1 0 1; padding: 0 1; overflow-y: scroll; scrollbar-gutter: stable; }
    #msg_content { width: 100%; }
    TextArea { height: 5; margin: 1 1; border: solid $accent; }
    Footer { height: 1; }
    """

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Label("terminal  |  bulletin worker"),
            Label("", id="clock"),
            Label("  |  Ctrl+D 提交  |  Ctrl+C 退出"),
            id="header_bar",
        )
        yield Static("", id="daemon_status_area")
        yield VerticalScroll(Static("", id="msg_content"), id="msg_area")
        yield TextArea("", id="input", soft_wrap=True)
        yield Footer()

    def on_mount(self):
        daemon_ok = _ensure_daemon(_daemon_dir)
        self._daemon_ok = daemon_ok
        self._daemon_warned = not daemon_ok
        self._messages: list[str] = []
        self.set_interval(TICK_INTERVAL, self.tick)
        self.query_one("#input", TextArea).focus()
        self._update_clock()
        self._update_status()

    def on_key(self, event):
        if event.key == "ctrl+d":
            self._on_ctrl_d()
            event.prevent_default()
            event.stop()
        elif event.key == "ctrl+c":
            self.exit()

    def tick(self):
        self._update_clock()
        self._update_status()

    def _update_clock(self):
        self.query_one("#clock", Label).update(
            datetime.now(_tz).strftime("%H:%M:%S")
        )

    def _update_status(self):
        status = _read_daemon_status()
        if status is None:
            if not self._daemon_warned:
                self.query_one("#daemon_status_area", Static).update(
                    "[WARN] cron_daemon 状态文件未就绪"
                )
                self._daemon_warned = True
            return

        daemon_st = status.get("daemon_status", "?")
        round_num = status.get("round", 0)
        agent_st = status.get("latest_agent_status", "—")
        latest_ts = status.get("latest_round_at", "")
        pid = status.get("pid", 0)

        if latest_ts:
            try:
                dt_utc = datetime.fromisoformat(latest_ts)
                if dt_utc.tzinfo is None:
                    dt_utc = dt_utc.replace(tzinfo=ZoneInfo("UTC"))
                dt_local = dt_utc.astimezone(_tz)
                latest_ts = dt_local.strftime("%H:%M:%S")
            except ValueError:
                pass

        icon = "🟢" if daemon_st in ("RUNNING", "SLEEPING") else "🔴"
        self.query_one("#daemon_status_area", Static).update(
            f"  {icon} daemon: {daemon_st}  "
            f"|  round: {round_num}  "
            f"|  latest: {agent_st}  "
            f"|  at: {latest_ts}  "
            f"|  pid: {pid}"
        )

    def _on_ctrl_d(self):
        """Ctrl+D 回调（预留——后续做 board 交互）。"""
        textarea = self.query_one("#input", TextArea)
        text = textarea.text.strip()
        if not text:
            return

        # TODO: 实现 leader post / 命令体系
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = f"{ts}  [预留]  "
        display = prefix + textwrap.indent(text, " " * len(prefix)).lstrip()
        self._messages.append(display)
        self.query_one("#msg_content", Static).update("\n".join(self._messages))
        self.query_one("#msg_area", VerticalScroll).scroll_end(animate=False)
        textarea.text = ""


# ═══════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════


def main(argv: list[str] | None = None):
    global _args, _daemon_dir, _status_json

    _args = parse_args(argv)
    _daemon_dir = Path(_args.daemon_dir).resolve()
    _status_json = _daemon_dir / ".cron_daemon.status.json"
    global _tz
    _tz = ZoneInfo(_args.timezone)

    app = Terminal()

    sigs = (signal.SIGINT, signal.SIGTERM)
    for sig in sigs:
        signal.signal(sig, lambda s, f: (_stop_daemon(), app.exit()))

    app.run()


if __name__ == "__main__":
    main()
