#!/usr/bin/env python3
"""
feishu_ui — Bulletin Board ↔ 飞书 消息守护进程

架构（双向）:
  写回路（已有）:
    - 定时（500ms）检查 board index.json 的 last_index
    - 计算 diff = now_last - last_seen；diff 在 [0, 30] 内视为合法
    - 对 diff > 0 的每条新留言，调 bb-get <index> 取内容
    - 通过飞书 API 发送给目标用户
  读回路（新增）:
    - WS 长连接监听飞书 p2p 私聊
    - 文本消息 → 调 bb-leader-post 写入 board（等同于 leader 发言）
    - 文件消息（≤100MB）→ 下载到 $worker_workspace/feishu_download/，平铺
    - 文件消息（>100MB）→ 回复"文件太大，请发送小于 100MB 的文件"

参数（通过 sh wrapper 填充）:
  --board-dir           :   留言板目录（含 index.json）
  --tools-dir           :   tools 目录（含 bb-get, bb-leader-post）
  --worker-workspace    :   worker_workspace 路径（拼接 feishu_download/）
  --feishu-app-id       :   飞书 App ID
  --feishu-app-secret   :   飞书 App Secret
  --leader-name         :   被通知者在留言板上的 speaker 名
  --leader-open-id      :   被通知者的飞书 open_id（非空时优先于联系人查询）

行为：
  - 首次启动：记下当前 last_index，不发送任何消息
  - 定时检查：diff ∈ [0, 30] → 遍历 (last_seen, now_last]，逐条 bb-get 发到飞书
  - diff 非法（负值或 >30）→ 输出告警日志，last_seen 原地不动
  - 启动时检查 token 有效性，失败直接退出
  - (新增) WS 接收只处理 chat_type == "p2p" 私聊

约定：
  - 不发任何已有的旧留言（首次启动仅记录 index，不同步历史）
  - 每轮同步完成更新 last_seen = now_last
  - 文本消息只写入留言板，不回复飞书
  - 文件 >100MB 回复一条消息

依赖：
  - feishu_api.py 通过 PYTHONPATH 或同目录引用
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
RUNNER_NAME = "feishu-ui"
FEISHU_DOWNLOAD_SUBDIR = "feishu_download"  # 文件下载子目录名
_FILE_DOWNLOAD_TIMEOUT = 60  # 下载文件超时（秒）
_MSG_LOG_TRIM = 18         # 日志中 message_id 截断长度
_P2P_SENDER_TRIM = 12      # 日志中 sender_id 截断长度
_TOKEN_CACHE_TTL = 5400    # 90 分钟 token 缓存（留 30 分钟余量）


# ── 简易 Token 管理器（供 WS 线程用） ────────────────────────────


class _TokenMgr:
    """供 WS 线程用的 token 缓存。"""
    def __init__(self, app_id: str, app_secret: str):
        self._app_id = app_id
        self._app_secret = app_secret
        self._token: str | None = None
        self._expires_at: float = 0.0

    def get(self) -> str | None:
        now = time.time()
        if now < self._expires_at and self._token is not None:
            return self._token
        self._token = get_token(self._app_id, self._app_secret)
        if self._token:
            self._expires_at = now + _TOKEN_CACHE_TTL
        return self._token


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
    """读取 index.json 中的 last_index。

    文件不存在时返回 0（空留言板），格式错误返回 None。
    """
    path = os.path.join(board_dir, "index.json")
    try:
        with open(path) as f:
            data = json.load(f)
            return data.get("last_index")
    except FileNotFoundError:
        return 0  # 空留言板，index 视为 0
    except PermissionError:
        _p(f"[{RUNNER_NAME}] ❌ 无权限读取 {path}", file=sys.stderr)
        return None
    except (json.JSONDecodeError, KeyError):
        return None


# ── bb-get ─────────────────────────────────────────────────────────


def _bb_get(tools_dir: str, index: int) -> str | None:
    """调 bb-get <index>，返回留言内容（含时间戳和发言人）。失败返回 None。"""
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


# ── bb-leader-post（新增） ─────────────────────────────────────────


def _bb_leader_post(tools_dir: str, text: str) -> bool:
    """调 bb-leader-post <text>，写入留言板（等同于 leader 发言）。失败返回 False。"""
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


# ── WS 接收器（新增） ────────────────────────────────────────────


class _FeishuWS:
    """自包含的 WS 消息接收器，只处理 p2p 私聊。

    回调逻辑：
      - 文本 → bb-leader-post 写入留言板
      - 文件 → 下载到 $download_dir/{msg_id}_{filename}，太大则回复
    """

    def __init__(self, app_id: str, app_secret: str,
                 download_dir: str, tools_dir: str):
        self._app_id = app_id
        self._app_secret = app_secret
        self._download_dir = download_dir
        self._tools_dir = tools_dir
        self._token_mgr = _TokenMgr(app_id, app_secret)
        self._stop = False

    def start(self):
        """启动 WS 连接。"""
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

        os.makedirs(self._download_dir, exist_ok=True)
        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_msg)
            .build()
        )
        client = lark.ws.Client(
            self._app_id, self._app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.WARNING,
        )
        _p(f"[{RUNNER_NAME}] ✅ WS 连接已建立")
        while not self._stop:
            try:
                client.start()
            except Exception as e:
                if self._stop:
                    break
                _p(f"[{RUNNER_NAME}] ⚠ WS 断开 ({e}), 5s 重连...")
                time.sleep(5)

    def stop(self):
        self._stop = True

    def _ts(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def _on_msg(self, data):
        """WS 消息回调：过滤 p2p，分派处理。"""
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
        event_sender = getattr(event, 'sender', None)
        if event_sender:
            sid_obj = getattr(event_sender, 'sender_id', None)
            if sid_obj is not None:
                sender_id = getattr(sid_obj, 'open_id', '') or ''
        if not sender_id:
            _p(f"[{self._ts()}] ⚠ 丢弃 {message_id[:_MSG_LOG_TRIM]}: 无 sender_id")
            return

        if msg_type == "text":
            self._on_text(message_id, sender_id, msg_obj)
        else:
            self._on_file(message_id, sender_id, msg_obj)

    def _on_text(self, message_id: str, sender_id: str, msg_obj):
        """处理 p2p 文本消息 → bb-leader-post。"""
        raw = getattr(msg_obj, 'content', '')
        text = ""
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    text = parsed.get("text", str(parsed))
                else:
                    text = str(parsed)
            except (json.JSONDecodeError, TypeError):
                text = str(raw)
        if not text:
            return

        preview = text[:40].replace("\n", " ")
        _p(f"[{self._ts()}] 📩 p2p ({sender_id[:_P2P_SENDER_TRIM]}...): {preview}")

        if _bb_leader_post(self._tools_dir, text):
            _p(f"[{self._ts()}] ✅ 已写入留言板")
        else:
            _p(f"[{self._ts()}] ❌ 写入留言板失败", file=sys.stderr)

    def _on_file(self, message_id: str, sender_id: str, msg_obj):
        """处理 p2p 文件消息 → 下载或回复超限。"""
        raw = getattr(msg_obj, 'content', '')
        if not raw:
            _p(f"[{self._ts()}] ⚠ 文件无 content, id={message_id[:_MSG_LOG_TRIM]}")
            return

        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            _p(f"[{self._ts()}] ⚠ 文件 content 解析失败, id={message_id[:_MSG_LOG_TRIM]}")
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
            _p(f"[{self._ts()}] 📎 文件已存在 (跳过) {file_name}")
            return

        token = self._token_mgr.get()
        if not token:
            _p(f"[{self._ts()}] ⚠ 跳过 {file_name}: 无 token")
            return

        _p(f"[{self._ts()}] 📥 下载 {file_name} ({resource_type})")

        import feishu_api as _fa
        r = _fa.download_resource(
            message_id, file_key, token,
            resource_type=resource_type,
            output_path=output,
            timeout=_FILE_DOWNLOAD_TIMEOUT,
        )

        if r.get("code") == 0:
            size_kb = r.get("size", 0) / 1024
            _p(f"[{self._ts()}] ✅ {file_name} ({size_kb:.1f}KB) → {output}")
        else:
            err = r.get("msg", "")
            _p(f"[{self._ts()}] ⚠ {file_name}: {err}")
            if "100MB" in err:
                token2 = self._token_mgr.get()
                if token2:
                    send_text_message(sender_id, token2,
                                      "文件太大了，请发送小于 100MB 的文件 🙏")


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
        _p(f"[{RUNNER_NAME}] ❌ index.json 格式异常，退出", file=sys.stderr)
        sys.exit(1)
    _p(f"[{RUNNER_NAME}] 📍 初始 last_index = {last_seen}（不同步历史）")

    # 5. 创建下载目录
    os.makedirs(download_dir, exist_ok=True)
    _p(f"[{RUNNER_NAME}] 📁 文件下载目录: {download_dir}")

    # ── 启动 WS 接收器（新增） ────────────────────────────────
    _p(f"[{RUNNER_NAME}] 🔄 启动飞书消息接收器...")
    ws = _FeishuWS(app_id, app_secret, download_dir, tools_dir)
    ws_thread = threading.Thread(target=ws.start, daemon=True)
    ws_thread.start()
    time.sleep(1)  # 等 WS 连接建立
    _p(f"[{RUNNER_NAME}] ✅ 飞书消息接收器已启动")

    # 6. 发一条启动通知
    greet = (
        f"🟢 {RUNNER_NAME} — 已启动\n"
        f"   留言板: {board_dir}\n"
        f"   通知到: {leader_name} ({open_id[:12]}...)\n"
        f"   文件下载: {download_dir}"
    )
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

        # diff 逆转（清空）
        if diff < 0:
            alert = (f"🔄 检测到留言板 index 逆转（{last_seen} → {now_last}），可能有清空操作\n"
                     f"将重新同步最近的消息...")
            send_text_message(open_id, token, alert)
            # 从 max(0, now_last - 10) 到 now_last 重发
            start = max(0, now_last - 10)
            for i in range(start + 1, now_last + 1):
                msg_text = _bb_get(tools_dir, i)
                if msg_text is None:
                    continue
                clean_text = "\n".join(line.strip() for line in msg_text.split("\n"))
                send_text_message(open_id, token, clean_text)
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
            clean_text = "\n".join(line.strip() for line in msg_text.split("\n"))
            result = send_text_message(open_id, token, clean_text)
            if result.get("code") != 0:
                # token 可能过期（飞书默认 2h），刷新重试一次
                new_token = get_token(app_id, app_secret)
                if new_token:
                    token = new_token
                    send_text_message(open_id, token, clean_text)
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
