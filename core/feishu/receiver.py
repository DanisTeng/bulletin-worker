"""receiver — 飞书消息/文件接收器的常驻进程。

功能:
  - 飞书 WebSocket 长连接接收消息
  - 线程安全的消息存储（供消费者通过 snapshot() 轮询）
  - 文件自动下载到本地（不存 MessageTable，走日志记录路径）
  - 内置 token 生命周期管理 + NameResolver（名字缓存）

架构:
  - 后台 WS 线程接收消息，写入 MessageStore
  - 主线程或其他消费者通过 snapshot() 读取
  - stop() 触发优雅退出

env:
    BULLETIN_FEISHU_APP_ID      — 飞书 App ID
    BULLETIN_FEISHU_APP_SECRET  — 飞书 App Secret

用法:

    from core.feishu.receiver import FeishuReceiver

    recv = FeishuReceiver(app_id, app_secret)
    recv.start()

    # 轮询接收到的消息
    messages = recv.snapshot()
    for sender_id, msg_list in messages.items():
        for msg in msg_list:
            print(msg["text"])

    recv.stop()
"""

import copy
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# 路径 fallback：当作为独立模块被 import（无 parent package）时
# 通过绝对 import 兜底
_THIS_RECV_DIR = Path(__file__).parent.resolve()
if str(_THIS_RECV_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_RECV_DIR))

try:
    from .feishu_api import (
        get_token,
        send_text_message,
        download_resource,
    )
except ImportError:
    from feishu_api import (
        get_token,
        send_text_message,
        download_resource,
    )

logger = logging.getLogger("feishu-receiver")

# ── 常量 ────────────────────────────────────────────────────────────────

_LOG_ID_TRIM = 18           # 日志中 message_id 截断长度
_LOG_SENDER_ID_TRIM = 12    # 日志中 sender_id 截断长度
_FILE_DOWNLOAD_TIMEOUT = 60  # 下载文件超时（秒）


# ── NameResolver ────────────────────────────────────────────────────────


class NameResolver:
    """Lazy-cached open_id → name 映射。

    通过飞书 contact API 查询用户名，结果永久缓存。
    线程安全，支持并发访问。
    """

    def __init__(self, app_id: str, app_secret: str):
        self._app_id = app_id
        self._app_secret = app_secret
        self._cache: dict[str, str] = {}
        self._lock = threading.Lock()

    def resolve(self, open_id: str) -> Optional[str]:
        """获取 open_id 对应的用户名。

        优先返回缓存结果。缓存未命中时调用 contact API 查询。

        Args:
            open_id: 飞书用户的 open_id

        Returns:
            用户名，或 None（解析失败）
        """
        with self._lock:
            cached = self._cache.get(open_id)
            if cached is not None:
                return cached if cached else None

        token = get_token(self._app_id, self._app_secret)
        if not token:
            return None

        from .feishu_api import _request
        result = _request(f"/contact/v3/users/{open_id}", token)
        user = result.get("data", {}).get("user", {})
        name = user.get("name", "") or ""

        with self._lock:
            self._cache[open_id] = name

        return name if name else None


# ── MessageStore ────────────────────────────────────────────────────────


