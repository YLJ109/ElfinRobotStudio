"""HTTP 逆解预览的有界计算槽；不承担运动或停止职责。"""
from __future__ import annotations

import asyncio
import threading


class PreviewIKService:
    def __init__(self, budget_s=0.8):
        self.budget_s = budget_s
        self._task = None

    def _retire(self, task):
        if self._task is task:
            self._task = None
        # 正确：请求取消后仍接住线程异常，避免未检索异常污染事件循环。
        if not task.cancelled():
            task.exception()

    async def solve(self, world, target, seed=None, allow_partial=False, *, position_only=False):
        if self._task is not None and not self._task.done():
            return {"ok": False, "reason_code": "ik_busy", "solve_ms": 0.,
                    "why": "逆解仍在处理上一目标，请稍后重试"}
        stop = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(world.ik_pose, target, seed, allow_partial,
            position_only=position_only, budget_s=self.budget_s, cancelled=stop.is_set))
        self._task = task
        task.add_done_callback(self._retire)
        try:
            # 知识点：取消 await 不会终止计算线程。槽位只能在计算真正退出后释放。
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            stop.set()
            raise
