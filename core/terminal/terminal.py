#!/usr/bin/env python3
"""
terminal — Bulletin Worker 交互式终端（打包 ELF）

架构: 基于 Textual，core/terminal/render.py 打包为 ELF。
       sh wrapper (run_terminal.sh) 填充所有路径参数。

功能:
  - 上半屏展示留言板内容（最新 100 条，20Hz 刷新，加速逻辑）
  - 状态栏显示 bb-status（ACTIVE 绿色高亮）
  - 下半屏输入区，Ctrl+D 发留言（通过 bb-leader-post wrapper）
  - Ctrl+C 键退出
  - /exit 命令退出
  - 状态栏显示时钟 + cron daemon 状态

注意: 所有留言板操作通过 shell wrapper 脚本（bb-leader-post / bb-recent / bb-get-status）
      执行，确保单点维护 —— 这些脚本由 core/agent_tools/render.py 统一部署。
"""

import argparse
import hashlib
import json
import os
import signal
import subprocess  # noqa: E402 — 唯一的外部操作方式
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Label, Static, TextArea
from rich.text import Text
from rich.markup import escape

# ── 全局配置 ────────────────────────────────────────────────────

TICK_INTERVAL = 1 / 60  # 60Hz
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
    """执行一个 tools/ 目录下的 shell wrapper 脚本，返回 stdout（去掉尾部换行）。

    通过 subprocess 而非 os.system，避免 shell 注入问题。
    失败时返回 None（静默）。
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
        self._tick_index = self._tick_index + 1
        if self._tick_index >= TICK_PER_REFRESH:
            self._tick_index = 0
            self._update_header()
            self._refresh_board()

    def _update_header(self):
        offset = timedelta(hours=_tz_offset)
        now = datetime.now(timezone(offset))
        clock_str = now.strftime("%H:%M:%S")
        daemon_info = _render_daemon_indicator(_cron_workdir)
        status_info = _render_status_indicator(_tools_dir)
        # 状态行 = 时钟 + status + cron（Textual 不支持 ANSI，状态用 emoji 代替）
        self.query_one("#header_bar", Horizontal).children[1].update(
            f"{clock_str}{status_info}{daemon_info}"
        )

    def _refresh_board(self):
        """读取 recent 留言并更新展示框（20Hz），带加速逻辑。

        加速：只比较最后一条留言是否变化，没变则不刷新展示框。
        通过 bb-recent wrapper 获取，而非内联。
        """

        if not _tools_dir:
            self._need_scroll_bottom = False
            return

        raw = _exec_wrapper("bb-recent", "100")
        if raw is None:
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
            # 重置 index 缓存，确保下一 tick 重新拉取 board 内容
            global _last_board_index
            _last_board_index = None
            self._need_scroll_bottom = True
            return

        # 通过 bb-leader-post wrapper 发留言（含自动设为 ACTIVE）
        if _tools_dir:
            _exec_wrapper("bb-leader-post", text)

        # 清空输入区
        textarea.text = ""

        # 标记需要滚动到底部 + 强制刷新展示
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
