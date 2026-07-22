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
echo "🧹 [0/7] 清空工作区 & 留言板..."
# 清空工作区内容（保留目录 inode，避免已 cd 进来的 shell 窗口悬空）
rm -rf "$WORKER_WS"/*
rm -rf "$BOARD_PATH"
mkdir -p "$WORKER_WS" "$BOARD_PATH"

# ── 第 1 步：渲染工具（pyinstaller + sh wrapper）───────────
echo "📦 [1/7] 打包 agent 工具..."
python3 "$CORE_DIR/agent_tools/render.py"

# ── 第 2 步：cron prompt ──────────────────────────
echo ""
echo "📜 [2/7] 渲染 cron prompt..."
python3 "$CORE_DIR/prompt/render.py"
echo ""

# ── 第 3 步：SKILL.md ────────────────────────────
echo "📖 [3/7] 渲染 SKILL.md..."
python3 "$CORE_DIR/skill/render.py"

# 部署到工作区
cp "$OUTPUT_DIR/SKILL.md" "$WORKER_WS/SKILL.md"
cp "$OUTPUT_DIR/PROMPT.md" "$WORKER_WS/PROMPT.md"
python3 -c "import sys; sys.path.insert(0, '$CORE_DIR'); from skill.render import load_config, render_tools_usage; c = load_config('$CORE_DIR/../config.json'); render_tools_usage(c, '$WORKER_WS/tools')"
echo "   → SKILL.md 已部署"
echo "   → PROMPT.md 已部署"
echo "   → TOOLS_USAGE.md 已部署"

# ── 第 4 步：任务计划工具 ────────────────────────────
echo ""
echo "📋 [4/7] 渲染任务计划工具..."
python3 "$CORE_DIR/task_plan/render.py"

# ── 第 5 步：初始化状态与目录 ──────────────────────────
echo ""
echo "🎯 [5/7] 初始化状态..."
python3 -c "import json; json.dump({'status': 'IDLE'}, open('$BOARD_PATH/status.json', 'w'))"
echo "   → 状态已初始化为 IDLE"

# ── 第 6 步：渲染 cron_daemon ──────────────────────────
echo ""
echo "🕒 [6/7] 渲染 cron_daemon（独立调度进程）..."
python3 "$CORE_DIR/cron_daemon/render.py"
echo ""

# ── 第 7 步：渲染 terminal ──────────────────────────
echo ""
echo "🖥️  [7/7] 渲染 terminal（交互式终端 TUI）..."
python3 "$CORE_DIR/terminal/render.py"
echo ""

# ── 第 8 步：渲染 feishu_sync ──────────────────────────
echo ""
echo "✈️  [8/8] 渲染 feishu_sync（飞书消息同步守护进程）..."
python3 "$CORE_DIR/user_tool/feishu_sync/render.py"
echo ""

echo "✅ 安装完成"
echo "工作区: $WORKER_WS"
echo "留言板: $BOARD_PATH"
echo ""
echo "💡 cron_daemon 使用:"
echo "   cd $WORKER_WS/user_tools/cron_daemon/"
echo "   启动: ./run_cron_daemon.sh"
echo "   停止: ./stop_cron_daemon.sh"
echo ""
echo "💻 terminal 使用:"
echo "   cd $WORKER_WS/user_tools/terminal/"
echo "   ./run_terminal.sh"
echo ""
echo "✈️  feishu_sync 使用:"
echo "   cd $WORKER_WS/user_tools/feishu/"
echo "   启动: ./run_feishu.sh"
echo "   停止: Ctrl+C"

