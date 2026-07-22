"""
feishu — 飞书通信工具模块，用于 Bulletin Worker 常驻进程。

分层架构:
  ┌─────────────┐
  │  receiver   │  WS 长连接接收 + 轮询 + 自动 token 管理
  ├─────────────┤
  │  send_file  │  文件上传 + 发送文件消息
  ├─────────────┤
  │  feishu_api │  原始飞书 API 调用（token 显式传参）
  └─────────────┘

用法（receiver 自动管理 token，其他工具传入 token）:

    from core.feishu.receiver import FeishuReceiver

    recv = FeishuReceiver(app_id, app_secret)
    recv.start()
    ...
    messages = recv.snapshot()  # 所有收到的消息
    recv.stop()

    # 发送
    from core.feishu import send_text, send_file
    send_text(token, "ou_xxx", "hello")
    send_file(token, "ou_xxx", "/path/to/file.pdf")
"""

from .feishu_api import (
    API_BASE,
    get_token,
    send_text_message,
    reply_message,
    send_file_message,
    download_resource,
    react_message,
    delete_reaction,
    get_reactions,
    list_chats,
    list_chat_messages,
    poll_new_messages,
)
from .send_file import (
    upload_file,
    send_file as send_file_to_user,
    supported_extensions,
)

__all__ = [
    "API_BASE",
    "get_token",
    "send_text_message",
    "reply_message",
    "send_file_message",
    "download_resource",
    "react_message",
    "delete_reaction",
    "get_reactions",
    "list_chats",
    "list_chat_messages",
    "poll_new_messages",
    "upload_file",
    "send_file_to_user",
    "supported_extensions",
]
