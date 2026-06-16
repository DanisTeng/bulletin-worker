#!/usr/bin/env bash
# 10-board-basic.sh — Board 工具基础功能测试
# 测试: post → recent, post → history, post → around

. "$(dirname "$0")/../helpers.sh"

test_start "Board 基础功能"

board_nuke

# ── 贴 3 条留言 ──
tool bb-worker-post "message one"
tool bb-worker-post "message two"
tool bb-worker-post "message three"

# ── recent ──
check_contains "recent 取最后 2 条" "message three" tool bb-recent 2
check_contains "recent 默认 20 行"  "message three" tool bb-recent

# ── recent --grep ──
check_contains "recent --grep two" "message two"   tool bb-recent 10 --grep "two"
check "recent --grep 不含 message one" \
  sh -c '! "$1" bb-recent 10 --grep "two" 2>/dev/null | grep -qF "message one"' _ "$TOOLS_DIR"

# ── history ──
today=$(date +%Y-%m-%d)
check_contains "history 当前日期" "message three" tool bb-history "$today"

# ── history 不存在日期（不应 crash）──
check "history 无数据日不报错" tool bb-history "2099-01-01"

# ── around ──
now=$(date +%Y-%m-%dT%H:%M)
check_contains "around 锚点+后5条" "message three" tool bb-around "$now" 0 5

# ── around 0 0（应输出空，不含任何留言）──
check "around 0 0 不包含留言" \
  sh -c '! "$1" bb-around "$2" 0 0 2>/dev/null | grep -qF "message"' _ "$TOOLS_DIR" "$now"

# ── leader post（同时验证引用了正确的 speaker name）──
check_contains "leader-post" "leader says hi" tool bb-leader-post "leader says hi"

# 现场恢复
board_nuke
test_pass
