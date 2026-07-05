#!/usr/bin/env python3
"""
core/cron_daemon/render.py — 渲染 cron_daemon 到工作区

1. 用 pyinstaller --onefile 把 cron_daemon.py 打成独立 ELF
2. 从 output/PROMPT.md 拷贝到目标目录（复用已有的 prompt 渲染成果）
3. 创建 cron_log/ 子目录
4. 生成 run_cron_daemon.sh wrapper（从 config.json 填充参数）

输出目录: $worker_workspace/user_tools/cron_daemon/，完全自包含
用户直接在该目录下执行 ./run_cron_daemon.sh 启动。
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
OUTPUT_DIR = os.path.join(ROOT_DIR, "output")
BUILD_DIR = os.path.join(ROOT_DIR, "tmp", "pyi-build-cron-daemon")


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

    # ELF 文件名是脚本基名去 .py
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
    """生成 run_cron_daemon.sh，从 config.json 填充所有参数。"""
    cron = config.get("cron_daemon", {})
    interval = cron.get("interval_minutes", 5)
    timeout = cron.get("timeout_seconds", 600)
    enable_status_check = cron.get("enable_status_check", True)
    skip_if_idle = cron.get("skip_if_idle", True)

    # 使用相对路径（wrapper cd 到 daemon 目录后）
    args = (
        f'--prompt ./PROMPT.md '
        f'--interval {interval} '
        f'--timeout {timeout} '
        f'--output-dir ./cron_log'
    )

    if enable_status_check:
        # bb-get-status 在 $worker_workspace/tools/bb-get-status
        ws_dir = os.path.dirname(os.path.dirname(dst_dir))  # user_tools/cron_daemon/ → 工作区根
        bb_status = os.path.join(ws_dir, "tools", "bb-get-status")
        if skip_if_idle:
            args += f' --bb-status-cmd "{bb_status}"'
        else:
            args += f' --bb-status-cmd "{bb_status}" --no-skip-if-idle'

    script = f"""#!/bin/bash
# run_cron_daemon.sh — 由 core/cron_daemon/render.py 自动生成
set -euo pipefail
cd "$(dirname "$0")"
exec ./cron_daemon {args}
"""


    sh_path = os.path.join(dst_dir, "run_cron_daemon.sh")
    with open(sh_path, "w") as f:
        f.write(script)
    _make_executable(sh_path)
    return sh_path


# ── 部署 ────────────────────────────────────────────────────────


def deploy_cron_daemon(config: dict) -> str:
    workspace_dir = config["worker_workspace"]
    dst_dir = os.path.join(workspace_dir, "user_tools", "cron_daemon")

    # 清空并重建目标目录
    if os.path.exists(dst_dir):
        shutil.rmtree(dst_dir)
    os.makedirs(dst_dir, exist_ok=True)

    # 1. pyinstaller 打包 cron_daemon.py → ELF
    src_py = os.path.join(SCRIPT_DIR, "cron_daemon.py")
    if not os.path.exists(src_py):
        print(f"❌ 未找到源文件: {src_py}", file=sys.stderr)
        sys.exit(1)

    print("🔨 pyinstaller 打包 cron_daemon...")
    sys.stdout.flush()
    elf_path = build_onefile(src_py, BUILD_DIR)
    size = os.path.getsize(elf_path)
    size_str = f"{size / 1024 / 1024:.1f}MB" if size > 1024 * 1024 else f"{size / 1024:.0f}KB"
    print(f"   ✅ {size_str}")

    dst_elf = os.path.join(dst_dir, "cron_daemon")
    if os.path.exists(dst_elf):
        os.remove(dst_elf)
    os.rename(elf_path, dst_elf)
    _make_executable(dst_elf)
    print(f"   → {dst_elf}")

    # 2. PROMPT.md — 复用 core/prompt/render.py 的输出
    src_prompt = os.path.join(OUTPUT_DIR, "PROMPT.md")
    dst_prompt = os.path.join(dst_dir, "PROMPT.md")

    if not os.path.exists(src_prompt):
        print(f"❌ 未找到 PROMPT.md，请先运行 core/prompt/render.py: {src_prompt}", file=sys.stderr)
        sys.exit(1)

    shutil.copy2(src_prompt, dst_prompt)
    print(f"   ✅ PROMPT.md → {dst_prompt}")

    # 3. 创建 cron_log/ 目录
    log_dir = os.path.join(dst_dir, "cron_log")
    os.makedirs(log_dir, exist_ok=True)
    print(f"   ✅ cron_log/ → {log_dir}")

    # 4. 生成 run_cron_daemon.sh wrapper
    sh_path = _render_run_sh(config, dst_dir)
    print(f"   ✅ {sh_path}")

    # 清理构建中间产物
    _cleanup_build(BUILD_DIR)

    print(f"\n📍 cron_daemon 已部署到: {dst_dir}")
    print(f"   启动: cd {dst_dir} && ./run_cron_daemon.sh")

    return dst_dir


def main():
    config = load_config(CONFIG_PATH)
    _validate_config(config)

    print("🔧 [cron_daemon] 渲染到工作区...")
    deploy_cron_daemon(config)


if __name__ == "__main__":
    main()