class MessageStore:
    """线程安全的消息存储。

    消息按 sender_id 分组，每组的列表最新在前。
    消费者通过 snapshot() 获取深拷贝的快照。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._by_sender: dict[str, list[dict]] = {}

    def add(self, message_id: str, sender_id: str, sender_name: str,
            text: str, create_time: str, chat_id: str = "") -> dict:
        """添加一条消息。

        Args:
            message_id: 飞书消息 ID
            sender_id: 发送者 open_id
            sender_name: 发送者名字
            text: 消息文本
            create_time: 飞书创建时间戳
            chat_id: 会话 ID（可选）

        Returns:
            dict: 已存储的消息 dict
        """
        msg = {
            "message_id": message_id,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "text": text,
            "create_time": create_time,
            "chat_id": chat_id,
            "recv_time": time.time(),
        }
        with self._lock:
            if sender_id not in self._by_sender:
                self._by_sender[sender_id] = []
            self._by_sender[sender_id].insert(0, msg)
        return msg

    def snapshot(self) -> dict[str, list[dict]]:
        """返回 {sender_id: [dict, ...]} 的深拷贝，每组最新在前。"""
        with self._lock:
            return copy.deepcopy(self._by_sender)

    def count(self) -> int:
        """返回总消息数。"""
        with self._lock:
            return sum(len(v) for v in self._by_sender.values())

    def last_message(self, sender_id: str) -> Optional[dict]:
        """获取某个发送者的最新消息。

        Args:
            sender_id: 发送者 open_id

        Returns:
            最新消息 dict，或 None（该发送者无消息）
        """
        with self._lock:
            msgs = self._by_sender.get(sender_id)
            if msgs:
                return msgs[0]
            return None


# ── Token 管理器 ────────────────────────────────────────────────────────


class TokenManager:
    """自动刷新 tenant_access_token 的管理器。

    飞书 token 有效期 2 小时，缓存 1.5 小时后自动刷新。
    线程安全。
    """

    _CACHE_TTL = 5400  # 90 分钟，留 30 分钟余量

    def __init__(self, app_id: str, app_secret: str):
        self._app_id = app_id
        self._app_secret = app_secret
        self._token: Optional[str] = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    def get(self) -> Optional[str]:
        """获取有效 token，过期自动刷新。

        Returns:
            tenant_access_token，或 None（获取失败）
        """
        with self._lock:
            if time.time() < self._expires_at and self._token is not None:
                return self._token

            self._token = get_token(self._app_id, self._app_secret)
            if self._token:
                self._expires_at = time.time() + self._CACHE_TTL
            return self._token


# 下划线别名兼容旧引用
_TokenManager = TokenManager


# ── FeishuReceiver ──────────────────────────────────────────────────────


class FeishuReceiver:
    """飞书消息接收器常驻进程。

    - 后台 WS 线程：接收文本消息存入 MessageStore，文件消息自动下载
    - snapshot() 供消费者轮询（深拷贝，线程安全）
    - 内置 NameResolver 自动解析用户名
    - 内置 TokenManager 自动刷新 token
    - 可选的 on_message 回调

    Attributes:
        store: MessageStore 实例（可直接读写）
        name_resolver: NameResolver 实例
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        on_message: Optional[callable] = None,
        file_storage_dir: str = "",
        log_file: str = "",
        token_manager: Optional[TokenManager] = None,
    ):
        self._app_id = app_id
        self._app_secret = app_secret
        self._on_message = on_message
        self._file_storage_dir = file_storage_dir
        self._log_file = log_file

        self.store = MessageStore()
        self.name_resolver = NameResolver(app_id, app_secret)
        if token_manager is not None:
            self._token_mgr = token_manager
        else:
            self._token_mgr = TokenManager(app_id, app_secret)

        self._ws_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ── lifecycle ──

    def start(self):
        """启动 WS 后台线程。"""
        if self._ws_thread and self._ws_thread.is_alive():
            logger.warning("FeishuReceiver already running")
            return
        self._stop_event.clear()
        self._ws_thread = threading.Thread(
            target=self._ws_loop,
            name="feishu-receiver-ws",
            daemon=True,
        )
        self._ws_thread.start()
        logger.info("FeishuReceiver started")

    def stop(self):
        """停止 WS 后台线程。"""
        self._stop_event.set()
        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=10)
        logger.info("FeishuReceiver stopped")

    # ── snapshot ──

    def snapshot(self) -> dict[str, list[dict]]:
        """线程安全的消息快照。

        Returns:
            {sender_id: [msg_dict, ...]}，每组最新在前。
        """
        return self.store.snapshot()

    # ── 发送辅助 ──

    def send_text(self, open_id: str, text: str) -> dict:
        """发送文本消息给指定用户（自动获取 token）。

        Args:
            open_id: 接收者的 open_id
            text: 消息文本

        Returns:
            dict: 飞书 API 响应
        """
        token = self._token_mgr.get()
        if not token:
            return {"code": -1, "msg": "token 获取失败"}
        return send_text_message(open_id, token, text)

    # ── 日志 ──

    def _log_line(self, line: str):
        """写日志到 stdout 和/或文件。"""
        print(line, flush=True)
        if self._log_file:
            od = os.path.dirname(self._log_file)
            if od:
                os.makedirs(od, exist_ok=True)
            with open(self._log_file, "a") as f:
                f.write(line + "\n")

    def _ts(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── 文件下载 ──

    def _get_file_storage_dir(self) -> str:
        d = self._file_storage_dir
        if not d:
            d = os.environ.get("BULLETIN_FILE_STORAGE_DIR", "./received_files")
        os.makedirs(d, exist_ok=True)
        return d

    def _build_file_output_path(self, storage_dir: str, sender_id: str,
                                file_name: str, message_id: str) -> str:
        date_str = datetime.now().strftime("%Y-%m-%d")
        safe_name = file_name if file_name else "unknown_file"
        return os.path.join(storage_dir, sender_id, date_str,
                            f"{message_id}_{safe_name}")

    def _download_and_log_file(self, msg_obj, message_id: str, msg_type: str,
                                sender_id: str, sender_name: str):
        """下载文件消息中的资源，不写入 MessageStore。

        去重：已存在的文件跳过下载。
        """
        raw = getattr(msg_obj, 'content', '')
        if not raw:
            self._log_line(
                f"[{self._ts()}] ⚠️  {sender_name}: "
                f"文件消息无 content (type={msg_type}), id={message_id[:_LOG_ID_TRIM]}")
            return

        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            self._log_line(
                f"[{self._ts()}] ⚠️  {sender_name}: "
                f"文件消息 content 解析失败 (type={msg_type}), id={message_id[:_LOG_ID_TRIM]}")
            return

        if not isinstance(parsed, dict):
            self._log_line(
                f"[{self._ts()}] ⚠️  {sender_name}: "
                f"文件消息 content 非 dict (type={msg_type}), id={message_id[:_LOG_ID_TRIM]}")
            return

        file_key = parsed.get("file_key", "") or ""
        image_key = parsed.get("image_key", "") or ""
        file_name = parsed.get("file_name", "") or "unknown"

        if not file_key and not image_key:
            self._log_line(
                f"[{self._ts()}] ⚠️  {sender_name}: "
                f"文件消息无 file_key/image_key (type={msg_type}), id={message_id[:_LOG_ID_TRIM]}")
            return

        key = file_key if file_key else image_key
        resource_type = "file" if file_key else "image"

        storage_dir = self._get_file_storage_dir()
        output_path = self._build_file_output_path(
            storage_dir, sender_id, file_name, message_id)

        # 去重
        if os.path.exists(output_path):
            size_kb = os.path.getsize(output_path) / 1024
            self._log_line(
                f"[{self._ts()}] 📎  {sender_name}: "
                f"文件已存在 (跳过) {file_name} ({size_kb:.1f}KB) -> {output_path}")
            return

        token = self._token_mgr.get()
        if not token:
            self._log_line(
                f"[{self._ts()}] ⚠️  {sender_name}: "
                f"下载文件失败 (token), id={message_id[:_LOG_ID_TRIM]}")
            return

        result = download_resource(
            message_id, key, token,
            resource_type=resource_type,
            output_path=output_path,
            timeout=_FILE_DOWNLOAD_TIMEOUT,
        )

        if result.get("code") == 0:
            size_kb = result.get("size", 0) / 1024
            path = result.get("path", output_path)
            self._log_line(
                f"[{self._ts()}] 📎  {sender_name}: "
                f"收到文件 {file_name} ({size_kb:.1f}KB) -> {path}")
        else:
            self._log_line(
                f"[{self._ts()}] ⚠️  {sender_name}: "
                f"文件 {file_name} 下载失败: {result.get('msg', '未知错误')}")

    # ── WS 循环 ──

    @staticmethod
    def _extract_text(msg_obj) -> str:
        """从文本消息对象中提取纯文本内容。"""
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

    def _ws_loop(self):
        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
        except ImportError:
            logger.error("lark-oapi 未安装, 运行: pip install lark-oapi")
            return

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_ws_message)
            .build()
        )

        ws_client = lark.ws.Client(
            self._app_id, self._app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.WARNING,
        )

        while not self._stop_event.is_set():
            try:
                ws_client.start()
            except Exception as e:
                if self._stop_event.is_set():
                    break
                logger.warning(f"WS 连接断开 ({e}), 5 秒后重连...")
                time.sleep(5)

    def _on_ws_message(self, data):
        """WS 消息回调。"""
        event = data.event
        if not event or not event.message:
            return

        msg_obj = event.message
        message_id = getattr(msg_obj, 'message_id', '')
        if not message_id:
            return

        msg_type = getattr(msg_obj, 'message_type', '') or ''
        create_time = getattr(msg_obj, 'create_time', '0')

        # sender 信息
        event_sender = getattr(event, 'sender', None)
        sender_id = ''
        if event_sender:
            sender_id_obj = getattr(event_sender, 'sender_id', None)
            if sender_id_obj is not None:
                sender_id = getattr(sender_id_obj, 'open_id', '') or ''

        if not sender_id:
            logger.error(
                f"丢弃消息 {message_id[:_LOG_ID_TRIM]}: 缺少 sender_id")
            return

        sender_name = self.name_resolver.resolve(sender_id)
        if not sender_name:
            logger.error(
                f"丢弃消息 {message_id[:_LOG_ID_TRIM]}: "
                f"无法解析用户名, sender_id={sender_id}")
            return

        if msg_type == "text":
            # 文本消息：存入 MessageStore
            text = self._extract_text(msg_obj)
            msg = self.store.add(
                message_id, sender_id, sender_name,
                text, create_time,
                chat_id="",
            )

            preview = msg["text"][:10].replace("\n", " ")
            self._log_line(
                f"[{self._ts()}] 📩 {msg['sender_name']}: "
                f"{preview}... [{len(msg['text'])}chars]")

            if self._on_message:
                try:
                    self._on_message(msg)
                except Exception as e:
                    logger.error(f"on_message 回调失败: {e}")
        else:
            # 非文本消息：下载文件
            self._download_and_log_file(
                msg_obj, message_id, msg_type, sender_id, sender_name)
