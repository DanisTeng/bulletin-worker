#!/usr/bin/env python3
"""
terminal — Bulletin Worker 交互式终端（打包 ELF）

架构: 基于 Textual，core/terminal/render.py 打包为 ELF。
       sh wrapper (run_terminal.sh) 填充所有路径参数。
"""

import argparse
import signal
import sys
import textwrap
from datetime import datetime, timezone, timedelta

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Label, Static, TextArea

TICK_INTERVAL = 1 / 20  # 20Hz

_tz_offset: int = 8


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
    return p.parse_args(argv)


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
            Label("  |  Ctrl+D 提交  |  Ctrl+C 退出"),
            id="header_bar",
        )
        yield VerticalScroll(Static("", id="msg_content"), id="msg_area")
        yield TextArea("", id="input", soft_wrap=True)
        yield Footer()

    def on_mount(self):
        self._messages: list[str] = []
        self.set_interval(TICK_INTERVAL, self.tick)
        self.query_one("#input", TextArea).focus()
        self._update_clock()

    def on_key(self, event):
        if event.key == "ctrl+d":
            self._on_ctrl_d()
            event.prevent_default()
            event.stop()
        elif event.key == "ctrl+c":
            self.exit()

    def tick(self):
        self._update_clock()

    def _update_clock(self):
        offset = timedelta(hours=_tz_offset)
        self.query_one("#clock", Label).update(
            datetime.now(timezone(offset)).strftime("%H:%M:%S")
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
    global _tz_offset
    _tz_offset = parse_args(argv).tz_offset

    app = Terminal()
    signal.signal(signal.SIGINT, lambda s, f: app.exit())
    signal.signal(signal.SIGTERM, lambda s, f: app.exit())
    app.run()


if __name__ == "__main__":
    main()
