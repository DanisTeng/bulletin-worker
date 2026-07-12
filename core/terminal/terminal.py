#!/usr/bin/env python3
"""
terminal — Bulletin Worker 交互式终端（打包 ELF）

架构: 基于 Textual，core/terminal/render.py 打包为 ELF。
       sh wrapper (run_terminal.sh) 填充所有路径参数。
"""

import argparse
import json
import os
import signal
import sys
import textwrap
from datetime import datetime, timezone, timedelta
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Label, Static, TextArea

TICK_INTERVAL = 1 / 20  # 20Hz

_tz_offset: int = 8
_cron_workdir: str | None = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="terminal — Bulletin Worker 交互式终端",
    )
    p.add_argument(
        "--tz-offset",
        type=int,
        default=8,
        metavar="HOURS",
        help="UTC 偏移小时数，如 +8（东八区），默认 8",
    )
    p.add_argument(
        "--cron-workdir",
        type=str,
        default=None,
        metavar="PATH",
        help="cron_daemon 工作目录（含 .cron_daemon.status.json），不传则不显示 daemon 状态",
    )
    return p.parse_args(argv)


def _read_daemon_status(workdir: str) -> dict:
    """读取 .cron_daemon.status.json，返回 {'daemon_status': ..., 'pid': ...}。"""
    status_path = Path(workdir) / ".cron_daemon.status.json"
    if not status_path.exists():
        return {"daemon_status": "N/A"}
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"daemon_status": "N/A"}
        return data
    except (json.JSONDecodeError, OSError):
        return {"daemon_status": "N/A"}


def _pid_alive(pid: int) -> bool:
    """检查 PID 是否存活（通过 /proc）。"""
    if pid <= 0:
        return False
    try:
        return os.path.exists(f"/proc/{pid}")
    except OSError:
        return False


def _render_daemon_indicator(status_path: str | None) -> str:
    """根据 status.json + PID 存活状态生成显示字符串。"""
    if not status_path:
        return ""

    st = _read_daemon_status(status_path)
    ds = st.get("daemon_status", "N/A")
    pid = st.get("pid", 0)
    rnd = st.get("round", 0)
    agent_st = st.get("latest_agent_status", None)

    if ds == "N/A":
        # status.json 不存在或不可读 → daemon 未启动
        return "  |  cron: ❌ 未启动"

    alive = _pid_alive(pid) if pid else False
    if not alive:
        return f"  |  cron: 💀 已退出（最后状态 {ds} 第{rnd}轮）"

    # daemon 进程活着
    icons = {"RUNNING": "▶️", "STANDBY": "💤", "FATAL": "💥", "STOPPED": "⏹️"}
    icon = icons.get(ds, "❓")
    agent_tag = f" | {agent_st}" if agent_st else ""
    return f"  |  cron: {icon} {ds} 第{rnd}轮{agent_tag}"


class Terminal(App):
    CSS = """
    Screen { layout: vertical; }
    #header_bar { height: 1; background: $primary; color: $text; padding: 0 1; }
    #msg_area { height: 1fr; border: solid $secondary; margin: 1 1 0 1; padding: 0 1; overflow-y: scroll; scrollbar-gutter: stable; }
    #msg_content { width: 100%; }
    TextArea { height: 5; margin: 1 1; border: solid $accent; }
    Footer { height: 1; }
    """

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Label("terminal  |  bulletin worker"),
            Label("", id="clock"),
            id="header_bar",
        )
        yield VerticalScroll(Static("", id="msg_content"), id="msg_area")
        yield TextArea("", id="input", soft_wrap=True)
        yield Footer()

    def on_mount(self):
        self._messages: list[str] = []
        self.set_interval(TICK_INTERVAL, self.tick)
        self.query_one("#input", TextArea).focus()
        self._update_header()

    def on_key(self, event):
        if event.key == "ctrl+d":
            self._on_ctrl_d()
            event.prevent_default()
            event.stop()
        elif event.key == "ctrl+c":
            self.exit()

    def tick(self):
        self._update_header()

    def _update_header(self):
        offset = timedelta(hours=_tz_offset)
        now = datetime.now(timezone(offset))
        clock_str = now.strftime("%H:%M:%S")
        daemon_info = _render_daemon_indicator(_cron_workdir)
        self.query_one("#header_bar", Horizontal).children[1].update(
            f"{clock_str}{daemon_info}"
        )

    def _on_ctrl_d(self):
        """Ctrl+D 回调（预留——后续做 board 交互）。

        /exit  — 退出 terminal。
        """
        textarea = self.query_one("#input", TextArea)
        text = textarea.text.strip()
        if not text:
            return

        if text == "/exit":
            textarea.text = ""
            self.exit()
            return

        # TODO: 实现 leader post / 命令体系
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = f"{ts}  [预留]  "
        display = prefix + textwrap.indent(text, " " * len(prefix)).lstrip()
        self._messages.append(display)
        self.query_one("#msg_content", Static).update("\n".join(self._messages))
        self.query_one("#msg_area", VerticalScroll).scroll_end(animate=False)
        textarea.text = ""


def main(argv: list[str] | None = None):
    global _tz_offset, _cron_workdir
    args = parse_args(argv)
    _tz_offset = args.tz_offset
    _cron_workdir = args.cron_workdir

    app = Terminal()
    signal.signal(signal.SIGINT, lambda s, f: app.exit())
    signal.signal(signal.SIGTERM, lambda s, f: app.exit())
    app.run()


if __name__ == "__main__":
    main()
