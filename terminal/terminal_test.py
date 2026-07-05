#!/usr/bin/env python3
"""
terminal_test.py — bb-terminal 基础测试

打通 TUI 测试链路：30Hz tick() 主循环 + _on_ctrl_d() 提交回调。

用法:
  python3 terminal_test.py

两个扩展点（后续 bb-terminal 在此基础上加功能）:
  - tick(self)       : 30Hz 被调用，用于刷新时间、读取留言板等
  - _on_ctrl_d(self) : Ctrl+D 提交时被调用，用于发 leader post

依赖: textual (pip install textual)
"""

import textwrap
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Label, Static, TextArea

# ── 公开变量（可被 bb-terminal 依赖） ─────────────────────────

TICK_INTERVAL = 1 / 30  # 30Hz


class TerminalTest(App):
    """bb-terminal 基础测试 App。

    子类可以覆写 tick() 和 _on_ctrl_d() 来扩展功能。
    """

    # ── CSS ──────────────────────────────────────────────

    CSS = """
    Screen {
        layout: vertical;
    }

    #header_bar {
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
    }

    #clock {
        text-style: bold;
    }

    #msg_area {
        height: 1fr;
        border: solid $secondary;
        margin: 1 1;
        padding: 0 1;
        overflow-y: scroll;
        scrollbar-gutter: stable;
    }

    #msg_content {
        width: 100%;
    }

    TextArea {
        height: 5;
        margin: 1 1;
        border: solid $accent;
    }

    Footer {
        height: 1;
    }
    """

    # ── 生命周期 ──

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Label("terminal-test  |  "),
            Label("", id="clock"),
            Label("  |  Ctrl+D 提交  |  Ctrl+C 退出"),
            id="header_bar",
        )
        yield VerticalScroll(Static("", id="msg_content"), id="msg_area")
        yield TextArea("", id="input", soft_wrap=True)
        yield Footer()

    def on_mount(self):
        """初始化状态、启动 30Hz 定时器、聚焦输入框。"""
        self._messages: list[str] = []
        self.set_interval(TICK_INTERVAL, self.tick)
        self.query_one("#input", TextArea).focus()
        self._update_clock()

    def on_key(self, event):
        """处理 Ctrl 快捷键。"""
        if event.key == "ctrl+d":
            self._on_ctrl_d()
            event.prevent_default()
            event.stop()
        elif event.key == "ctrl+c":
            self.exit()

    # ── 扩展点 1: 30Hz tick ──

    def tick(self) -> None:
        """30Hz 主循环。后续 bb-terminal 在这里刷新留言板、状态等。

        当前实现：每秒更新一次时钟。
        """
        self._update_clock()

    def _update_clock(self):
        now = datetime.now()
        ts = now.strftime("%H:%M:%S.%f")[:-3]
        self.query_one("#clock", Label).update(ts)

    # ── 扩展点 2: Ctrl+D 提交回调 ──

    def _on_ctrl_d(self) -> None:
        """Ctrl+D 按下时的回调。后续 bb-terminal 在这里做 leader post。"""
        textarea = self.query_one("#input", TextArea)
        text = textarea.text.strip()
        if not text:
            return

        ts = datetime.now().strftime("%H:%M:%S")
        prefix = f"{ts}  "
        indent = " " * len(prefix)
        display = prefix + textwrap.indent(text, indent).lstrip()
        self._messages.append(display)
        self.query_one("#msg_content", Static).update("\n".join(self._messages))
        self.query_one("#msg_area", VerticalScroll).scroll_end(animate=False)

        textarea.text = ""


def main():
    TerminalTest().run()


if __name__ == "__main__":
    main()
