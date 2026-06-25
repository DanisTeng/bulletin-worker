#!/usr/bin/env python3
"""
core/cron_daemon/render.py — 渲染 cron_daemon 到工作区

1. 将 cron_daemon.py 拷贝到 $worker_workspace/user_tools/cron_daemon/
2. 渲染 PROMPT.txt（替换 $WORKER_WS 等占位符）到同一目录
3. 创建 cron_log/ 子目录

输出目录: $worker_workspace/user_tools/cron_daemon/，完全自包含
用户直接在该目录下运行: python3 cron_daemon.py -p PROMPT.txt -i 5 -t 900
"""

import json
import os
import re
import shutil
import stat
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")
OUTPUT_DIR = os.path.join(ROOT_DIR, "output")

# ── 加载 ────────────────────────────────────────────────────────


def load_config(path: str) -> dict:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"❌ 未找到 config 文件: {path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ config 格式错误: {e}", file=sys.stderr)
        sys.exit(1)


def _validate_config(config: dict):
    required = ["worker_workspace", "board_path", "worker_name"]
    missing = [k for k in required if not config.get(k)]
    if missing:
        print(f"❌ config.json 缺少必要配置: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)


# ── 占位符替换 ──────────────────────────────────────────────────


def build_placeholders(config: dict) -> dict[str, str]:
    """构建占位符 → 实际值映射。"""
    placeholders = {
        "$WORKER_NAME": config.get("worker_name", ""),
        "$SUPERIOR_NAME": config.get("superior_name", ""),
        "$WORKER_WS": config["worker_workspace"],
        "$BOARD_PATH": config["board_path"],
        "$TOOLS_DIR": os.path.join(config["worker_workspace"], "tools"),
    }

    # identity_files 特殊处理：多行缩进
    identity_files = config.get("identity_files", [])
    if identity_files:
        indented = "\n".join(f"  - {f}" for f in identity_files)
        placeholders["$IDENTITY_FILES_INDENTED"] = indented
    else:
        placeholders["$IDENTITY_FILES_INDENTED"] = "  (无)"

    return placeholders


def render_template(template: str, placeholders: dict[str, str]) -> str:
    """将 template 中的 $NAME 全部替换为 placeholders 中的值。"""
    sorted_keys = sorted(placeholders.keys(), key=len, reverse=True)
    result = template
    for key in sorted_keys:
        result = result.replace(key, placeholders[key])
    return result


def _verify_placeholders(content: str):
    """检查是否有未替换的 $UPPER_CASE 占位符"""
    pattern = re.compile(r"\$[A-Z][A-Z_]+")
    unfilled = pattern.findall(content)
    if unfilled:
        unique = sorted(set(unfilled))
        print(f"   ⚠️  未替换的占位符: {' '.join(unique)}")
        return False
    return True


# ── 部署 ────────────────────────────────────────────────────────


def deploy_cron_daemon(config: dict) -> str:
    """
    将 cron_daemon 部署到工作区。
    返回目标目录路径。
    """
    workspace_dir = config["worker_workspace"]
    dst_dir = os.path.join(workspace_dir, "user_tools", "cron_daemon")

    # 清空并重建目标目录
    if os.path.exists(dst_dir):
        shutil.rmtree(dst_dir)
    os.makedirs(dst_dir, exist_ok=True)

    # 1. 拷贝 cron_daemon.py
    src_py = os.path.join(SCRIPT_DIR, "cron_daemon.py")
    dst_py = os.path.join(dst_dir, "cron_daemon.py")

    if not os.path.exists(src_py):
        print(f"❌ 未找到源文件: {src_py}", file=sys.stderr)
        sys.exit(1)

    shutil.copy2(src_py, dst_py)
    # 添加可执行权限（虽然 Python 脚本需要 python3 运行，但加个权限方便 chmod）
    st = os.stat(dst_py)
    os.chmod(dst_py, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"   ✅ cron_daemon.py → {dst_py}")

    # 2. 渲染 PROMPT.txt
    src_prompt = os.path.join(SCRIPT_DIR, "PROMPT.txt")
    dst_prompt = os.path.join(dst_dir, "PROMPT.txt")

    if not os.path.exists(src_prompt):
        print(f"❌ 未找到模板文件: {src_prompt}", file=sys.stderr)
        sys.exit(1)

    placeholders = build_placeholders(config)

    with open(src_prompt, "r") as f:
        template = f.read()

    rendered = render_template(template, placeholders)

    with open(dst_prompt, "w") as f:
        f.write(rendered)

    print(f"   ✅ PROMPT.txt → {dst_prompt}")
    ok = _verify_placeholders(rendered)

    # 3. 创建 cron_log/ 目录
    log_dir = os.path.join(dst_dir, "cron_log")
    os.makedirs(log_dir, exist_ok=True)
    print(f"   ✅ cron_log/ → {log_dir}")

    if ok:
        print(f"\n📍 cron_daemon 已部署到: {dst_dir}")
        print(f"   运行: cd {dst_dir} && python3 cron_daemon.py -p PROMPT.txt -i 5 -t 900")
    else:
        print(f"\n⚠️  有未替换的占位符，请检查", file=sys.stderr)
        sys.exit(1)

    return dst_dir


def main():
    config = load_config(CONFIG_PATH)
    _validate_config(config)

    print("🔧 [cron_daemon] 渲染 cron_daemon 到工作区...")
    deploy_cron_daemon(config)


if __name__ == "__main__":
    main()
