#!/usr/bin/env bash
# run.sh — Bulletin Worker 自测入口
# 用法:   cd bulletin-worker && bash test/run.sh
# 效果:   full setup → 逐个跑 case → 清理

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEST_DIR="/tmp/bb-test-$$"

# ── 生成测试 config ──
echo "🧪 生成测试 config..."
sed "s|TEMPLATE|$$|g" "$ROOT_DIR/test/config_test.json" > /tmp/bb-config-$$.json
cp /tmp/bb-config-$$.json "$ROOT_DIR/config.json"

# 提取路径用于校验
WS_DIR=$(python3 -c "import json; print(json.load(open('$ROOT_DIR/config.json'))['worker_workspace'])")
BD_DIR=$(python3 -c "import json; print(json.load(open('$ROOT_DIR/config.json'))['board_path'])")

echo "   工作区: $WS_DIR"
echo "   留言板: $BD_DIR"

# ── full setup ──
echo ""
echo "🔧 Full setup..."
cd "$ROOT_DIR"
bash setup.sh
echo ""

# ── 逐个跑 case ──
ALL_PASS=true
FAILED_CASES=""

for case in "$ROOT_DIR/test/cases/"*.sh; do
  name="$(basename "$case" .sh)"
  echo "▶️  [$name]"
  if bash "$case" "$TEST_DIR"; then
    echo "  ✅ [$name] 通过"
  else
    echo "  ❌ [$name] 失败" >&2
    ALL_PASS=false
    FAILED_CASES="$FAILED_CASES $name"
  fi
  echo ""
done

# ── 清理 ──
echo "🧹 清理..."
rm -rf "$TEST_DIR" /tmp/bb-config-$$.json

# ── 结果汇总 ──
if [ "$ALL_PASS" = true ]; then
  echo "━━━━━━━━━━━━━━━━━━━━"
  echo "🎉 全部测试通过"
  echo "━━━━━━━━━━━━━━━━━━━━"
  exit 0
else
  echo "━━━━━━━━━━━━━━━━━━━━"
  echo "💥 以下 case 失败:$FAILED_CASES" >&2
  echo "━━━━━━━━━━━━━━━━━━━━"
  exit 1
fi
