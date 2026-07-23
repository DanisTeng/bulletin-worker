#!/usr/bin/env python3
"""
feishu_ui — Bulletin Board ↔ 飞书 消息守护进程

功能（双向）:
  写回路（已有）:
    - 定时（500ms）检查 board index.json 的 last_index
    - 新留言 → 调 bb-get 取内容 → 飞书推送给 leader
  读回路（新增）:
    - 自己的 WS 长连接监听飞书 p2p 私聊
    - 文本消息 → 调 bb-leader-post 写入 board
    - 文件消息 → 下载到 $worker_workspace/feishu_download/
    - 文件 >100MB → 回复"太大了"

参数（通过 sh wrapper 填充）:
  --board-dir           :   留言板目录（含 index.json）
  --tools-dir           :   tools 目录（含 bb-get / bb-leader-post）
  --worker-workspace    :   worker_workspace 路径（拼接 feishu_download/）
  --feishu-app-id       :   飞书 App ID
  --feishu-app-secret   :   飞书 App Secret
  --leader-name         :   被通知者在留言板上的 speaker 名
  --leader-open-id      :   被通知者的飞书 open_id（非空时优先于联系人查询）

依赖:
  - feishu_api.py 通过 PYTHONPATH 或同目录引用
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# ── 路径配置 ─────────────────────────────────────────────────────

_THIS_DIR = Path(__file__).parent.resolve()
_FEISHU_API_DIR = _THIS_DIR.parent  # core/feishu/
for p in [str(_THIS_DIR), str(_FEISHU_API_DIR)]:
    if p not in sys.path and os.path.isdir(p):
        sys.path.insert(0, p)

import feishu_api as _fa

_p = print


# ── 常量 ─────────────────────────────────────────────────────────

POLL_INTERVAL = 0.5          # 索引轮询间隔（秒）
MAX_LEGACY_DIFF = 30         # 最大合法 index 差异
RUNNER_NAME = "feishu-ui"
FEISHU_DOWNLOAD_SUBDIR = "feishu_download"
_TOKEN_CACHE_TTL = 5400      # 90 分钟 token 缓存
_FILE_DOWNLOAD_TIMEOUT = 60  # 下载超时（秒）
_MSG_LOG_TRIM = 18           # message_id 截断
_P2P_SENDER_TRIM = 12        # sender_id 截断


# ── 简易 Token 管理器 ────────────────────────────────────────────


class _TokenMgr:
    """线程安全的 token 缓存。"""
    def __init__(self, app_id, app_secret):
        self._app_id = app_id
        self._app_secret = app_secret
        self._token = None
        self._expires = 0.0

    def get(self):
        now = time.time()
        if now < self._expires and self._token:
            return self._token
        self._token = _fa.get_token(self._app_id, self._app_secret)
        if self._token:
            self._expires = now + _TOKEN_CACHE_TTL
        return self._token


# ── 命令行 ───────────────────────────────────────────────────────


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="feishu_ui — Bulletin Board ↔ 飞书 消息守护进程",
    )
    p.add_argument("--board-dir", required=True)
    p.add_argument("--tools-dir", required=True)
    p.add_argument("--worker-workspace", required=True)
    p.add_argument("--feishu-app-id", required=True)
    p.add_argument("--feishu-app-secret", required=True)
    p.add_argument("--leader-name", required=True)
    p.add_argument("--leader-open-id", default="")
    return p.parse_args(argv)


# ── index 读写 ────────────────────────────────────────────────────


def _read_index(board_dir):
    path = os.path.join(board_dir, "index.json")
    try:
        with open(path) as f:
            return json.load(f).get("last_index")
    except FileNotFoundError:
        return 0
    except (PermissionError, json.JSONDecodeError, KeyError):
        _p(f"[{RUNNER_NAME}] ❌ index.json 异常", file=sys.stderr)
        return None


# ── bb-get / bb-leader-post ────────────────────────────────────────


def _bb_get(tools_dir, index):
    get_path = os.path.join(tools_dir, "bb-get")
    if not os.path.isfile(get_path):
        _p(f"[{RUNNER_NAME}] ❌ 未找到 bb-get", file=sys.stderr)
        return None
    try:
        r = subprocess.run([get_path, str(index)],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, OSError) as e:
        _p(f"[{RUNNER_NAME}] ⚠ bb-get({index}): {e}", file=sys.stderr)
        return None


def _bb_leader_post(tools_dir, text):
    post_path = os.path.join(tools_dir, "bb-leader-post")
    if not os.path.isfile(post_path):
        _p(f"[{RUNNER_NAME}] ❌ 未找到 bb-leader-post", file=sys.stderr)
        return False
    try:
        r = subprocess.run([post_path, text],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as e:
        _p(f"[{RUNNER_NAME}] ⚠ bb-leader-post: {e}", file=sys.stderr)
        return False


# ── 飞书解析 ─────────────────────────────────────────────────────


def _resolve_open_id(app_id, app_secret, leader_name):
    token = _fa.get_token(app_id, app_secret)
    if not token:
        return None
    page_token = None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    while True:
        params = "page_size=50"
        if page_token:
            params += f"&page_token={page_token}"
        url = f"https://open.feishu.cn/open-apis/contact/v3/users?{params}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except Exception:
            return None
        if data.get("code") != 0:
            return None
        for user in data.get("data", {}).get("items", []):
            if user.get("name") == leader_name:
                return user.get("open_id")
        if not data.get("data", {}).get("has_more"):
            break
        page_token = data.get("data", {}).get("page_token")
    return None


# ── WS 接收器 ──────────────────────────────────────────────────────


class _FeishuWS:
    """自己的 WS 接收器，只处理 p2p 私聊。

    逻辑直接写在回调里：
      - 文本 → bb-leader-post
      - 文件 → 下载到 download_dir
      - 文件 >100MB → 回复"太大了"
    """

    def __init__(self, app_id, app_secret, download_dir, tools_dir):
        self._app_id = app_id
        self._app_secret = app_secret
        self._download_dir = download_dir
        self._tools_dir = tools_dir
        self._token_mgr = _TokenMgr(app_id, app_secret)
        self._stop = False

    def start(self):
        os.makedirs(self._download_dir, exist_ok=True)
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

        handler = (lark.EventDispatcherHandler.builder("", "")
                   .register_p2_im_message_receive_v1(self._on_msg)
                   .build())
        ws = lark.ws.Client(self._app_id, self._app_secret,
                            event_handler=handler,
                            log_level=lark.LogLevel.WARNING)

        _p(f"[{RUNNER_NAME}] ✅ WS 连接已建立")
        while not self._stop:
            try:
                ws.start()
            except Exception as e:
                if self._stop:
                    break
                _p(f"[{RUNNER_NAME}] ⚠ WS 断开 ({e}), 5s 重连...")
                time.sleep(5)

    def stop(self):
        self._stop = True

    def _log(self, msg):
        _p(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

    def _on_msg(self, data):
        event = data.event
        if not event or not event.message:
            return
        msg_obj = event.message
        message_id = getattr(msg_obj, 'message_id', '') or ''
        if not message_id:
            return

        # 只处理 p2p 私聊
        chat_type = getattr(msg_obj, 'chat_type', '') or ''
        if chat_type != "p2p":
            return

        msg_type = getattr(msg_obj, 'message_type', '') or ''

        # sender_id
        sender_id = ''
        sender = getattr(event, 'sender', None)
        if sender:
            sid = getattr(sender, 'sender_id', None)
            if sid is not None:
                sender_id = getattr(sid, 'open_id', '') or ''
        if not sender_id:
            self._log(f"⚠ 丢弃 {message_id[:_MSG_LOG_TRIM]}: 无 sender_id")
            return

        if msg_type == "text":
            self._on_text(message_id, sender_id, msg_obj)
        else:
            self._on_file(message_id, sender_id, msg_obj)

    # ── 文本处理 ──

    def _on_text(self, message_id, sender_id, msg_obj):
        raw = getattr(msg_obj, 'content', '')
        text = ""
        if raw:
            try:
                p = json.loads(raw)
                text = p.get("text", str(p)) if isinstance(p, dict) else str(p)
            except (json.JSONDecodeError, TypeError):
                text = str(raw)
        if not text:
            return

        preview = text[:40].replace("\n", " ")
        self._log(f"📩 p2p ({sender_id[:_P2P_SENDER_TRIM]}...): {preview}")

        if _bb_leader_post(self._tools_dir, text):
            self._log(f"✅ 已写入留言板")
        else:
            self._log(f"❌ 写入留言板失败")

    # ── 文件处理 ──

    def _on_file(self, message_id, sender_id, msg_obj):
        raw = getattr(msg_obj, 'content', '')
        if not raw:
            return
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(parsed, dict):
            return

        file_key = parsed.get("file_key", "") or parsed.get("image_key", "") or ""
        file_name = parsed.get("file_name", "") or "unknown"
        resource_type = "file" if parsed.get("file_key") else "image"
        if not file_key:
            return

        output = os.path.join(self._download_dir, f"{message_id}_{file_name}")

        # 去重
        if os.path.exists(output):
            self._log(f"📎 文件已存在 {file_name}")
            return

        token = self._token_mgr.get()
        if not token:
            return

        self._log(f"📥 下载 {file_name} ({resource_type})")

        r = _fa.download_resource(message_id, file_key, token,
                                   resource_type=resource_type,
                                   output_path=output,
                                   timeout=_FILE_DOWNLOAD_TIMEOUT)
        if r.get("code") == 0:
            self._log(f"✅ {file_name} → {output}")
        else:
            err = r.get("msg", "")
            self._log(f"⚠ {file_name}: {err}")
            if "100MB" in err:
                token2 = self._token_mgr.get()
                if token2:
                    _fa.send_text_message(sender_id, token2,
                                          "文件太大了，请发送小于 100MB 的文件 🙏")


# ── 主循环 ────────────────────────────────────────────────────────


def main_loop(args):
    board_dir = args.board_dir
    tools_dir = args.tools_dir
    download_dir = os.path.join(args.worker_workspace, FEISHU_DOWNLOAD_SUBDIR)
    app_id = args.feishu_app_id
    app_secret = args.feishu_app_secret
    leader_name = args.leader_name
    leader_open_id = args.leader_open_id

    # 启动校验
    _p(f"[{RUNNER_NAME}] 🚀 启动")
    for d in [board_dir, tools_dir]:
        if not os.path.isdir(d):
            _p(f"[{RUNNER_NAME}] ❌ {d} 不存在", file=sys.stderr)
            sys.exit(1)
    os.makedirs(download_dir, exist_ok=True)
    _p(f"[{RUNNER_NAME}] 📁 文件: {download_dir}")

    # 验证 token
    token = _fa.get_token(app_id, app_secret)
    if not token:
        _p(f"[{RUNNER_NAME}] ❌ token 获取失败", file=sys.stderr)
        sys.exit(1)
    _p(f"[{RUNNER_NAME}] ✅ token OK")

    # open_id
    if leader_open_id:
        open_id = leader_open_id
    else:
        _p(f"[{RUNNER_NAME}] 🔍 查询 {leader_name}...")
        open_id = _resolve_open_id(app_id, app_secret, leader_name)
        if not open_id:
            _p(f"[{RUNNER_NAME}] ❌ 未找到 {leader_name}", file=sys.stderr)
            sys.exit(1)
    _p(f"[{RUNNER_NAME}] ✅ open_id: {open_id[:12]}...")

    # 初始 index
    last_seen = _read_index(board_dir)
    if last_seen is None:
        sys.exit(1)
    _p(f"[{RUNNER_NAME}] 📍 last_index = {last_seen}")

    # ── 启动 WS ──────────────────────────────────────────────
    import threading
    ws = _FeishuWS(app_id, app_secret, download_dir, tools_dir)
    ws_thread = threading.Thread(target=ws.start, daemon=True)
    ws_thread.start()
    time.sleep(1)  # 等 WS 连接建立
    _p(f"[{RUNNER_NAME}] ✅ WS 接收器已启动")

    # ── 启动通知 ────────────────────────────────────────────
    _fa.send_text_message(open_id, token,
                          f"🟢 {RUNNER_NAME} 已启动\n 文件: {download_dir}")
    _p(f"[{RUNNER_NAME}] ✅ 已通知飞书")

    # ── 写回路轮询 ──────────────────────────────────────────
    _p(f"[{RUNNER_NAME}] 🔄 轮询留言板 ({POLL_INTERVAL}s)")
    try:
        while True:
            time.sleep(POLL_INTERVAL)
            now = _read_index(board_dir)
            if now is None:
                continue
            diff = now - last_seen
            if diff == 0:
                continue

            if diff < 0:
                _fa.send_text_message(open_id, token,
                    f"🔄 index 逆转 ({last_seen}→{now}), 重发最近 10 条")
                for i in range(max(0, now - 10) + 1, now + 1):
                    t = _bb_get(tools_dir, i)
                    if t:
                        _fa.send_text_message(open_id, token,
                            "\n".join(l.strip() for l in t.split("\n")))
                last_seen = now
                continue

            if diff > MAX_LEGACY_DIFF:
                last_seen = now
                continue

            for i in range(last_seen + 1, now + 1):
                t = _bb_get(tools_dir, i)
                if not t:
                    continue
                clean = "\n".join(l.strip() for l in t.split("\n"))
                r = _fa.send_text_message(open_id, token, clean)
                if r.get("code") != 0:
                    new_tok = _fa.get_token(app_id, app_secret)
                    if new_tok:
                        token = new_tok
                        _fa.send_text_message(open_id, token, clean)
            last_seen = now

    except KeyboardInterrupt:
        _p(f"\n[{RUNNER_NAME}] 👋 退出")
    finally:
        ws.stop()


def main():
    main_loop(parse_args())


if __name__ == "__main__":
    main()
