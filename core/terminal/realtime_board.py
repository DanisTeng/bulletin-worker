"""
RealtimeBoardManager — 后台线程周期性刷新留言板内容。

用 threading.Thread + subprocess.run（同步），不做任何 async。
后台线程只更新 self.last_board_text / self.last_board_index，主线程仅读取。
"""

import os
import subprocess
import threading
import time


class RealtimeBoardManager:
    """后台线程定期拉取 bb-index + bb-recent，主线程无阻塞读取。

    构造参数：
        tools_dir:   tools 目录路径，含 bb-index / bb-recent wrapper
        interval:    刷新间隔，秒（默认 1.0）
        recent_cnt:  bb-recent 拉取条数（默认 100）

    对外接口（线程安全）：
        last_board_text:  str | None
        last_board_index: int | None
        request_refresh():  立即刷新
        start() / stop()
    """

    def __init__(
        self,
        tools_dir: str,
        interval: float = 1.0,
        recent_cnt: int = 100,
    ):
        self._tools_dir = tools_dir
        self._interval = interval
        self._recent_cnt = recent_cnt

        self.last_board_text: str | None = None
        self.last_board_index: int | None = None

        self._last_cached_index: int | None = None
        self._refresh_flag = False
        self._stopped = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self):
        if self._thread is not None:
            return
        self._stopped = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stopped = True
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def request_refresh(self):
        with self._lock:
            self._refresh_flag = True

    def reset_cache(self):
        with self._lock:
            self._last_cached_index = None
            self.last_board_index = None
            self.last_board_text = None

    # ── 后台线程 ──────────────────────────────────────────────────

    def _run(self):
        while not self._stopped:
            self._do_refresh()

            # 等 interval 或被 request_refresh 唤醒
            deadline = time.monotonic() + self._interval
            while time.monotonic() < deadline:
                if self._stopped:
                    return
                with self._lock:
                    need = self._refresh_flag
                if need:
                    with self._lock:
                        self._refresh_flag = False
                    break
                time.sleep(0.05)

    def _do_refresh(self):
        # 查 index
        idx = self._exec("bb-index")
        if idx is not None:
            try:
                cur_idx = int(idx.strip())
            except (ValueError, TypeError):
                cur_idx = None

            with self._lock:
                cached = self._last_cached_index

            if cached is not None and cur_idx is not None and cur_idx == cached:
                return  # 无变化

            with self._lock:
                self._last_cached_index = cur_idx
                self.last_board_index = cur_idx

        # 拉内容
        raw = self._exec("bb-recent", str(self._recent_cnt))
        if raw is not None:
            with self._lock:
                self.last_board_text = raw

    def _exec(self, script_name: str, *args: str) -> str | None:
        script = os.path.join(self._tools_dir, script_name)
        if not os.path.isfile(script) or not os.access(script, os.X_OK):
            return None
        try:
            r = subprocess.run(
                [script, *args],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode != 0:
                return None
            out = r.stdout
            if out.endswith("\n"):
                out = out[:-1]
            return out
        except (OSError, subprocess.TimeoutExpired):
            return None
