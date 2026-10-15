"""One owner for submitted actions; cancellation revokes the action immediately."""
from __future__ import annotations

import asyncio
import time


class MotionExecutor:
    def __init__(self, log):
        self.log = log
        self.epoch = 0
        self.task = None
        self.state = "idle"
        self.error = ""
        self.action = ""
        self.started = None

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    def check(self, epoch):
        if epoch != self.epoch:
            raise asyncio.CancelledError()

    def submit(self, action, run, completed=None):
        if self.busy:
            return False
        epoch = self.epoch
        self.state, self.error, self.action = "running", "", action
        self.started = time.time()

        async def execute():
            try:
                await run(epoch)
                self.check(epoch)
                self.state = "completed"
                if completed:
                    completed()
                self.log("execution", action + " completed", {"epoch": epoch})
            except asyncio.CancelledError:
                if epoch == self.epoch:
                    self.state = "cancelled"
                raise
            except Exception as exc:
                if epoch == self.epoch:
                    self.state, self.error = "failed", str(exc)
                self.log("execution", action + " failed: " + str(exc), {"epoch": epoch})

        self.task = asyncio.create_task(execute())
        return True

    def revoke(self, reason):
        self.epoch += 1
        self.state, self.error = "cancelled", reason
        task = self.task
        if task and not task.done():
            task.cancel()
        return task

    async def cancel(self, reason):
        task = self.revoke(reason)
        if task and task is not asyncio.current_task():
            try:
                await task
            except asyncio.CancelledError:
                pass

    def snapshot(self):
        return {"epoch": self.epoch, "state": self.state, "busy": self.busy,
                "action": self.action, "error": self.error, "started": self.started}
