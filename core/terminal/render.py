#!/usr/bin/env python3
"""
core/terminal/render.py — 渲染 terminal TUI 到工作区

1. pyinstaller --onefile 把 terminal.py 打成独立 ELF
2. 生成 run_terminal.sh wrapper（从 config.json 填充 --daemon-dir）

输出目录: $worker_workspace/user_tools/terminal/，完全自包含
用户直接在该目录下执行 ./run_terminal.sh 启动 TUI。
"""

import json
import os
import shutil
import stat
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")
BUILD_DIR = os.path.join(ROOT_DIR, "tmp", "pyi-build-terminal")


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
    required = ["worker_workspace"]
    missing = [k for k in required if not config.get(k)]
    if missing:
        print(f"❌ config.json 缺少必要配置: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)


# ── pyinstaller 打包 ────────────────────────────────────────────


def _make_executable(path: str):
    st = os.stat(path)
    os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def build_onefile(py_path: str, work_dir: str) -> str:
    """用 pyinstaller --onefile 打包单个 .py 脚本。返回输出的 ELF 路径。"""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--distpath",
            work_dir,
            "--workpath",
            os.path.join(work_dir, "build"),
            "--specpath",
            work_dir,
            "--log-level",
            "WARN",
            py_path,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"❌ pyinstaller 打包失败: {py_path}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    name = os.path.basename(py_path).rsplit(".", 1)[0]
    return os.path.join(work_dir, name)


def _cleanup_build(work_dir: str):
    """清理 pyinstaller 构建中间产物。"""
    if not os.path.isdir(work_dir):
        return
    build_dir = os.path.join(work_dir, "build")
    if os.path.isdir(build_dir):
        shutil.rmtree(build_dir, ignore_errors=True)
    for f in os.listdir(work_dir):
        if f.endswith(".spec"):
            os.remove(os.path.join(work_dir, f))


# ── sh wrapper 渲染 ──────────────────────────────────────────


def _render_run_sh(config: dict, dst_dir: str) -> str:
    """生成 run_terminal.sh，从 config.json 填充参数。"""
    ws = config["worker_workspace"]
    tz = config.get("timezone", "Asia/Shanghai")
    daemon_dir = os.path.join(ws, "user_tools", "cron_daemon")

    script = f"""#!/bin/bash
# run_terminal.sh — 由 core/terminal/render.py 自动生成
set -euo pipefail
cd "$(dirname "$0")"
exec ./terminal --daemon-dir "{daemon_dir}" --timezone "{tz}"
"""

    sh_path = os.path.join(dst_dir, "run_terminal.sh")
    with open(sh_path, "w") as f:
        f.write(script)
    _make_executable(sh_path)
    return sh_path


# ── 部署 ────────────────────────────────────────────────────────


def deploy_terminal(config: dict) -> str:
    workspace_dir = config["worker_workspace"]
    dst_dir = os.path.join(workspace_dir, "user_tools", "terminal")

    # 清空并重建目标目录
    if os.path.exists(dst_dir):
        shutil.rmtree(dst_dir)
    os.makedirs(dst_dir, exist_ok=True)

    # 1. pyinstaller 打包 terminal.py → ELF
    src_py = os.path.join(SCRIPT_DIR, "terminal.py")
    if not os.path.exists(src_py):
        print(f"❌ 未找到源文件: {src_py}", file=sys.stderr)
        sys.exit(1)

    print("🔨 pyinstaller 打包 terminal...")
    sys.stdout.flush()
    elf_path = build_onefile(src_py, BUILD_DIR)
    size = os.path.getsize(elf_path)
    size_str = f"{size / 1024 / 1024:.1f}MB" if size > 1024 * 1024 else f"{size / 1024:.0f}KB"
    print(f"   ✅ {size_str}")

    dst_elf = os.path.join(dst_dir, "terminal")
    if os.path.exists(dst_elf):
        os.remove(dst_elf)
    os.rename(elf_path, dst_elf)
    _make_executable(dst_elf)
    print(f"   → {dst_elf}")

    # 2. 生成 run_terminal.sh wrapper
    sh_path = _render_run_sh(config, dst_dir)
    print(f"   ✅ {sh_path}")

    # 清理构建中间产物
    _cleanup_build(BUILD_DIR)

    print(f"\n📍 terminal 已部署到: {dst_dir}")
    print(f"   启动: cd {dst_dir} && ./run_terminal.sh")
    print(f"   退出: Ctrl+C 或 Ctrl+C")

    return dst_dir


def main():
    config = load_config(CONFIG_PATH)
    _validate_config(config)

    print("🔧 [terminal] 渲染到工作区...")
    deploy_terminal(config)


if __name__ == "__main__":
    main()
