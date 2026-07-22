#!/usr/bin/env python3
"""
terminal — Bulletin Worker 交互式终端（打包 ELF）

架构: 基于 Textual，core/terminal/render.py 打包为 ELF。
       sh wrapper (run_terminal.sh) 填充所有路径参数。

功能:

  - 上半屏展示留言板内容（通过 RealtimeBoardManager 异步刷新，0.5 秒一次）
  - 状态栏显示 bb-status（ACTIVE 绿色高亮）
  - 下半屏输入区，Ctrl+D 发留言（通过 bb-leader-post wrapper）
  - Ctrl+C 键退出
  - /exit 命令退出
  - 状态栏显示时钟 + cron daemon 状态

注意: 
  - 留言板高频刷新（bb-index / bb-recent）由 RealtimeBoardManager 在后台协程中处理，
    不阻塞输入事件循环。
  - 其他操作（post / clear / get-status）仍用同步 _exec_wrapper，因为它们
    是低频操作，不值得异步化。
"""

import argparse
import json
import os
import signal
import subprocess  # noqa: E402 — 只在低频操作中使用
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Label, Static, TextArea
from rich.text import Text
from rich.markup import escape

from core.terminal.realtime_board import RealtimeBoardManager

# ── 全局配置 ────────────────────────────────────────────────────

TICK_INTERVAL = 1 / 60  # 60Hz（纯 UI 刷新，不碰 IO）
TICK_PER_REFRESH = 6

_tz_offset: int = 8
_cron_workdir: str | None = None
_tools_dir: str | None = None


# 状态缓存
_last_status: str | None = None


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
    p.add_argument(
        "--tools-dir",
        type=str,
        default=None,
        metavar="PATH",
        help="tools 目录路径（含 bb-leader-post / bb-recent / bb-get-status 等脚本），"
        "不传则禁用留言板交互",
    )
    return p.parse_args(argv)


