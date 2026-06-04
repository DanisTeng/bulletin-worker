#!/usr/bin/env python3
"""
render_skill_md.py — 渲染 SKILL.md
算法：把 core/self_recognition_part.md 和 core/workflow_part.md 中
带 $ 符号的表达式替换成 config.json 中同名的配置项，合并输出到 output/SKILL.md。
"""

import json
import os
import re
import sys

CORE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CORE_DIR)
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")
OUTPUT_DIR = os.path.join(ROOT_DIR, "output")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "SKILL.md")


def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return json.load(f)


def render_part(text: str, config: dict) -> str:
    """Replace $key expressions with corresponding config values.

    Supports:
      - $key           -> config["key"]
      - $key.suffix    -> treated as part of the string (no special meaning for keys with dots)
    """

    def replacer(m: re.Match) -> str:
        key = m.group(1)
        if key in config:
            val = config[key]
            if isinstance(val, list):
                # Render lists as YAML-style bullet in code blocks
                return "\n".join(val) if val else ""
            return str(val)
        # Leave unreplaced variables as-is (fail softly so user can see what's missing)
        return m.group(0)

    return re.sub(r"\$([a-zA-Z_][a-zA-Z0-9_.]*)", replacer, text)


def build_skill_md(config: dict) -> str:
    parts = []

    for filename in ["self_recognition_part.md", "workflow_part.md"]:
        path = os.path.join(CORE_DIR, filename)
        if os.path.exists(path):
            with open(path, "r") as f:
                raw = f.read()
            parts.append(render_part(raw, config))
            parts.append("\n\n")

    return "".join(parts).strip() + "\n"


def main():
    config = load_config(CONFIG_PATH)
    skill_md = build_skill_md(config)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        f.write(skill_md)

    print(f"✅ Rendered to {OUTPUT_PATH}")
    print(skill_md)


if __name__ == "__main__":
    main()
