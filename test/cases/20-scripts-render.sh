#!/usr/bin/env bash
# 20-scripts-render.sh — v2 工作流脚本渲染测试
# 验证 setup.sh 渲染后，scripts/ 目录包含 v2 需要的所有 .md 文件

. "$(dirname "$0")/../helpers.sh"

test_start "v2 工作流脚本渲染"

# ── 验证 scripts 目录存在 ──
if [ ! -d "$WORKSPACE_DIR/scripts" ]; then
  echo "  ❌ scripts/ 目录不存在" >&2
  echo "     请先运行 setup.sh" >&2
  exit 1
fi

echo "  ✅ scripts/ 目录存在"

# ── 逐个检查必需文件 ──
required_files=(
  "update_task_plan.md"
  "execute_task_plan.md"
  "new_task_plan.md"
  "task_plan_format.md"
  "task_plan_strategy.md"
  "bb_plan_format.md"
)

all_ok=true
for f in "${required_files[@]}"; do
  path="$WORKSPACE_DIR/scripts/$f"
  if [ -f "$path" ]; then
    size=$(wc -c < "$path")
    echo "  ✅ $f ($size bytes)"
  else
    echo "  ❌ $f 缺失" >&2
    all_ok=false
  fi
done

# ── 验证内容不为空 ──
for f in "${required_files[@]}"; do
  path="$WORKSPACE_DIR/scripts/$f"
  if [ -f "$path" ] && [ ! -s "$path" ]; then
    echo "  ❌ $f 为空文件" >&2
    all_ok=false
  fi
done

# ── 验证关键内容片段存在（抽样检查）──
check_contains "update_task_plan.md 含 plan 操作" "bb-plan-format-check" \
  cat "$WORKSPACE_DIR/scripts/update_task_plan.md"

check_contains "new_task_plan.md 含 plan 操作" "bb-plan-format-check" \
  cat "$WORKSPACE_DIR/scripts/new_task_plan.md"

check_contains "execute_task_plan.md 含 bb-plan-show-next" "bb-plan-show-next" \
  cat "$WORKSPACE_DIR/scripts/execute_task_plan.md"

check_contains "task_plan_format.md 含 JSON 结构说明" "briefing" \
  cat "$WORKSPACE_DIR/scripts/task_plan_format.md"

check_contains "task_plan_strategy.md 含设计原则" "子任务" \
  cat "$WORKSPACE_DIR/scripts/task_plan_strategy.md"

check_contains "bb_plan_format.md 含 JSON 结构说明" "briefing" \
  cat "$WORKSPACE_DIR/scripts/bb_plan_format.md"

# ── 验证新增 sh wrapper ──
new_wrappers=(
  "bb-plan-show-brief"
  "bb-plan-format-check"
  "bb-plan-archive"
  "bb-worker-post-no-mission"
  "bb-worker-post-new-mission"
  "bb-worker-post-update-mission"
  "bb-worker-post-execute"
)

for tw in "${new_wrappers[@]}"; do
  path="$TOOLS_DIR/$tw"
  if [ -f "$path" ]; then
    echo "  ✅ $tw"
  else
    echo "  ❌ 缺少 wrapper: $tw" >&2
    all_ok=false
  fi
done

# ── 验证新增子命令可用性 ──
tool bb-plan-show-brief 2>/dev/null | head -3

if [ "$all_ok" = true ]; then
  test_pass
else
  test_fail
fi
