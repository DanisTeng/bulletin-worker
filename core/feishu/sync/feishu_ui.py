#!/usr/bin/env python3
"""
feishu_ui — Bulletin Board ↔ 飞书 消息守护进程

功能（双向）:
  写回路（已有）:
    - 定时（500ms）检查 board index.json 的 last_index
    - 新留言 → 调 bb-get 取内容 → 飞书推送给 leader
  读回路（新增）:
    - FeishuReceiver WS 接收飞书 p2p 私聊消息
    - 文本消息 → 调 bb-leader-post 写入 board（等同于 leader 发言）
    - 文件消息（≤100MB）→ 下载到 $worker_workspace/feishu_download/
    - 文件消息（>100MB）→ 回复"文件太大"

参数（通过 sh wrapper 填充）:
  --board-dir           :   留言板目录（含 index.json）
  --tools-dir           :   tools 目录（含 bb-get / bb-leader-post wrapper）
  --worker-workspace    :   worker_workspace 路径（拼接 feishu_download/）
  --feishu-app-id       :   飞书 App ID
  --feishu-app-secret   :   飞书 App Secret
  --leader-name         :   被通知者在留言板上的 speaker 名
  --leader-open-id      :   被通知者的飞书 open_id（非空时优先于联系人查询）

依赖:
  - feishu_api.py 通过 PYTHONPATH 或同目录引用
  - receiver.py   通过 PYTHONPATH 或同目录引用
  - sys.path 在 pyinstaller 打包时需包含 core/feishu/
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── 路径配置 ─────────────────────────────────────────────────────

_THIS_DIR = Path(__file__).parent.resolve()
_FEISHU_API_DIR = _THIS_DIR.parent  # core/feishu/
for p in [str(_THIS_DIR), str(_FEISHU_API_DIR)]:
    if p not in sys.path and os.path.isdir(p):
        sys.path.insert(0, p)

import feishu_api as _fa
from receiver import FeishuReceiver
from receiver import TokenManager  # noqa: F401 重导出给外部 import

_p = print


# ── 常量 ─────────────────────────────────────────────────────────

POLL_INTERVAL = 0.5          # 索引轮询间隔（秒）
MAX_LEGACY_DIFF = 30         # 最大合法 index 差异
RUNNER_NAME = "feishu-ui"
RECEIVE_LOOP_SLEEP = 0.5    # 接收轮询间隔（秒）
FEISHU_DOWNLOAD_SUBDIR = "feishu_download"  # 文件下载子目录名
_LOG_SENDER_TRIM = 12        # 日志中 sender_id 截断长度
_LOG_MSG_TRIM = 18           # 日志中 message_id 截断长度
_FILE_DOWNLOAD_TIMEOUT = 60  # 下载超时（秒）


# ── 命令行 ───────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="feishu_ui — Bulletin Board ↔ 飞书 消息守护进程",
    )
    p.add_argument("--board-dir", required=True, help="留言板目录（含 index.json）")
    p.add_argument("--tools-dir", required=True, help="tools 目录（含 bb-get, bb-leader-post）")
    p.add_argument("--worker-workspace", required=True, help="worker_workspace 路径")
    p.add_argument("--feishu-app-id", required=True, help="飞书 App ID")
    p.add_argument("--feishu-app-secret", required=True, help="飞书 App Secret")
    p.add_argument("--leader-name", required=True, help="被通知者在留言板上的 speaker 名")
    p.add_argument("--leader-open-id", default="", help="被通知者的飞书 open_id（可选，非空时免联系人查询）")
    return p.parse_args(argv)


# ── index 读写 ────────────────────────────────────────────────────


def _read_index(board_dir: str) -> int | None:
    path = os.path.join(board_dir, "index.json")
    try:
        with open(path) as f:
            data = json.load(f)
            return data.get("last_index")
    except FileNotFoundError:
        return 0
    except PermissionError:
        _p(f"[{RUNNER_NAME}] ❌ 无权限读取 {path}", file=sys.stderr)
        return None
    except (json.JSONDecodeError, KeyError):
        return None


# ── bb-get ─────────────────────────────────────────────────────────


def _bb_get(tools_dir: str, index: int) -> str | None:
    get_path = os.path.join(tools_dir, "bb-get")
    if not os.path.isfile(get_path):
        _p(f"[{RUNNER_NAME}] ❌ 未找到 bb-get 在 {tools_dir}", file=sys.stderr)
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


# ── bb-leader-post ────────────────────────────────────────────────


def _bb_leader_post(tools_dir: str, text: str) -> bool:
    post_path = os.path.join(tools_dir, "bb-leader-post")
    if not os.path.isfile(post_path):
        _p(f"[{RUNNER_NAME}] ❌ 未找到 bb-leader-post 在 {tools_dir}", file=sys.stderr)
        return False
    try:
        result = subprocess.run(
            [post_path, text],
            capture_output=True, text=True, timeout=10,
        )
        ok = result.returncode == 0
        if not ok:
            _p(f"[{RUNNER_NAME}] ⚠ bb-leader-post 返回码 {result.returncode}: {result.stderr[:200]}",
               file=sys.stderr)
        return ok
    except (subprocess.TimeoutExpired, OSError) as e:
        _p(f"[{RUNNER_NAME}] ⚠ bb-leader-post 失败: {e}", file=sys.stderr)
        return False


# ── 飞书解析 ─────────────────────────────────────────────────────


def _resolve_open_id(app_id: str, app_secret: str, leader_name: str) -> str | None:
    token = _fa.get_token(app_id, app_secret)
    if not token:
        _p(f"[{RUNNER_NAME}] ❌ 获取飞书 token 失败", file=sys.stderr)
        return None

    page_token = None
    base_url = "https://open.feishu.cn/open-apis/contact/v3/users"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

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


# ── 日志辅助 ──────────────────────────────────────────────────────


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ── 自定义 WS 消息处理器 ─────────────────────────────────────────


def make_ws_handler(tools_dir: str, download_dir: str,
                    token_mgr: TokenManager):
    """创建 FeishuReceiver WS 回调的替换函数。

    只处理 p2p 私聊：
      - 文本消息 → 写 MessageStore（供 _receive_loop 消费）
      - 文件消息 → 直接下载或回复超限

    返回 (handler_fn, text_store): handler_fn 是 _on_ws_message 替换函数，
    text_store 是消息队列（list + threading.Lock）供 _receive_loop 消费。
    """

    text_store: list[dict] = []
    text_lock = threading.Lock()

    os.makedirs(download_dir, exist_ok=True)

    def _ts_log() -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def _file_output_path(file_name: str, message_id: str) -> str:
        safe_name = file_name if file_name else "unknown_file"
        return os.path.join(download_dir, f"{message_id}_{safe_name}")

    def handler(data):
        """替换 FeishuReceiver._on_ws_message。"""
        event = data.event
        if not event or not event.message:
            return

        msg_obj = event.message
        message_id = getattr(msg_obj, 'message_id', '')
        if not message_id:
            return

        # chat_type 过滤：只处理 p2p 私聊
        chat_type = getattr(msg_obj, 'chat_type', '') or ''
        if chat_type != "p2p":
            return

        msg_type = getattr(msg_obj, 'message_type', '') or ''

        # sender_id
        event_sender = getattr(event, 'sender', None)
        sender_id = ''
        if event_sender:
            sid_obj = getattr(event_sender, 'sender_id', None)
            if sid_obj is not None:
                sender_id = getattr(sid_obj, 'open_id', '') or ''
        if not sender_id:
            _p(f"[{_ts_log()}] ⚠ 丢弃消息 {message_id[:_LOG_MSG_TRIM]}: 缺少 sender_id")
            return

        if msg_type == "text":
            _handle_text_msg(message_id, sender_id, msg_obj)
        else:
            _handle_file_msg(message_id, sender_id, msg_obj, msg_type)

    def _extract_text(msg_obj) -> str:
        raw = getattr(msg_obj, 'content', '')
        if not raw:
            return ""
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed.get("text", str(parsed))
            return str(parsed)
        except (json.JSONDecodeError, TypeError):
            return str(raw)

    def _handle_text_msg(message_id: str, sender_id: str, msg_obj):
        text = _extract_text(msg_obj)
        if not text:
            return

        msg = {
            "message_id": message_id,
            "sender_id": sender_id,
            "text": text,
            "recv_time": time.time(),
        }

        with text_lock:
            text_store.append(msg)

        preview = text[:40].replace("\n", " ")
        _p(f"[{_ts_log()}] 📩 p2p 文本 ({sender_id[:_LOG_SENDER_TRIM]}...): {preview}")

    def _handle_file_msg(message_id: str, sender_id: str, msg_obj, msg_type: str):
        raw = getattr(msg_obj, 'content', '')
        if not raw:
            _p(f"[{_ts_log()}] ⚠ 文件消息无 content, id={message_id[:_LOG_MSG_TRIM]}")
            return

        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            _p(f"[{_ts_log()}] ⚠ 文件消息 content 解析失败, id={message_id[:_LOG_MSG_TRIM]}")
            return

        if not isinstance(parsed, dict):
            return

        file_key = parsed.get("file_key", "") or parsed.get("image_key", "") or ""
        file_name = parsed.get("file_name", "") or "unknown"
        resource_type = "file" if parsed.get("file_key") else "image"

        if not file_key:
            _p(f"[{_ts_log()}] ⚠ 文件消息无 file_key, id={message_id[:_LOG_MSG_TRIM]}")
            return

        output_path = _file_output_path(file_name, message_id)

        # 去重
        if os.path.exists(output_path):
            size_kb = os.path.getsize(output_path) / 1024
            _p(f"[{_ts_log()}] 📎 文件已存在 (跳过) {file_name} ({size_kb:.1f}KB) -> {output_path}")
            return

        token = token_mgr.get()
        if not token:
            _p(f"[{_ts_log()}] ⚠ 文件 {file_name} 跳过: token 不可用")
            return

        _p(f"[{_ts_log()}] 📥 下载文件 {file_name} ({resource_type}), id={message_id[:_LOG_MSG_TRIM]}")

        result = _fa.download_resource(
            message_id, file_key, token,
            resource_type=resource_type,
            output_path=output_path,
            timeout=_FILE_DOWNLOAD_TIMEOUT,
        )

        if result.get("code") == 0:
            size_kb = result.get("size", 0) / 1024
            _p(f"[{_ts_log()}] ✅ 文件已保存 {file_name} ({size_kb:.1f}KB) -> {output_path}")
        else:
            err_msg = result.get("msg", "未知错误")
            _p(f"[{_ts_log()}] ⚠ 文件 {file_name} 下载失败: {err_msg}")

            # 文件太大 → 回复用户
            if "超出大小限制" in err_msg or "100MB" in err_msg:
                token_retry = token_mgr.get()
                if token_retry:
                    _fa.send_text_message(
                        sender_id, token_retry,
                        "文件太大了，请发送小于 100MB 的文件 🙏"
                    )
                    _p(f"[{_ts_log()}] ✅ 已回复超限提示")

    return handler, text_store, text_lock


# ── 文本消费循环 ──────────────────────────────────────────────────


def _receive_loop(tools_dir: str, text_store: list,
                  text_lock: threading.Lock, stop_event: threading.Event):
    """后台线程：消费 text_store 中的 p2p 文本消息，写入留言板。"""
    processed_ids: set[str] = set()

    while not stop_event.is_set():
        time.sleep(RECEIVE_LOOP_SLEEP)

        batch: list[dict] = []
        with text_lock:
            i = 0
            while i < len(text_store):
                msg = text_store[i]
                if msg["message_id"] in processed_ids:
                    text_store.pop(i)
                    continue
                batch.append(msg)
                i += 1

        for msg in batch:
            msg_id = msg["message_id"]
            processed_ids.add(msg_id)
            text = msg["text"]
            sender_id = msg["sender_id"]

            preview = text[:40].replace("\n", " ")
            _p(f"[{_ts()}] 📩 写入留言板 ({sender_id[:_LOG_SENDER_TRIM]}...): {preview}")

            if _bb_leader_post(tools_dir, text):
                _p(f"[{_ts()}] ✅ 已写入留言板")
            else:
                _p(f"[{_ts()}] ❌ 写入留言板失败", file=sys.stderr)

        # 定期清理 processed_ids（防止无限膨胀）
        if len(processed_ids) > 10000:
            processed_ids.clear()
            _p(f"[{_ts()}] 🧹 清理 processed_ids 缓存")


# ── 主循环 ────────────────────────────────────────────────────────


def main_loop(args: argparse.Namespace):
    board_dir = args.board_dir
    tools_dir = args.tools_dir
    worker_workspace = args.worker_workspace
    app_id = args.feishu_app_id
    app_secret = args.feishu_app_secret
    leader_name = args.leader_name
    leader_open_id = args.leader_open_id

    download_dir = os.path.join(worker_workspace, FEISHU_DOWNLOAD_SUBDIR)

    # ── 启动校验 ───────────────────────────────────────────────
    _p(f"[{RUNNER_NAME}] 🚀 启动 Bulletin ↔ Feishu 守护进程")

    if not os.path.isdir(board_dir):
        _p(f"[{RUNNER_NAME}] ❌ board_dir 不存在: {board_dir}", file=sys.stderr)
        sys.exit(1)

    if not os.path.isdir(tools_dir):
        _p(f"[{RUNNER_NAME}] ❌ tools_dir 不存在: {tools_dir}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(download_dir, exist_ok=True)
    _p(f"[{RUNNER_NAME}] 📁 文件下载目录: {download_dir}")

    # 获取飞书 token（验证凭证有效性）
    token = _fa.get_token(app_id, app_secret)
    if not token:
        _p(f"[{RUNNER_NAME}] ❌ 飞书 token 获取失败（app_id/app_secret 无效）", file=sys.stderr)
        sys.exit(1)
    _p(f"[{RUNNER_NAME}] ✅ 飞书 token 获取成功")

    # 确认目标 open_id
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

    # 记录初始 index（写回路用）
    last_seen = _read_index(board_dir)
    if last_seen is None:
        _p(f"[{RUNNER_NAME}] ❌ index.json 格式异常，退出", file=sys.stderr)
        sys.exit(1)
    _p(f"[{RUNNER_NAME}] 📍 初始 last_index = {last_seen}（不同步历史）")

    # ── 读回路：FeishuReceiver + 自定义 WS handler ────────────
    _p(f"[{RUNNER_NAME}] 🔄 启动飞书消息接收器...")

    token_mgr = TokenManager(app_id, app_secret)

    # 创建自定义 WS handler（只处理 p2p 消息）
    ws_handler, text_store, text_lock = make_ws_handler(
        tools_dir, download_dir, token_mgr,
    )

    # 创建 FeishuReceiver，但替换其 WS handler
    recv = FeishuReceiver(
        app_id, app_secret,
        on_message=None,
        token_manager=token_mgr,
    )
    # 替换内部 WS handler（monkey-patch 实例方法）
    recv._on_ws_message = ws_handler

    recv.start()

    # 启动文本消费后台线程
    stop_event = threading.Event()
    consume_thread = threading.Thread(
        target=_receive_loop,
        args=(tools_dir, text_store, text_lock, stop_event),
        name="feishu-ui-consume",
        daemon=True,
    )
    consume_thread.start()

    _p(f"[{RUNNER_NAME}] ✅ 飞书消息接收器已启动")

    # 发一条启动通知
    greet = (
        f"🟢 {RUNNER_NAME} — 已启动\n"
        f"   留言板: {board_dir}\n"
        f"   通知到: {leader_name} ({open_id[:12]}...)\n"
        f"   文件下载: {download_dir}"
    )
    _fa.send_text_message(open_id, token, greet)
    _p(f"[{RUNNER_NAME}] ✅ 已发送启动通知到飞书")

    # ── 写回路：轮询 board → 飞书 ──────────────────────────────
    _p(f"[{RUNNER_NAME}] 🔄 开始轮询留言板（{POLL_INTERVAL}s）...")
    try:
        while True:
            time.sleep(POLL_INTERVAL)

            now_last = _read_index(board_dir)
            if now_last is None:
                continue

            diff = now_last - last_seen

            if diff == 0:
                continue

            # diff 逆转（清空）
            if diff < 0:
                alert = (
                    f"🔄 检测到留言板 index 逆转（{last_seen} → {now_last}），"
                    f"可能有清空操作\n将重新同步最近的消息..."
                )
                _fa.send_text_message(open_id, token, alert)
                start = max(0, now_last - 10)
                for i in range(start + 1, now_last + 1):
                    msg_text = _bb_get(tools_dir, i)
                    if msg_text is None:
                        continue
                    clean_text = "\n".join(line.strip()
                                           for line in msg_text.split("\n"))
                    _fa.send_text_message(open_id, token, clean_text)
                last_seen = now_last
                continue

            # diff 正向跳变（跳过）
            if diff > MAX_LEGACY_DIFF:
                last_seen = now_last
                continue

            # diff 合法：逐条同步
            for i in range(last_seen + 1, now_last + 1):
                msg_text = _bb_get(tools_dir, i)
                if msg_text is None:
                    continue
                clean_text = "\n".join(line.strip()
                                       for line in msg_text.split("\n"))
                result = _fa.send_text_message(open_id, token, clean_text)
                if result.get("code") != 0:
                    new_token = _fa.get_token(app_id, app_secret)
                    if new_token:
                        token = new_token
                        _fa.send_text_message(open_id, token, clean_text)
            last_seen = now_last

    except KeyboardInterrupt:
        _p(f"\n[{RUNNER_NAME}] 👋 收到 Ctrl+C，退出")
    finally:
        _p(f"[{RUNNER_NAME}] ⏹ 正在停止...")
        stop_event.set()
        recv.stop()
        _p(f"[{RUNNER_NAME}] ✅ 已停止")


# ── 入口 ──────────────────────────────────────────────────────────


def main():
    args = parse_args()
    main_loop(args)


if __name__ == "__main__":
    main()
