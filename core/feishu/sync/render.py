#!/usr/bin/env python3
"""
core/feishu/sync/render.py — 渲染 feishu_ui 到工作区

1. pyinstaller --onefile 把 feishu_ui.py 打成独立 ELF
2. 生成 run_feishu.sh wrapper（从 config.json 填充所有 -- 参数）

输出目录: $worker_workspace/user_tools/feishu/，完全自包含

config.json 字段:
  "feishu": {
    "app_id": "cli_xxx",
    "app_secret": "",        # 或走环境变量 PUBLIC_FEISHU_APP_SECRET
    "leader_user_name": "滕怀远",
    "leader_open_id": ""     # 可选，非空时免联系人查询
  }
"""

import json
import os
import shutil
import stat
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")
BUILD_DIR = os.path.join(ROOT_DIR, "tmp", "pyi-build-feishu-ui")


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
    feishu = config.get("feishu")
    if not feishu:
        print("❌ config.json 缺少 feishu 配置段", file=sys.stderr)
        sys.exit(1)
    missing = []
    for key in ("app_id", "leader_user_name"):
        if not feishu.get(key):
            missing.append(f"feishu.{key}")
    if missing:
        print(f"❌ config.json feishu 缺少必要字段: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    required_base = ["worker_workspace", "board_path"]
    missing_base = [k for k in required_base if not config.get(k)]
    if missing_base:
        print(f"❌ config.json 缺少必要字段: {', '.join(missing_base)}", file=sys.stderr)
        sys.exit(1)


# ── pyinstaller 打包 ────────────────────────────────────────────


def _make_executable(path: str):
    st = os.stat(path)
    os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _prepare_build_dir() -> tuple[str, str]:
    """准备打包用的临时目录。

    1. 复制 feishu_ui.py 到临时目录
    2. 复制 feishu_api.py 到临时目录（通过 --add-data 打包进 ELF）

    Returns:
        (src_dir, py_path): 临时源目录和 feishu_ui.py 路径
    """
    src_dir = os.path.join(ROOT_DIR, "tmp", "pyi-src-feishu-ui")
    os.makedirs(src_dir, exist_ok=True)

    feishu_ui_src = os.path.join(SCRIPT_DIR, "feishu_ui.py")
    dst_py = os.path.join(src_dir, "feishu_ui.py")
    shutil.copy2(feishu_ui_src, dst_py)

    feishu_api_src = os.path.join(ROOT_DIR, "core", "feishu", "feishu_api.py")
    feishu_api_dst = os.path.join(src_dir, "feishu_api.py")
    shutil.copy2(feishu_api_src, feishu_api_dst)

    return src_dir, dst_py


def _cleanup_prepared(src_dir: str):
    """清理临时源目录。"""
    if os.path.isdir(src_dir):
        shutil.rmtree(src_dir, ignore_errors=True)


def build_onefile(py_path: str, work_dir: str) -> str:
    """用 pyinstaller --onefile 打包 feishu_ui.py。

    用 --add-data 把 feishu_api.py 作为数据文件打包进 ELF。
    这样 ELF 启动时 feishu_api.py 会解压到临时目录，import 可以找到。
    """
    py_dir = os.path.dirname(py_path)
    feishu_api_path = os.path.join(py_dir, "feishu_api.py")
    add_data_spec = f"{feishu_api_path}:."

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--add-data",
            add_data_spec,
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
    if not os.path.isdir(work_dir):
        return
    build_dir = os.path.join(work_dir, "build")
    if os.path.isdir(build_dir):
        shutil.rmtree(build_dir, ignore_errors=True)
    for f in os.listdir(work_dir):
        if f.endswith(".spec"):
            os.remove(os.path.join(work_dir, f))


# ── sh wrapper 渲染 ──────────────────────────────────────────


def _validate_feishu_config(feishu_cfg: dict):
    """启动前检查 feishu 配置，空时主动报错退出。"""
    if not feishu_cfg.get("app_id"):
        print("❌ config.json feishu.app_id 为空", file=sys.stderr)
        sys.exit(1)
    if not feishu_cfg.get("leader_user_name"):
        print("❌ config.json feishu.leader_user_name 为空", file=sys.stderr)
        sys.exit(1)


def _render_run_sh(config: dict, dst_dir: str) -> str:
    """生成 run_feishu.sh，从 config.json 填充参数。"""
    workspace = config.get("worker_workspace", "")
    board_dir = config.get("board_path", "")
    tools_dir = os.path.join(workspace, "tools") if workspace else ""
    feishu_cfg = config.get("feishu", {})

    _validate_feishu_config(feishu_cfg)

    app_id = feishu_cfg["app_id"]
    app_secret = feishu_cfg.get("app_secret", "")
    leader_name = feishu_cfg["leader_user_name"]
    leader_open_id = feishu_cfg.get("leader_open_id", "")

    # secret 为空时从环境变量读取
    if app_secret:
        secret_line = f'--feishu-app-secret "{app_secret}"'
    else:
        secret_line = '--feishu-app-secret "$PUBLIC_FEISHU_APP_SECRET"'

    script = f"""#!/bin/bash
# run_feishu.sh — 由 core/feishu/sync/render.py 自动生成
set -euo pipefail
cd "$(dirname "$0")"
exec ./feishu_ui \
    --board-dir "{board_dir}" \
    --tools-dir "{tools_dir}" \
    --worker-workspace "{workspace}" \
    --feishu-app-id "{app_id}" \
    {secret_line} \
    --leader-name "{leader_name}" \
    --leader-open-id "{leader_open_id}"
"""
    sh_path = os.path.join(dst_dir, "run_feishu.sh")
    with open(sh_path, "w") as f:
        f.write(script)
    _make_executable(sh_path)
    return sh_path


# ── 部署 ────────────────────────────────────────────────────────


def deploy_feishu_ui(config: dict) -> str:
    workspace_dir = config["worker_workspace"]
    dst_dir = os.path.join(workspace_dir, "user_tools", "feishu")

    # 清空并重建目标目录
    if os.path.exists(dst_dir):
        shutil.rmtree(dst_dir)
    os.makedirs(dst_dir, exist_ok=True)

    # 0. 准备临时源目录（feishu_api.py 复制到同级）
    src_dir, src_py = _prepare_build_dir()

    # 1. pyinstaller 打包 feishu_ui.py → ELF
    if not os.path.exists(src_py):
        print(f"❌ 未找到源文件: {src_py}", file=sys.stderr)
        _cleanup_prepared(src_dir)
        sys.exit(1)

    print("🔨 pyinstaller 打包 feishu_ui...")
    sys.stdout.flush()
    elf_path = build_onefile(src_py, BUILD_DIR)
    size = os.path.getsize(elf_path)
    size_str = f"{size / 1024 / 1024:.1f}MB" if size > 1024 * 1024 else f"{size / 1024:.0f}KB"
    print(f"   ✅ {size_str}")

    dst_elf = os.path.join(dst_dir, "feishu_ui")
    if os.path.exists(dst_elf):
        os.remove(dst_elf)
    os.rename(elf_path, dst_elf)
    _make_executable(dst_elf)
    print(f"   → {dst_elf}")

    # 清理临时源目录
    _cleanup_prepared(src_dir)

    # 2. 生成 run_feishu.sh wrapper
    sh_path = _render_run_sh(config, dst_dir)
    print(f"   ✅ {sh_path}")

    # 清理构建中间产物
    _cleanup_build(BUILD_DIR)

    print(f"\n📍 feishu_ui 已部署到: {dst_dir}")
    print(f"   启动: cd {dst_dir} && ./run_feishu.sh")
    print(f"   停止: Ctrl+C")

    return dst_dir


def main():
    config = load_config(CONFIG_PATH)
    _validate_config(config)

    print("🔧 [feishu_ui] 渲染到工作区...")
    deploy_feishu_ui(config)


if __name__ == "__main__":
    main()
