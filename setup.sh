#!/usr/bin/env bash
# setup.sh — Bulletin Worker 一键安装脚本
# 用法:  编辑 config.json → 运行 ./setup.sh
# 效果:  清空 $worker_workspace 和 $board_path → 全新部署
#        工具 / SKILL.md / PROMPT.md 全部就位
#
# 依赖:  python3, pyinstaller

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CORE_DIR="$SCRIPT_DIR/core"
OUTPUT_DIR="$SCRIPT_DIR/output"

if [ ! -f "$SCRIPT_DIR/config.json" ]; then
  echo "❌ 未找到 config.json，请先编辑配置"
  exit 1
fi

# ── 读配置 ─────────────────────────────────────────────────
WORKER_WS=$(python3 -c "
import json
c = json.load(open('$SCRIPT_DIR/config.json'))
print(c['worker_workspace'])
")
BOARD_PATH=$(python3 -c "
import json
c = json.load(open('$SCRIPT_DIR/config.json'))
print(c['board_path'])
")

echo "🚀 Bulletin Worker 安装开始"
echo "   工作区: $WORKER_WS"
echo "   留言板: $BOARD_PATH"
echo ""

# ── 安全校验 ──────────────────────────────────────────────
if [ -z "$WORKER_WS" ] || [ -z "$BOARD_PATH" ]; then
  echo "❌ worker_workspace 或 board_path 为空"
  exit 1
fi
if [ "$WORKER_WS" = "/" ] || [ "$BOARD_PATH" = "/" ]; then
  echo "❌ 不允许将根目录设为工作区或留言板"
  exit 1
fi
if echo "$BOARD_PATH" | grep -qF "$WORKER_WS"; then
  echo "❌ board 路径不能是 workspace 的子目录"
  echo "     请修改 config.json，让 board_path 独立于 worker_workspace"
  echo "     示例: board_path: \"/var/data/bulletin-board\""
  exit 1
fi

# ── 清空并重建目录 ─────────────────────────────────────────
echo "🧹 [0/3] 清空工作区 & 留言板..."
rm -rf "$WORKER_WS" "$BOARD_PATH"
mkdir -p "$WORKER_WS" "$BOARD_PATH"

# ── 第 1 步：渲染工具（pyinstaller + sh wrapper）───────────
echo "📦 [1/3] 打包 agent 工具..."
python3 "$CORE_DIR/render_agent_tools.py"

# ── 第 2 步：cron prompt ──────────────────────────
echo ""
echo "📜 [2/3] 渲染 cron prompt..."
python3 "$CORE_DIR/render_prompt.py"
echo ""

# ── 第 3 步：SKILL.md ────────────────────────────
echo "📖 [3/3] 渲染 SKILL.md..."
python3 "$CORE_DIR/render_skill_md.py"

# 部署到工作区
cp "$OUTPUT_DIR/SKILL.md" "$WORKER_WS/SKILL.md"
cp "$OUTPUT_DIR/PROMPT.md" "$WORKER_WS/PROMPT.md"
echo "   → SKILL.md 已部署"
echo "   → PROMPT.md 已部署"

# ── 第 4 步：任务计划工具 ────────────────────────────
echo ""
echo "📋 [4/3] 渲染任务计划工具..."
python3 "$CORE_DIR/render_task_plan.py"

echo ""
echo "✅ 安装完成"
echo "工作区: $WORKER_WS"
echo "留言板: $BOARD_PATH"