def _exec_wrapper(script_name: str, *args: str) -> str | None:
    """同步执行一个 tools/ 目录下的 shell wrapper 脚本（低频操作：post / clear / status）。

    高频刷新（bb-index / bb-recent）走 RealtimeBoardManager 的异步路径。
    此函数仅用于低频操作，同步调用无感知影响。
    """
    if not _tools_dir:
        return None
    script = os.path.join(_tools_dir, script_name)
    if not os.path.isfile(script) or not os.access(script, os.X_OK):
        return None
    try:
        result = subprocess.run(
            [script, *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        out = result.stdout
        if out.endswith("\n"):
            out = out[:-1]
        return out
    except (OSError, subprocess.TimeoutExpired):
        return None


# ── cron daemon 状态 ────────────────────────────────────────────


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


def _render_daemon_indicator(workdir: str | None) -> str:
    """根据 status.json + PID 存活状态生成显示字符串。"""
    if not workdir:
        return ""

    st = _read_daemon_status(workdir)
    ds = st.get("daemon_status", "N/A")
    pid = st.get("pid", 0)
    rnd = st.get("round", 0)
    agent_st = st.get("latest_agent_status", None)

    if ds == "N/A":
        return "  |  cron: ❌ 未启动"

    alive = _pid_alive(pid) if pid else False
    if not alive:
        return f"  |  cron: 💀 已退出（最后状态 {ds} 第{rnd}轮）"

    icons = {"RUNNING": "▶️", "STANDBY": "💤", "FATAL": "💥", "STOPPED": "⏹️"}
    icon = icons.get(ds, "❓")
    agent_tag = f" | {agent_st}" if agent_st else ""
    return f"  |  cron: {icon} {ds} 第{rnd}轮{agent_tag}"


def _render_status_indicator(tools_dir: str | None) -> str:
    """调用 bb-get-status 并格式化显示（ACTIVE 绿色高亮）。"""
    global _last_status
    if not tools_dir:
        return ""

    raw = _exec_wrapper("bb-get-status")
    if raw is None:
        _last_status = None
        return "  |  status: ❌"

    status = raw.strip().upper()
    _last_status = status

    icons = {"ACTIVE": "🟢", "BUSY": "🔴", "IDLE": "⚪"}
    icon = icons.get(status, "❓")
    return f"  |  {icon} {status}"


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
        self._need_scroll_bottom = False
        self._tick_index = 0

        # 启动 RealtimeBoardManager（后台异步刷新 board）
        self._board_mgr: RealtimeBoardManager | None = None
        if _tools_dir:
            self._board_mgr = RealtimeBoardManager(
                tools_dir=_tools_dir, interval=1.0, recent_cnt=100
            )
            self._board_mgr.start()

        # 用 set_interval 注册一个纯 UI 帧回调（不再塞 IO）
        self.set_interval(TICK_INTERVAL, self.tick)

        self.query_one("#input", TextArea).focus()
        self._update_board_display()
        self._update_header()

    def on_key(self, event):
        if event.key == "ctrl+d":
            self._on_ctrl_d()
            event.prevent_default()
            event.stop()
        elif event.key == "ctrl+c":
            self.exit()

    # ── 生命周期 ──────────────────────────────────────────────────

    def on_unmount(self):
        """App 卸载时停止后台 board 刷新。"""
        if self._board_mgr is not None:
            self._board_mgr.stop()
            self._board_mgr = None

    # ── tick 回调 ─────────────────────────────────────────────────

    def tick(self):
        """纯 UI tick：每 60Hz 触发一次。

        不再直接读取 subprocess——board 内容由 RealtimeBoardManager 异步维护。
        tick 只负责将已准备好的数据显示到 UI 上。
        """
        self._tick_index += 1
        if self._tick_index >= TICK_PER_REFRESH:
            self._tick_index = 0
            self._update_header()
            self._update_board_display()

    # ── 头部状态栏 ───────────────────────────────────────────────

    def _update_header(self):
        offset = timedelta(hours=_tz_offset)
        now = datetime.now(timezone(offset))
        clock_str = now.strftime("%H:%M:%S")
        daemon_info = _render_daemon_indicator(_cron_workdir)
        status_info = _render_status_indicator(_tools_dir)
        self.query_one("#header_bar", Horizontal).children[1].update(
            f" {clock_str}{status_info}{daemon_info}"
        )

    # ── board 展示 ────────────────────────────────────────────────

    def _update_board_display(self):
        """从 RealtimeBoardManager 取最新内容更新展示框。

        不执行任何 subprocess，不阻塞 event loop。
        """
        if self._board_mgr is None:
            self._need_scroll_bottom = False
            return

        raw = self._board_mgr.last_board_text
        if raw is None:
            # 尚未取到数据（首次启动时）
            return

        lines = raw.split("\n") if raw else []
        if not lines or (len(lines) == 1 and lines[0] == ""):
            self.query_one("#msg_content", Static).update("（暂无留言）")
            self._need_scroll_bottom = False
            return

        self.query_one("#msg_content", Static).update(Text(escape(raw)))

        # Ctrl+D 发帖后的自动滚底
        if self._need_scroll_bottom:
            self.query_one("#msg_area", VerticalScroll).scroll_end(animate=False)
            self._need_scroll_bottom = False

    # ── Ctrl+D 处理 ──────────────────────────────────────────────

    def _on_ctrl_d(self):
        """Ctrl+D 回调：调用 bb-leader-post 将输入内容作为领导留言发布。"""
        textarea = self.query_one("#input", TextArea)
        text = textarea.text.strip()
        if not text:
            return

        if text == "/exit":
            textarea.text = ""
            self.exit()
            return

        if text == "/clear":
            textarea.text = ""
            if _tools_dir:
                _exec_wrapper("bb-board-clear")
            # 重置 board 管理器的 index 缓存，下次刷新立即拉新内容
            if self._board_mgr is not None:
                self._board_mgr.reset_cache()
                self._board_mgr.request_refresh()
            self._need_scroll_bottom = True
            return

        # 通过 bb-leader-post wrapper 发留言（含自动设为 ACTIVE）
        if _tools_dir:
            _exec_wrapper("bb-leader-post", text)

        # 清空输入区
        textarea.text = ""

        # 通知 board manager 立即刷新 + 自动滚底
        if self._board_mgr is not None:
            self._board_mgr.request_refresh()
        self._need_scroll_bottom = True


def main(argv: list[str] | None = None):
    global _tz_offset, _cron_workdir, _tools_dir, _last_status
    args = parse_args(argv)
    _tz_offset = args.tz_offset
    _cron_workdir = args.cron_workdir
    _tools_dir = args.tools_dir
    _last_status = None

    app = Terminal()
    signal.signal(signal.SIGINT, lambda s, f: app.exit())
    signal.signal(signal.SIGTERM, lambda s, f: app.exit())
    app.run()


if __name__ == "__main__":
    main()
