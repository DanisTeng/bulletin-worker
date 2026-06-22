#!/usr/bin/env python3
"""
core/prompt/render.py — 渲染 PROMPT.md（cron prompt）
算法：把 core/prompt/prompt.md 中带 $ 符号的表达式替换成 config.json 中同名的配置项，
输出到 output/PROMPT.md。
"""

import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")
OUTPUT_DIR = os.path.join(ROOT_DIR, "output")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "PROMPT.md")


def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return json.load(f)


def render(text: str, config: dict) -> str:
    def replacer(m: re.Match) -> str:
        key = m.group(1)
        if key in config:
            val = config[key]
            if isinstance(val, list):
                return "\n".join(val) if val else ""
            return str(val)
        return m.group(0)

    return re.sub(r"\x24([a-zA-Z_][a-zA-Z0-9_.]*)", replacer, text)


def main():
    config = load_config(CONFIG_PATH)
    prompt_path = os.path.join(SCRIPT_DIR, "prompt.md")

    with open(prompt_path, "r") as f:
        raw = f.read()

    rendered = render(raw, config).strip() + "\n"

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        f.write(rendered)

    print(f"🌟 提示词已渲染到 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
