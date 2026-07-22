"""
RealtimeBoardManager — 后台异步刷新留言板内容。

职责：
  - 独立 asyncio 协程，周期调用 tools/bb-index + bb-recent 刷新 board 内容
  - 通过 asyncio.Event 支持外部触发即时刷新（例如 Ctrl+D 发帖后）
  - 线程安全：不碰 Textual widget，只维护好最新 board 文本和 index，调用方取走自行渲染

用法（在 Textual App 中）:

    self._board_mgr = RealtimeBoardManager(tools_dir="/path/to/tools")
    self._board_mgr.start()

    在 Ctrl+D 回调中：
        self._board_mgr.request_refresh()

    在 tick 中（或任何协程中）获取内容：
        content = self._board_mgr.last_board_text
        idx = self._board_mgr.last_board_index

    退出时：
        self._board_mgr.stop()
"""

import asyncio
import os
import subprocess

_REFRESH_INTERVAL = 1.0  # 默认 1 秒刷新一次
_RECENT_LINES = 100      # 默认拉取 100 条


class RealtimeBoardManager:
    """异步后台 board 刷新管理器。

    构造参数：
        tools_dir:   tools 目录路径，含 bb-index / bb-recent 等 wrapper
        interval:    刷新间隔，秒（默认 1.0）
        recent_cnt:  bb-recent 拉取条数（默认 100）

    对外接口（线程安全）：
        last_board_text:  str | None — 最新拉取的 board 内容
        last_board_index: int | None — 最新 index 值
        request_refresh():  标记需要立即刷新（通过 asyncio.Event）

    生命周期：
        start()  →  创建后台 task
        stop()   →  取消后台 task
    """

    def __init__(
        self,
        tools_dir: str,
        interval: float = _REFRESH_INTERVAL,
        recent_cnt: int = _RECENT_LINES,
    ):
        self._tools_dir = tools_dir
        self._interval = interval
        self._recent_cnt = recent_cnt

        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        # 对外公开的最新数据（线程安全：只在后台协程中写入，读取无竞态）
        self.last_board_text: str | None = None
        self.last_board_index: int | None = None

        # 内部状态
        self._last_cached_index: int | None = None
        self._refresh_event = asyncio.Event()
        self._stopped = False

    # ── 生命周期 ──────────────────────────────────────────────────

    def start(self):
        """启动后台刷新协程。必须在 event loop 已运行的环境中调用。"""
        if self._task is not None:
            return  # 已经在跑
        self._loop = asyncio.get_running_loop()
        self._stopped = False
        self._refresh_event.clear()
        self._task = self._loop.create_task(self._run())

    def stop(self):
        """停止后台刷新协程。"""
        self._stopped = True
        self._refresh_event.set()  # 唤醒让协程退出
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._loop = None

    def request_refresh(self):
        """请求立即刷新一次（非阻塞，设 event）。"""
        if not self._stopped:
            self._refresh_event.set()

    def reset_cache(self):
        """重置内部的 index 缓存，下次刷新无条件拉取新内容。

        用于 /clear 等操作后强制全量刷新。
        """
        self._last_cached_index = None
        self.last_board_index = None
        self.last_board_text = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ── 内部刷新逻辑 ──────────────────────────────────────────────

    async def _run(self):
        """后台循环：轮流触发刷新和等待。"""
        while not self._stopped:
            await self._refresh_once()

            # 等 interval 秒，或被 request_refresh 唤醒
            try:
                await asyncio.wait_for(
                    self._refresh_event.wait(),
                    timeout=self._interval,
                )
            except asyncio.TimeoutError:
                pass

            # 被唤醒后重置 event，以便下一轮 wait
            if self._refresh_event.is_set():
                self._refresh_event.clear()

    async def _refresh_once(self):
        """拉取一次最新 index + board 内容。"""
        idx = await self._exec_async("bb-index")
        if idx is not None:
            try:
                cur_idx = int(idx.strip())
            except (ValueError, TypeError):
                cur_idx = None

            # 与上次相同时跳过 bb-recent
            if (
                self._last_cached_index is not None
                and cur_idx is not None
                and cur_idx == self._last_cached_index
            ):
                return

            self._last_cached_index = cur_idx
            self.last_board_index = cur_idx

        raw = await self._exec_async("bb-recent", str(self._recent_cnt))
        if raw is not None:
            self.last_board_text = raw

    async def _exec_async(self, script_name: str, *args: str) -> str | None:
        """异步执行 tools 目录下的 wrapper 脚本，返回 stdout（去掉尾部换行）。

        使用 asyncio.create_subprocess_exec 避免阻塞 event loop。
        """
        script = os.path.join(self._tools_dir, script_name)
        if not os.path.isfile(script) or not os.access(script, os.X_OK):
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                script,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=10
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return None

            if proc.returncode != 0:
                return None
            out = stdout.decode("utf-8")
            if out.endswith("\n"):
                out = out[:-1]
            return out
        except (OSError, subprocess.SubprocessError):
            return None
