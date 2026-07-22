"""飞书开放平台 API 封装

提供通用飞书 API 调用能力，不含任何业务逻辑。

约定:
  - 所有函数显式接收 token 参数，调用者自行管理 token 生命周期。
  - 唯一例外是 get_token() 本身（它不需要 token）。
  - 返回 dict 统一格式: {"code": 0, ...} 或 {"code": -1, "msg": "..."}.
"""

import json
import os
import urllib.error
import urllib.request

API_BASE = "https://open.feishu.cn/open-apis"


# ── 底层请求 ──────────────────────────────────────────────────────────


def _request(path, token, method="GET", body=None):
    """发送飞书 API 请求。

    Pre-condition: token 不能为空（获取 token 本身不走此函数）。

    Args:
        path: API 路径，如 /im/v1/messages
        token: tenant_access_token
        method: HTTP 方法，默认 GET
        body: 请求体 dict，自动序列化 JSON

    Returns:
        dict: 飞书 API 响应。出错时返回 {"code": -1, "msg": ...}
    """
    assert token, "_request requires a valid token"

    url = f"{API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    data = json.dumps(body).encode("utf-8") if body else None

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"code": -1, "msg": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"code": -1, "msg": str(e)}


# ── 认证 ──────────────────────────────────────────────────────────────


