#!/usr/bin/env bash
# setup.sh — Bulletin Worker 一键安装脚本
# 用法:  编辑 config.json → 运行 ./setup.sh
# 输出:  $worker_workspace/tools/ (ELF + sh wrapper)
#        $worker_workspace/SKILL.md
#        $worker_workspace/PROMPT.md (stdout 也会输出)
#
# 依赖:  python3, pyinstaller, git (可选)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CORE_DIR="$SCRIPT_DIR/core"
OUTPUT_DIR="$SCRIPT_DIR/output"

if [ ! -f "$SCRIPT_DIR/config.json" ]; then
  echo "❌ 未找到 config.json，请先编辑配置"
  exit 1
fi

echo "🚀 Bulletin Worker 安装开始"
echo ""

# ── 第 1 步：渲染工具（pyinstaller + sh wrapper）─
echo "📦 [1/3] 打包 agent 工具..."
python3 "$CORE_DIR/render_agent_tools.py"

# ── 第 2 步：cron prompt ──────────────────────────
echo ""
echo "📜 [2/3] 渲染 cron prompt..."
PROMPT=$(python3 "$CORE_DIR/render_prompt.py")
echo ""
echo "──────────────────────────────────────"
echo "Cron prompt（也可用于 stdio 输入）:"
echo "──────────────────────────────────────"
echo "$PROMPT"
echo "──────────────────────────────────────"
echo ""

# ── 第 3 步：SKILL.md ────────────────────────────
echo "📖 [3/3] 渲染 SKILL.md..."
python3 "$CORE_DIR/render_skill_md.py"

# 复制到工作区
WORKER_WS=$(python3 -c "import json; print(json.load(open('$SCRIPT_DIR/config.json'))['worker_workspace'])")
mkdir -p "$WORKER_WS"
cp "$OUTPUT_DIR/SKILL.md" "$WORKER_WS/SKILL.md"
cp "$OUTPUT_DIR/PROMPT.md" "$WORKER_WS/PROMPT.md"
echo "   → 已部署到 $WORKER_WS/"

echo ""
echo "✅ 安装完成"
echo "工作区: $WORKER_WS"
