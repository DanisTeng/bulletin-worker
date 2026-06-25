#!/usr/bin/env python3
"""
core/agent_tools/render.py — 渲染 agent 工具到工作区

工作流程：
  1. 用 pyinstaller --onefile 把 agent_tools/*.py 打成独立 ELF
  2. 把 ELF 拷贝到 $worker_workspace/tools/
  3. 渲染 sh wrapper（引用 ELF 而非 .py 文件，依赖嵌入）

输出目录: $worker_workspace/tools/，完全自包含（不依赖 Python runtime）。
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
TOOLS_DEF_PATH = os.path.join(SCRIPT_DIR, "tools_def.json")
AGENT_TOOLS_DIR = os.path.join(ROOT_DIR, "agent_tools")
BUILD_DIR = os.path.join(ROOT_DIR, "tmp", "pyi-build")


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


def load_tools_def(path: str) -> list[dict]:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"❌ 未找到工具定义文件: {path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ 工具定义格式错误: {e}", file=sys.stderr)
        sys.exit(1)


def _validate_config(config: dict):
    required = ["worker_workspace", "board_path", "worker_name", "superior_name"]
    missing = [k for k in required if not config.get(k)]
    if missing:
        print(f"❌ config.json 缺少必要配置: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)


# ── 占位符 ──────────────────────────────────────────────────────


def build_placeholders(config: dict, tools_dir: str) -> dict[str, str]:
    """
    构建占位符 → 实际值的映射。
    $AGENT_TOOLS_DIR 指向 tools/ 自身，使工作区自包含。

    注意：_validate_config 已保证 board_path / worker_name / superior_name / worker_workspace 非空。
    """
    return {
        "$BOARD_DIR": config["board_path"],
        "$WORKER_NAME": config["worker_name"],
        "$SUPERIOR_NAME": config["superior_name"],
        "$WORKSPACE_DIR": config["worker_workspace"],
        "$AGENT_TOOLS_DIR": tools_dir,
    }


def render_template(template: str, placeholders: dict[str, str]) -> str:
    """将 template 中的 $NAME 全部替换为 placeholders 中的值。"""
    sorted_keys = sorted(placeholders.keys(), key=len, reverse=True)
    result = template
    for key in sorted_keys:
        result = result.replace(key, placeholders[key])
    return result


# ── pyinstaller 打包 ────────────────────────────────────────────


def _make_executable(path: str):
    st = os.stat(path)
    os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _pyi_script_name(py_path: str) -> str:
    """'bb_status.py' → 'bb_status'（去掉 .py 后缀）"""
    base = os.path.basename(py_path)
    return base.rsplit(".", 1)[0]


def build_onefile(py_path: str, work_dir: str) -> str:
    """
    用 pyinstaller --onefile 打包单个 .py 脚本。
    返回输出的 ELF 路径。
    """
    script_name = _pyi_script_name(py_path)

    # pyinstaller 默认把产物放 dist/，我们指定 --distpath 到 work_dir
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

    return os.path.join(work_dir, script_name)


def build_all(src_dir: str, work_dir: str) -> dict[str, str]:
    """
    打包 src_dir 下所有 .py 文件。
    返回 {basename_without_ext → elf_path} 映射。
    """
    os.makedirs(work_dir, exist_ok=True)

    py_files = sorted(
        f for f in os.listdir(src_dir) if f.endswith(".py") and f != "__init__.py"
    )

    if not py_files:
        print(f"❌ agent_tools 目录无 .py 文件: {src_dir}", file=sys.stderr)
        sys.exit(1)

    elf_map: dict[str, str] = {}
    print("🔨 pyinstaller 打包中...")

    for fname in py_files:
        py_path = os.path.join(src_dir, fname)
        name = _pyi_script_name(fname)
        sys.stdout.write(f"   {name:20s} ... ")
        sys.stdout.flush()
        elf_path = build_onefile(py_path, work_dir)
        size = os.path.getsize(elf_path)
        size_str = f"{size / 1024 / 1024:.1f}MB" if size > 1024 * 1024 else f"{size / 1024:.0f}KB"
        print(f" ✅ {size_str}")
        elf_map[name] = elf_path

    return elf_map


# ── 拷贝到工作区 ────────────────────────────────────────────────


def deploy_elfs(
    elf_map: dict[str, str],
    dst_dir: str,
) -> list[tuple[str, str]]:
    """把 ELF 拷贝到 dst_dir。返回 [(name, dst_path), ...]"""
    os.makedirs(dst_dir, exist_ok=True)
    deployed: list[tuple[str, str]] = []

    for name, src in elf_map.items():
        dst = os.path.join(dst_dir, name)
        shutil.copy2(src, dst)
        _make_executable(dst)
        deployed.append((name, dst))

    return deployed


# ── sh wrapper 渲染 ─────────────────────────────────────────────


def render_sh_wrappers(
    tools_def: list[dict],
    placeholders: dict[str, str],
    output_dir: str,
) -> list[tuple[str, str]]:
    """渲染 sh wrapper 到 output_dir。返回 [(name, path), ...]"""
    os.makedirs(output_dir, exist_ok=True)
    rendered: list[tuple[str, str]] = []

    for tool in tools_def:
        name = tool["name"]
        # bb-plan-* 由 core/task_plan/render.py 单独处理，这里跳过避免冲突
        if name.startswith("bb-plan-"):
            continue
        template = tool["template"]

        script = render_template(template, placeholders)
        if not script.endswith("\n"):
            script += "\n"

        out = os.path.join(output_dir, name)
        with open(out, "w") as f:
            f.write(script)

        _make_executable(out)

        if not script.startswith("#!/"):
            print(f"⚠️  {name} 缺少 shebang", file=sys.stderr)

        rendered.append((name, out))

    return rendered


# ── 验证 ────────────────────────────────────────────────────────


def _verify_placeholders(wrappers: list[tuple[str, str]]):
    """检查 sh wrapper 是否有未替换的 $UPPER_CASE 占位符"""
    # 匹配 \$UPPER_CASE_UNDERSCORE 模式的未替换占位符
    # 不匹配 shell 原生变量如 $@ $* $?（不含大写字母或下划线开头）
    pattern = re.compile(r"\$[A-Z][A-Z_]+")
    all_ok = True

    for name, path in wrappers:
        content = open(path).read()
        unfilled = pattern.findall(content)
        if unfilled:
            unique = sorted(set(unfilled))
            print(f"   ❌ {name} — 未替换: {' '.join(unique)}")
            all_ok = False
        else:
            print(f"   ✅ {name}")

    return all_ok


# ── 清理 ────────────────────────────────────────────────────────


def _cleanup_build(work_dir: str):
    """清理 pyinstaller 构建中间产物（build/ + *.spec），保留 dist/ 下的 ELF 供后续增量用。"""
    if not os.path.isdir(work_dir):
        return
    build_dir = os.path.join(work_dir, "build")
    if os.path.isdir(build_dir):
        shutil.rmtree(build_dir, ignore_errors=True)
    for f in os.listdir(work_dir):
        if f.endswith(".spec"):
            os.remove(os.path.join(work_dir, f))


# ── 主流程 ──────────────────────────────────────────────────────


def main():
    config = load_config(CONFIG_PATH)
    _validate_config(config)

    workspace_dir = config["worker_workspace"]
    tools_dir = os.path.join(workspace_dir, "tools")

    # 第 1 步：pyinstaller 打包
    elf_map = build_all(AGENT_TOOLS_DIR, BUILD_DIR)

    print()

    # 第 2 步：ELF 部署到 tools/
    deployed = deploy_elfs(elf_map, tools_dir)

    print(f"📦 ELF 部署到 {tools_dir}:")
    for name, path in deployed:
        size = os.path.getsize(path)
        size_str = f"{size / 1024 / 1024:.1f}MB" if size > 1024 * 1024 else f"{size / 1024:.0f}KB"
        print(f"   {name:20s}  {size_str:>6s}  {path}")

    # 第 3 步：渲染 sh wrapper（$AGENT_TOOLS_DIR 指向 tools/ 自身）
    tools_def = load_tools_def(TOOLS_DEF_PATH)
    placeholders = build_placeholders(config, tools_dir)
    wrappers = render_sh_wrappers(tools_def, placeholders, tools_dir)

    print()
    print(f"📜 Shell wrapper ({len(wrappers)}):")
    for name, path in wrappers:
        print(f"   {name:20s}  {path}")

    # 验证
    print()
    print("🔍 验证占位符替换:")
    ok = _verify_placeholders(wrappers)

    # 清理
    _cleanup_build(BUILD_DIR)

    print()
    if ok:
        print(f"✅ 全部完成，共 {len(deployed)} 个 ELF + {len(wrappers)} 个 wrapper")
    else:
        print(f"⚠️  有未替换的占位符，请检查", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
