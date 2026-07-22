#!/usr/bin/env python3
"""
feishu_sync — Bulletin Board → 飞书 消息同步守护进程

架构：
  1. 定时（500ms）检查 board index.json 的 last_index
  2. 计算 diff = now_last - last_seen；diff 在 [0, 30] 内视为合法
  3. 对 diff > 0 的每条新留言，调 bb-get <index> 取内容
  4. 通过飞书 API 发送给目标用户

参数（通过 sh wrapper 填充）:
  --board-dir       :   留言板目录（含 index.json）
  --tools-dir       :   tools 目录（含 bb-get ELF / wrapper）
  --feishu-app-id   :   飞书 App ID
  --feishu-app-secret : 飞书 App Secret
  --leader-name     :   被通知者在留言板上的 speaker 名
  --leader-open-id  :   被通知者的飞书 open_id（非空时优先于联系人查询）

行为：
  - 首次启动：记下当前 last_index，不发送任何消息
  - 定时检查：diff ∈ [0, 30] → 遍历 (last_seen, now_last]，逐条 bb-get 发到飞书
  - diff 非法（负值或 >30）→ 输出告警日志，last_seen 原地不动
  - 启动时检查 token 有效性，失败直接退出

约定：
  - 不发任何已有的旧留言（首次启动仅记录 index，不同步历史）
  - 每轮同步完成更新 last_seen = now_last

依赖：
  - feishu_api.py 通过 PYTHONPATH 或同目录引用
  - sys.path 在 pyinstaller 打包时需包含 core/feishu/
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ── 路径配置 ─────────────────────────────────────────────────────

# pyinstaller --add-data 把 feishu_api.py 打包进 ELF，解压后在同级目录
# 源码开发时从 core/feishu/ 引用
# 注意: 只加 *存在* 的路径到 sys.path，否则 pyinstaller bootloader 的
#       path finder hook 会因找不到目录而崩溃，且输出不可见。
_THIS_DIR = Path(__file__).parent.resolve()
_FEISHU_API_DIR = _THIS_DIR.parent  # core/feishu/（源码）或 pyinstaller 解压同目录
for p in [str(_THIS_DIR), str(_FEISHU_API_DIR)]:
    if p not in sys.path and os.path.isdir(p):
        sys.path.insert(0, p)

try:
    from feishu_api import get_token, send_text_message
except ImportError:
    # 回退：作为独立模块 import（pyinstaller ELF 提取后可能 sys.path 不同）
    import feishu_api as _fa
    get_token = _fa.get_token
    send_text_message = _fa.send_text_message

# print with flush for pyinstaller ELF
_p = print


# ── 常量 ─────────────────────────────────────────────────────────

POLL_INTERVAL = 0.5        # 检查间隔（秒）
MAX_LEGACY_DIFF = 30       # 最大合法 index 差异
RUNNER_NAME = "feishu-sync"

# ── 命令行 ───────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="feishu_sync — Bulletin Board → 飞书 消息同步",
    )
    p.add_argument("--board-dir", required=True, help="留言板目录（含 index.json）")
    p.add_argument("--tools-dir", required=True, help="tools 目录（含 bb-get）")
    p.add_argument("--feishu-app-id", required=True, help="飞书 App ID")
    p.add_argument("--feishu-app-secret", required=True, help="飞书 App Secret")
    p.add_argument("--leader-name", required=True, help="被通知者在留言板上的 speaker 名")
    p.add_argument("--leader-open-id", default="", help="被通知者的飞书 open_id（可选，非空时免联系人查询）")
    return p.parse_args(argv)


# ── index 读写 ────────────────────────────────────────────────────


def _read_index(board_dir: str) -> int | None:
    """读取 index.json 中的 last_index。文件不存在或格式错误返回 None。"""
    path = os.path.join(board_dir, "index.json")
    try:
        with open(path) as f:
            data = json.load(f)
            return data.get("last_index")
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


# ── bb-get ─────────────────────────────────────────────────────────


def _bb_get(tools_dir: str, index: int) -> str | None:
    """调 bb-get <index>，返回留言内容（含时间戳和发言人）。失败返回 None。"""
    get_path = os.path.join(tools_dir, "bb-get")
    if not os.path.isfile(get_path):
        # 试 ELF 名 bb_get（pyinstaller 产物）
        get_path = os.path.join(tools_dir, "bb_get")

    if not os.path.isfile(get_path):
        _p(f"[{RUNNER_NAME}] ❌ 未找到 bb-get / bb_get 工具在 {tools_dir}", file=sys.stderr)
        return None

    try:
        result = subprocess.run(
            [get_path, str(index)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        text = result.stdout.strip()
        return text if text else None
    except (subprocess.TimeoutExpired, OSError) as e:
        _p(f"[{RUNNER_NAME}] ⚠ bb-get({index}) 失败: {e}", file=sys.stderr)
        return None


# ── 飞书解析 ─────────────────────────────────────────────────────


def _resolve_open_id(app_id: str, app_secret: str, leader_name: str) -> str | None:
    """通过飞书联系人 API 按姓名查询 open_id。

    使用 GET /contact/v3/users 遍历分页，返回第一个 name 匹配的 open_id。
    失败返回 None。
    """
    token = get_token(app_id, app_secret)
    if not token:
        _p(f"[{RUNNER_NAME}] ❌ 获取飞书 token 失败", file=sys.stderr)
        return None

    # 尝试请求联系人列表
    page_token = None
    base_url = "https://open.feishu.cn/open-apis/contact/v3/users"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    import urllib.request
    import urllib.error

    while True:
        params = f"page_size=50"
        if page_token:
            params += f"&page_token={page_token}"
        url = f"{base_url}?{params}"

        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
            _p(f"[{RUNNER_NAME}] ❌ 联系人查询失败: {e}", file=sys.stderr)
            return None

        if data.get("code") != 0:
            _p(f"[{RUNNER_NAME}] ❌ 联系人 API 错误: code={data.get('code')} msg={data.get('msg', '')}",
                  file=sys.stderr)
            return None

        items = data.get("data", {}).get("items", [])
        for user in items:
            name = user.get("name", "")
            if name == leader_name:
                return user.get("open_id")

        has_more = data.get("data", {}).get("has_more", False)
        if not has_more:
            break
        page_token = data.get("data", {}).get("page_token")

    return None


# ── 消息格式化 ─────────────────────────────────────────────────────


def _format_sync_message(text: str | None, leader_name: str) -> str:
    """把 bb-get 返回的留言行格式化为飞书可读消息。

    原始格式：
      2026-07-22 14:30 [Danis] (#42) 这里一条留言
      这是续行

    Args:
        text: bb-get 原始输出，可为 None
    Returns:
        格式化后的消息文本
    """
    if not text:
        return ""
    return text


# ── 主循环 ────────────────────────────────────────────────────────


def main_loop(args: argparse.Namespace):
    board_dir = args.board_dir
    tools_dir = args.tools_dir
    app_id = args.feishu_app_id
    app_secret = args.feishu_app_secret
    leader_name = args.leader_name
    leader_open_id = args.leader_open_id

    # ── 启动校验 ───────────────────────────────────────────────
    _p(f"[{RUNNER_NAME}] 🚀 启动 Bulletin → Feishu 同步守护进程")

    # 1. 检查 board 目录存在
    if not os.path.isdir(board_dir):
        _p(f"[{RUNNER_NAME}] ❌ board_dir 不存在: {board_dir}", file=sys.stderr)
        sys.exit(1)

    # 2. 获取飞书 token（验证凭证有效性）
    token = get_token(app_id, app_secret)
    if not token:
        _p(f"[{RUNNER_NAME}] ❌ 飞书 token 获取失败（app_id/app_secret 无效）", file=sys.stderr)
        sys.exit(1)
    _p(f"[{RUNNER_NAME}] ✅ 飞书 token 获取成功")

    # 3. 确认目标 open_id
    if leader_open_id:
        open_id = leader_open_id
        _p(f"[{RUNNER_NAME}] ✅ 使用配置中的 open_id: {open_id[:12]}...")
    else:
        _p(f"[{RUNNER_NAME}] 🔍 查询联系人 \"{leader_name}\" 的 open_id...")
        open_id = _resolve_open_id(app_id, app_secret, leader_name)
        if not open_id:
            _p(f"[{RUNNER_NAME}] ❌ 未找到 \"{leader_name}\"，请检查配置或联系人在当前租户下",
                  file=sys.stderr)
            sys.exit(1)
        _p(f"[{RUNNER_NAME}] ✅ 找到 {leader_name}: open_id={open_id}")

    # 4. 记录初始 index
    last_seen = _read_index(board_dir)
    if last_seen is None:
        _p(f"[{RUNNER_NAME}] ❌ 无法读取 index.json（未初始化？先跑 setup）", file=sys.stderr)
        sys.exit(1)
    _p(f"[{RUNNER_NAME}] 📍 初始 last_index = {last_seen}（不同步历史）")

    # 5. 发一条启动通知
    greet = f"🟢 {RUNNER_NAME} — 同步守护进程已启动\n   留言板: {board_dir}\n   通知到: {leader_name} ({open_id[:12]}...)"
    send_text_message(open_id, token, greet)
    _p(f"[{RUNNER_NAME}] ✅ 已发送启动通知到飞书")

    # ── 循环 ─────────────────────────────────────────────────
    _p(f"[{RUNNER_NAME}] 🔄 开始轮询（{POLL_INTERVAL}s 间隔）...")
    while True:
        time.sleep(POLL_INTERVAL)

        now_last = _read_index(board_dir)
        if now_last is None:
            continue

        diff = now_last - last_seen

        # 无变化
        if diff == 0:
            continue

        # diff 非法
        if diff < 0 or diff > MAX_LEGACY_DIFF:
            _p(f"[{RUNNER_NAME}] ⚠ index 跳变: last_seen={last_seen}, now_last={now_last}, diff={diff}（跳过本轮）",
                  file=sys.stderr)
            continue

        # diff 合法：逐条同步
        _p(f"[{RUNNER_NAME}] 📨 同步 {diff} 条新留言（#{last_seen + 1} ~ #{now_last}）")
        for i in range(last_seen + 1, now_last + 1):
            msg_text = _bb_get(tools_dir, i)
            if msg_text is None:
                _p(f"[{RUNNER_NAME}] ⚠ 获取留言 #{i} 失败，跳过", file=sys.stderr)
                continue
            # 去掉续行缩进，保持可读
            clean_text = "\n".join(line.strip() for line in msg_text.split("\n"))
            result = send_text_message(open_id, token, clean_text)
            if result.get("code") != 0:
                _p(f"[{RUNNER_NAME}] ⚠ 发送留言 #{i} 失败: {result.get('msg', '')}", file=sys.stderr)
                # token 过期时刷新
                if result.get("code") == 99991663 or "token" in str(result.get("msg", "")).lower():
                    new_token = get_token(app_id, app_secret)
                    if new_token:
                        token = new_token
                        # 重试
                        result2 = send_text_message(open_id, token, clean_text)
                        if result2.get("code") != 0:
                            _p(f"[{RUNNER_NAME}] ❌ 重试仍失败: {result2.get('msg', '')}", file=sys.stderr)
                    else:
                        _p(f"[{RUNNER_NAME}] ❌ token 刷新失败，退出", file=sys.stderr)
                        sys.exit(1)
            else:
                _p(f"[{RUNNER_NAME}]   ✅ #{i} 已同步")

        # 更新 last_seen
        last_seen = now_last


# ── 入口 ──────────────────────────────────────────────────────────


def main():
    args = parse_args()
    try:
        main_loop(args)
    except KeyboardInterrupt:
        _p(f"\n[{RUNNER_NAME}] 👋 收到 Ctrl+C，退出")
        sys.exit(0)


if __name__ == "__main__":
    main()
