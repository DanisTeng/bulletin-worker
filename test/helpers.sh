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

# ── 断言 ──

assert_ok() {
  local code=$?
  if [ "$code" -ne 0 ]; then
    echo "  ❌ 期望退出码 0，实际 $code" >&2
    return 1
  fi
}

assert_fail() {
  local code=$?
  if [ "$code" -eq 0 ]; then
    echo "  ❌ 期望非零退出码，实际 0" >&2
    return 1
  fi
}

assert_contains() {
  local output="$1"
  local expected="$2"
  if ! echo "$output" | grep -qF "$expected"; then
    echo "  ❌ 输出不包含: $expected" >&2
    echo "    实际输出: $output" >&2
    return 1
  fi
}

assert_not_contains() {
  local output="$1"
  local expected="$2"
  if echo "$output" | grep -qF "$expected"; then
    echo "  ❌ 输出不应包含: $expected" >&2
    return 1
  fi
}

assert_non_empty() {
  local output="$1"
  if [ -z "$output" ]; then
    echo "  ❌ 输出不应为空" >&2
    return 1
  fi
}

# ── 现场恢复 ──

board_clear() {
  rm -f "$BOARD_PATH"/*.json "$BOARD_PATH"/*.md "$BOARD_PATH"/*.txt 2>/dev/null
  # 清空目录后创建空文件以避免 board 工具报错（取决于实现）
  # bb_board 工具基于文件名日期，仅清理内容
  for f in "$BOARD_PATH"/*; do
    if [ -f "$f" ]; then
      : > "$f"
    fi
  done
}

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

# 执行一组检查，任一失败则测试整体失败
check() {
  local desc="$1"
  shift
  if "$@"; then
    echo "  ✅ $desc"
  else
    echo "  ❌ $desc" >&2
    return 1
  fi
}
