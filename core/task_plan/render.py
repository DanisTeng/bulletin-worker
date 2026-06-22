#!/usr/bin/env python3
"""
core/task_plan/render.py — 渲染任务计划 + v2 工作流到工作区

工作流程：
  1. 把 agent_tools/bb_plan.py 打成 ELF，部署到 $worker_workspace/tools/
  2. 渲染 bb-plan-* 的 sh wrapper，部署到 tools/
  3. 部署 v2 工作流 md（update_task_plan / execute_task_plan / new_task_plan）到 scripts/
  4. 部署计划书设计文档（task_plan_format / task_plan_strategy）到 scripts/
  5. 创建 data/ 目录
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")
TASK_PLAN_DIR = os.path.join(SCRIPT_DIR)
AGENT_TOOLS_DIR = os.path.join(ROOT_DIR, "agent_tools")
BUILD_DIR = os.path.join(ROOT_DIR, "tmp", "pyi-build")


def load_config(path: str) -> dict:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"❌ 未找到 config 文件: {path}", file=sys.stderr)
        sys.exit(1)


def _validate_config(config: dict):
    required = ["worker_workspace"]
    missing = [k for k in required if not config.get(k)]
    if missing:
        print(f"❌ config.json 缺少必要配置: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)


def build_onefile(py_path: str, work_dir: str) -> str:
    """用 pyinstaller --onefile 打包单个 .py 脚本。返回 ELF 路径。"""
    base = os.path.basename(py_path)
    name = base.rsplit(".", 1)[0]

    result = subprocess.run(
        [
            "pyinstaller",
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

    return os.path.join(work_dir, name)


def _make_executable(path: str):
    st = os.stat(path)
    os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def render_sh_wrapper(template: str, placeholders: dict[str, str]) -> str:
    """将 template 中的占位符替换为实际值。"""
    sorted_keys = sorted(placeholders.keys(), key=len, reverse=True)
    result = template
    for key in sorted_keys:
        result = result.replace(key, placeholders[key])
    return result


def _verify_placeholders(content: str) -> list[str]:
    """检查内容中是否有未替换的 $UPPER_CASE 占位符"""
    pattern = re.compile(r"\$[A-Z][A-Z_]+")
    return sorted(set(pattern.findall(content)))


def render_md(content: str, config: dict) -> str:
    """替换 md 文本中的 $key 占位符为 config 中的对应值。"""
    import re
    def replacer(m):
        key = m.group(1)
        if key in config:
            val = config[key]
            if isinstance(val, list):
                return "\n".join(val) if val else ""
            return str(val)
        return m.group(0)
    return re.sub(r"\x24([a-zA-Z_][a-zA-Z0-9_.]*)", replacer, content)


def _deploy_rendered_md(src: str, dst: str, config: dict):
    """读取、渲染 $key 占位符、写入目标路径。"""
    if not os.path.exists(src):
        return False
    with open(src) as f:
        content = f.read()
    rendered = render_md(content, config)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w") as f:
        f.write(rendered)
    return True


def main():
    config = load_config(CONFIG_PATH)
    _validate_config(config)

    workspace_dir = config["worker_workspace"]
    tools_dir = os.path.join(workspace_dir, "tools")
    scripts_dir = os.path.join(workspace_dir, "scripts")
    plan_dir = os.path.join(workspace_dir, "plan")
    plan_path = os.path.join(plan_dir, "current_plan.json")
    os.makedirs(tools_dir, exist_ok=True)
    os.makedirs(scripts_dir, exist_ok=True)
    os.makedirs(plan_dir, exist_ok=True)

    # ── 占位符 ──


    placeholders = {
        "$BOARD_DIR": config.get("board_path", ""),
        "$WORKSPACE_DIR": workspace_dir,
        "$AGENT_TOOLS_DIR": tools_dir,
        "$PLAN_FILE": plan_path,
    }

    # ── 第 1 步：打包 bb_plan.py ──
    plan_py = os.path.join(AGENT_TOOLS_DIR, "bb_plan.py")
    if not os.path.exists(plan_py):
        print(f"❌ 未找到 {plan_py}", file=sys.stderr)
        sys.exit(1)

    print("🔨 打包 bb_plan.py ...")
    os.makedirs(BUILD_DIR, exist_ok=True)
    elf_path = build_onefile(plan_py, BUILD_DIR)

    # 部署 ELF
    elf_dst = os.path.join(tools_dir, "bb_plan")
    shutil.copy2(elf_path, elf_dst)
    _make_executable(elf_dst)
    size = os.path.getsize(elf_dst)
    size_str = f"{size / 1024 / 1024:.1f}MB" if size > 1024 * 1024 else f"{size / 1024:.0f}KB"
    print(f"   ✅ bb_plan → {elf_dst} ({size_str})")

    # ── 第 2 步：从 agent_tools_def.json 读取 plan wrapper template ──
    tools_def_path = os.path.join(ROOT_DIR, "core", "agent_tools", "tools_def.json")
    if os.path.exists(tools_def_path):
        with open(tools_def_path) as f:
            all_tools = json.load(f)
    else:
        all_tools = []
    plan_tools = [t for t in all_tools if t["name"].startswith("bb-plan-")]
    if not plan_tools:
        print("⚠️  agent_tools_def.json 中未找到 bb-plan-* 条目", file=sys.stderr)
    wrappers = {t["name"]: t["template"] for t in plan_tools}

    print()
    print("📜 Shell wrapper:")
    for name, template in wrappers.items():
        script = render_sh_wrapper(template, placeholders)
        unfilled = _verify_placeholders(script)
        dst = os.path.join(tools_dir, name)
        with open(dst, "w") as f:
            f.write(script + "\n")
        _make_executable(dst)
        status = "✅" if not unfilled else f"⚠️  未替换: {unfilled}"
        print(f"   {status} {name} → {dst}")

    # ── 第 3 步：部署 v2 工作流脚本到 scripts/ ──
    skill_dir = os.path.join(ROOT_DIR, "core", "skill")
    v2_files = [
        ("update_task_plan.md",   "update_task_plan.md"),
        ("execute_task_plan.md",  "execute_task_plan.md"),
        ("new_task_plan.md",      "new_task_plan.md"),
    ]
    print()
    print("📋 v2 工作流脚本:")
    for src_name, dst_name in v2_files:
        src = os.path.join(skill_dir, src_name)
        dst = os.path.join(scripts_dir, dst_name)
        if _deploy_rendered_md(src, dst, config):
            print(f"   ✅ {dst_name}")
        else:
            print(f"   ⚠️  未找到 {src_name}", file=sys.stderr)

    # ── 第 4 步：部署计划书设计文档到 scripts/ ──
    design_files = [
        ("task_plan_format.md",   "task_plan_format.md"),
        ("task_plan_strategy.md", "task_plan_strategy.md"),
    ]
    print()
    print("📋 计划书设计文档:")
    for src_name, dst_name in design_files:
        src = os.path.join(TASK_PLAN_DIR, src_name)
        dst = os.path.join(scripts_dir, dst_name)
        if _deploy_rendered_md(src, dst, config):
            print(f"   ✅ {dst_name}")
        else:
            print(f"   ⚠️  未找到 {src_name}", file=sys.stderr)

    # ── 第 5 步：创建 data/ 目录 ──
    data_dir = os.path.join(workspace_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    print()
    print("📁 data/ 目录已创建")

    # ── 清理 ──
    build_dir = os.path.join(BUILD_DIR, "build")
    if os.path.isdir(build_dir):
        shutil.rmtree(build_dir, ignore_errors=True)
    for f in os.listdir(BUILD_DIR):
        if f.endswith(".spec"):
            os.remove(os.path.join(BUILD_DIR, f))

    print("\n✅ 任务计划工具渲染完成")


if __name__ == "__main__":
    main()
