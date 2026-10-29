"""操作系统持有控制进程锁；进程退出自动释放，不依赖易过期的 PID 文件。"""
from pathlib import Path
import os


class ServiceLease:
    def __init__(self, path):
        self.path = Path(path)
        self._file = None

    def acquire(self):
        if self._file is not None:
            raise RuntimeError("CONTROL_OWNER_BUSY: 当前服务已取得控制权")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            # 正确：固定锁住同一字节，不删除或替换锁文件，否则可出现双持有者。
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("CONTROL_OWNER_BUSY: 已有控制服务，请先关闭旧实例；禁止多 worker") from exc
        self._file = handle

    def release(self):
        handle, self._file = self._file, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
