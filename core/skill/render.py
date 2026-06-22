#!/usr/bin/env python3
"""
core/skill/render.py — 渲染 SKILL.md
算法：把 skill/ 下 3 个 .md part 文件中带 $ 符号的表达式替换成 config.json 中
同名的配置项，合并输出到 output/SKILL.md。
"""

import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))
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

    return re.sub(r"\$" r"([a-zA-Z_][a-zA-Z0-9_.]*)", replacer, text)


def build_skill_md(config: dict) -> str:
    parts = []

    for filename in ["self_recognition_part.md", "tools_usage_part.md", "workflow_v2_part.md"]:
        path = os.path.join(SCRIPT_DIR, filename)
        if os.path.exists(path):
            with open(path, "r") as f:
                raw = f.read()
            parts.append(render_part(raw, config))
            parts.append("\n\n")

    return "".join(parts).strip() + "\n"


def render_tools_usage(config: dict, dst_dir: str):
    """渲染 tools_usage_part.md 到指定目录下的 TOOLS_USAGE.md。"""
    usage_path = os.path.join(SCRIPT_DIR, "tools_usage_part.md")
    if not os.path.exists(usage_path):
        print("   ⚠️  tools_usage_part.md 不存在，跳过")
        return
    with open(usage_path) as f:
        content = render_part(f.read(), config)
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, "TOOLS_USAGE.md")
    with open(dst, "w") as f:
        f.write(content)
    print(f"   → TOOLS_USAGE.md 已渲染")


def main():
    config = load_config(CONFIG_PATH)
    skill_md = build_skill_md(config)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        f.write(skill_md)

    print(f"🌟 SKILL.md 已渲染到 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
