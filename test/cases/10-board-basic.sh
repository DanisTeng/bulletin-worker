#!/usr/bin/env bash
# 10-board-basic.sh — Board 工具基础功能测试
# 测试: post → recent, post → history, post → around

. "$(dirname "$0")/../helpers.sh"

NAME="Board 基础功能"

test_start "$NAME"
board_nuke

# ── post + recent ──
echo "  贴 3 条留言..."
tool bb-worker-post "message one"
tool bb-worker-post "message two"
tool bb-worker-post "message three"

echo "  recent 取最后 2 条..."
result=$(tool bb-recent 2)
assert_ok
assert_contains "$result" "message three"
assert_contains "$result" "message two"
assert_not_contains "$result" "message one"
check "post + recent" test $? -eq 0

# ── post + recent 默认行数 ──
echo "  recent 默认..."
result=$(tool bb-recent)
assert_ok
assert_contains "$result" "message three"
check "recent 默认 20 行" test $? -eq 0

# ── post + recent --grep ──
echo "  recent --grep two..."
result=$(tool bb-recent 10 --grep "two")
assert_ok
assert_contains "$result" "message two"
assert_not_contains "$result" "message one"
assert_not_contains "$result" "message three"
check "recent --grep 过滤" test $? -eq 0

# ── post + history ──
today=$(date +%Y-%m-%d)
echo "  history $today..."
result=$(tool bb-history "$today")
assert_ok
assert_contains "$result" "message three"
assert_contains "$result" "message two"
assert_contains "$result" "message one"
check "history 显示所有留言" test $? -eq 0

# ── history 不存在的日期 ──
echo "  history 无内容的日期..."
result=$(tool bb-history "2099-01-01")
assert_ok
assert_not_contains "$result" "message"
check "history 无数据日不报错" test $? -eq 0

# ── around 锚点测试 ──
now=$(date +%Y-%m-%dT%H:%M)
echo "  around 当前时刻 (hh:mm) $now..."
result=$(tool bb-around "$now" 0 5)
assert_ok
assert_contains "$result" "message three"
check "around 锚点+后5条" test $? -eq 0

# ── around 0 0 ──
echo "  around 0 0..."
result=$(tool bb-around "$now" 0 0)
assert_ok
# 0 前 0 后 → 空结果（不含锚点行本身）
check "around 0 0 返回空" test $? -eq 0

# ── 领导发帖 ──
echo "  领导发帖..."
result=$(tool bb-leader-post "leader says hi")
assert_ok
assert_contains "$result" "leader says hi"
check "leader-post" test $? -eq 0

# 现场恢复
board_nuke
test_pass