def get_token(app_id, app_secret):
    """获取 tenant_access_token。

    Args:
        app_id: 飞书自建应用的 App ID
        app_secret: 飞书自建应用的 App Secret

    Returns:
        str: tenant_access_token，失败返回 None
    """
    body = {"app_id": app_id, "app_secret": app_secret}
    req = urllib.request.Request(
        f"{API_BASE}/auth/v3/tenant_access_token/internal",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    if result.get("code") != 0:
        return None
    return result["tenant_access_token"]


# ── 消息收发 ──────────────────────────────────────────────────────────


def _build_post_content(text: str) -> str:
    """用 post + md tag 包装文本，支持 markdown 渲染。

    Args:
        text: 消息文本（支持基础 markdown 语法）

    Returns:
        str: JSON 字符串，可直接作为 content 参数
    """
    return json.dumps({
        "zh_cn": {
            "content": [[{"tag": "md", "text": text}]],
        }
    })


def send_text_message(open_id, token, text):
    """发送文本消息给指定用户（post 格式，支持 markdown 渲染）。

    Args:
        open_id: 接收者的 open_id
        token: tenant_access_token
        text: 消息文本

    Returns:
        dict: 飞书 API 响应
    """
    content = _build_post_content(text)
    return _request(
        "/im/v1/messages?receive_id_type=open_id",
        token=token,
        method="POST",
        body={
            "receive_id": open_id,
            "msg_type": "post",
            "content": content,
        },
    )


def send_text_to_chat(chat_id, token, text):
    """发送文本消息到指定群聊（post 格式，支持 markdown 渲染）。

    Args:
        chat_id: 群聊会话 ID (oc_xxx)
        token: tenant_access_token
        text: 消息文本

    Returns:
        dict: 飞书 API 响应
    """
    content = _build_post_content(text)
    return _request(
        "/im/v1/messages?receive_id_type=chat_id",
        token=token,
        method="POST",
        body={"receive_id": chat_id, "msg_type": "post", "content": content},
    )


def reply_message(msg_id, token, text):
    """回复消息（post 格式，支持 markdown 渲染）。

    Args:
        msg_id: 要回复的消息 ID
        token: tenant_access_token
        text: 回复文本

    Returns:
        dict: 飞书 API 响应
    """
    content = _build_post_content(text)
    return _request(
        f"/im/v1/messages/{msg_id}/reply",
        token=token,
        method="POST",
        body={"content": content, "msg_type": "post"},
    )


def send_file_message(token, open_id, file_key):
    """通过 file_key 发送文件消息给指定用户。

    Args:
        token: tenant_access_token
        open_id: 接收者的 open_id
        file_key: 上传文件后获取的 file_key

    Returns:
        dict: 飞书 API 响应
    """
    content = json.dumps({"file_key": file_key})
    return _request(
        "/im/v1/messages?receive_id_type=open_id",
        token=token,
        method="POST",
        body={"receive_id": open_id, "msg_type": "file", "content": content},
    )


# ── 消息反应 ──────────────────────────────────────────────────────────


def react_message(msg_id, token, emoji="Get"):
    """给消息添加表情反应。

    Args:
        msg_id: 消息 ID
        token: tenant_access_token
        emoji: 表情类型，常见值 Get / Done / Typing

    Returns:
        dict: 飞书 API 响应
    """
    return _request(
        f"/im/v1/messages/{msg_id}/reactions",
        token=token,
        method="POST",
        body={"reaction_type": {"emoji_type": emoji}},
    )


def delete_reaction(msg_id, reaction_id, token):
    """删除消息上的表情反应。

    reaction_id 可从 get_reactions 的返回结果获取。

    Args:
        msg_id: 消息 ID
        reaction_id: reaction 的 ID
        token: tenant_access_token

    Returns:
        dict: 飞书 API 响应
    """
    return _request(
        f"/im/v1/messages/{msg_id}/reactions/{reaction_id}",
        token=token,
        method="DELETE",
    )


def get_reactions(msg_id, token, page_size=20):
    """读取消息上的所有表情反应。

    Args:
        msg_id: 消息 ID
        token: tenant_access_token
        page_size: 每页数量

    Returns:
        dict: 飞书 API 响应
    """
    return _request(
        f"/im/v1/messages/{msg_id}/reactions?page_size={page_size}",
        token=token,
    )


# ── 资源下载 ──────────────────────────────────────────────────────────


def download_resource(message_id, file_key, token, resource_type="file",
                      output_path="", timeout=60):
    """同步下载消息中的资源文件到本地。

    Args:
        message_id: 消息 ID
        file_key: 资源的 file_key 或 image_key
        token: tenant_access_token
        resource_type: 资源类型，"image" 或 "file"
        output_path: 保存路径，为空时不保存到磁盘
        timeout: 超时秒数，默认 60s

    Returns:
        dict: {
            "code": 0,
            "path": "/path/to/file",    # 仅在 output_path 非空时存在
            "content_type": "application/pdf",
            "size": 123456,
        }
        失败时返回 {"code": -1, "msg": "错误原因"}
    """
    assert token, "download_resource requires a valid token"

    url = f"{API_BASE}/im/v1/messages/{message_id}/resources/{file_key}?type={resource_type}"
    headers = {"Authorization": f"Bearer {token}"}

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            content_type = resp.headers.get("Content-Type", "")
            content_length = int(resp.headers.get("Content-Length", len(body)))

            MAX_SIZE = 100 * 1024 * 1024  # 100MB
            if content_length > MAX_SIZE:
                return {
                    "code": -1,
                    "msg": f"超出大小限制 ({content_length / 1024 / 1024:.1f}MB > 100MB)",
                }

            result = {
                "code": 0,
                "content_type": content_type,
                "size": content_length,
            }

            if output_path:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(body)
                result["path"] = output_path

            return result

    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            detail = str(e)
        return {"code": -1, "msg": f"HTTP {e.code}: {detail}"}
    except urllib.error.URLError as e:
        return {"code": -1, "msg": f"网络错误: {e.reason}"}
    except OSError as e:
        return {"code": -1, "msg": f"存储失败: {e}"}
    except Exception as e:
        return {"code": -1, "msg": f"下载失败: {str(e)[:200]}"}


# ── 会话查询 ──────────────────────────────────────────────────────────


def list_messages(chat_id, token, page_size=20):
    """拉取会话消息列表（按创建时间倒序）。

    Args:
        chat_id: 会话 ID (oc_xxx)
        token: tenant_access_token
        page_size: 每页数量

    Returns:
        dict: 飞书 API 原始响应
    """
    return _request(
        f"/im/v1/messages?container_id_type=chat&container_id={chat_id}"
        f"&page_size={page_size}&sort_type=ByCreateTimeDesc",
        token=token,
    )


def list_chat_messages(chat_id, token, page_size=50):
    """拉取会话消息列表（按创建时间倒序）—— 命名更清晰的封装。

    Args:
        chat_id: 会话 ID (oc_xxx)
        token: tenant_access_token
        page_size: 每页数量

    Returns:
        dict: 飞书 API 原始响应
    """
    return list_messages(chat_id, token, page_size=page_size)


def list_chats(token, page_size=50):
    """列出 bot 加入的所有群聊/会话。

    Pre-condition: token 有效。

    Args:
        token: tenant_access_token
        page_size: 每页数量（最大 100）

    Returns:
        list[dict]: 会话列表，每个元素包含 chat_id, name 等字段
    """
    assert token, "list_chats requires a valid token"
    items = []
    page_token = ""
    while True:
        params = f"page_size={page_size}"
        if page_token:
            params += f"&page_token={page_token}"
        result = _request(f"/im/v1/chats?{params}", token=token)
        if result.get("code") != 0:
            return result
        data = result.get("data", {})
        items.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        page_token = data.get("page_token", "")
    return {"code": 0, "data": {"items": items}}


def poll_new_messages(chat_ids, token, processed_ids, page_size=50):
    """轮询新消息：从多个会话拉取消息，过滤出未处理的新消息。

    Pre-condition:
        - token 有效
        - processed_ids 是 set[str]

    Args:
        chat_ids: list[str]，要轮询的会话 ID 列表
        token: tenant_access_token
        processed_ids: set[str]，已处理过的消息 ID 集合
        page_size: 每个会话拉取的消息数量

    Returns:
        list[dict]: 新消息列表，按创建时间升序排列。
                    每条消息包含 message_id, chat_id, sender_id, text,
                    create_time 等字段。返回空列表表示没有新消息。
    """
    new_messages = []
    for cid in chat_ids:
        result = list_chat_messages(cid, token, page_size=page_size)
        if result.get("code") != 0:
            continue
        items = result.get("data", {}).get("items", [])
        for msg in items:
            msg_id = msg.get("message_id", "")
            if msg_id in processed_ids:
                continue
            sender_type = msg.get("sender", {}).get("sender_type", "")
            if sender_type == "app":
                processed_ids.add(msg_id)
                continue
            new_messages.append(msg)

    new_messages.sort(key=lambda m: m.get("create_time", "0"))
    return new_messages
