#!/usr/bin/env bash
# helpers.sh — 测试公共函数
# 被 test/cases/*.sh source 使用
# 路径从项目根目录的 config.json 自动读取

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG="$ROOT_DIR/config.json"

WORKSPACE_DIR="$(python3 -c "import json; print(json.load(open('$CONFIG'))['worker_workspace'])")"
BOARD_PATH="$(python3 -c "import json; print(json.load(open('$CONFIG'))['board_path'])")"
TOOLS_DIR="$WORKSPACE_DIR/tools"

if [ ! -d "$TOOLS_DIR" ]; then
  echo "❌ 工具目录不存在: $TOOLS_DIR" >&2
  echo "   请先运行 test/run.sh 或 setup.sh" >&2
  exit 1
fi

# ── 调用 tool wrapper ──
# 用法: tool <tool-name> [args...]
tool() {
  local name="$1"; shift
  "$TOOLS_DIR/$name" "$@"
}

# ── 现场恢复 ──

board_nuke() {
  # 彻底清空 board 目录（删所有文件）
  rm -rf "$BOARD_PATH"
  mkdir -p "$BOARD_PATH"
}

plan_clear() {
  rm -f "$WORKSPACE_DIR/plan/current_plan.json"
}

# ── 测试框架 ──

test_start() {
  local name="$1"
  echo ""
  echo "━━━ $name ━━━"
}

test_pass() {
  echo "  ✅ 通过"
}

test_fail() {
  echo "  ❌ 失败" >&2
  exit 1
}

# 执行一条命令并描述，失败则 return 1
check() {
  local desc="$1"
  shift
  if "$@"; then
    echo "  ✅ $desc"
    return 0
  else
    echo "  ❌ $desc" >&2
    return 1
  fi
}

# 执行命令并检查输出含某字符串，失败则 return 1
check_contains() {
  local desc="$1" expected="$2"
  shift 2
  local output; output=$("$@" 2>/dev/null) || true
  if echo "$output" | grep -qF "$expected"; then
    echo "  ✅ $desc"
    return 0
  else
    echo "  ❌ $desc (未包含: $expected)" >&2
    return 1
  fi
}
