#!/usr/bin/env python3
"""
terminal_test.py — TUI 基础功能测试 (Textual)

Textual 版复读机: 输入文字 → 显示到留言列表。

用法:
  python3 terminal_test.py

按键:
  Tab / Ctrl+[ / Ctrl+]  切换焦点（输入区 / 留言区）
  Ctrl+D                  提交输入到留言列表
  Ctrl+C                  退出
"""

from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Static, TextArea


class MessageList(Static):
    """带滚动条的留言列表，自动滚动到底部。"""

    def __init__(self):
        super().__init__("")
        self._lines: list[str] = []

    def add_message(self, text: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self._lines.append(f"{ts}  {text}")
        self.update("\n".join(self._lines))

    def on_mount(self):
        self.styles.border = "solid", "blue"
        self.styles.height = "1fr"
        self.styles.overflow_y = "auto"
        self.styles.padding = (1, 1)


class TerminalTest(App):
    CSS = """
    Screen {
        layout: vertical;
    }

    #header {
        height: 3;
        content-align: center middle;
        background: $primary;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }

    #message_count {
        text-style: bold;
        padding: 0 1;
        height: 1;
    }

    MessageList {
        margin: 1 1;
    }

    TextArea {
        height: 5;
        margin: 1 1;
        border: solid $secondary;
    }

    Footer {
        height: 1;
    }
    """

    def __init__(self):
        super().__init__()
        self.message_count = 0

    def compose(self) -> ComposeResult:
        yield Static("terminal-test  |  Textual 复读机  |  Ctrl+D 提交  |  q 退出", id="header")
        yield Static("0 条留言", id="message_count")
        yield MessageList()
        yield TextArea("", id="input", soft_wrap=True)
        yield Footer()

    def on_mount(self):
        self.query_one("#input", TextArea).focus()

    def on_text_area_changed(self, event: TextArea.Changed):
        # 提交是全局快捷键，不需要这里处理
        pass

    def on_key(self, event):
        if event.key == "ctrl+d":
            textarea = self.query_one("#input", TextArea)
            text = textarea.text.strip()
            if text:
                ml = self.query_one(MessageList)
                ml.add_message(text)
                self.message_count += 1
                self.query_one("#message_count", Static).update(f"{self.message_count} 条留言")
                textarea.text = ""
            event.prevent_default()
            event.stop()
        elif event.key == "ctrl+c":
            self.exit()
        elif event.key == "q":
            self.exit()


def main():
    app = TerminalTest()
    app.run()


if __name__ == "__main__":
    main()
