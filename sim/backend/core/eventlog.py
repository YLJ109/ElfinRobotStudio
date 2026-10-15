"""双轨日志：指令审计 + 运行事件。内存环形缓冲 + 落盘 JSONL，可导出 CSV。"""

from __future__ import annotations

import csv
import io
import json
import os
import threading
import time
from collections import deque
from pathlib import Path

from . import config

_CSV_INJECT_PREFIX = ("=", "+", "-", "@", chr(9), chr(13))

KINDS = ("cmd", "event", "alarm", "ai", "program", "point", "link", "sys",
         "interlock", "perm", "jog", "ready", "speed", "power", "free", "execution")


class EventLog:
    def __init__(self, keep=None):
        self._lock = threading.RLock()
        self.keep = keep or config.get_settings()["log_keep"]
        self.buf = deque(maxlen=self.keep)
        self.path = config.DATA_DIR / "events.jsonl"
        self.seq = 0

    MAX_BYTES = 8 * 1024 * 1024      # 落盘超过 8MB 就轮转一次，避免长期跑吃满磁盘

    def _rotate_if_big(self):
        try:
            if self.path.exists() and self.path.stat().st_size > self.MAX_BYTES:
                old = self.path.with_suffix(".1.jsonl")
                if old.exists():
                    old.unlink()
                self.path.rename(old)
        except OSError:
            pass

    def add(self, kind, text, extra=None):
        if kind not in KINDS:
            # 记日志本身不该把请求打挂，降级成 event 并留下痕迹
            text = f"[kind={kind}] {text}"
            kind = "event"
        with self._lock:
            self.seq += 1
            row = {"seq": self.seq, "t": round(time.time(), 3), "kind": kind, "text": text}
            if extra:
                row["extra"] = extra
            self.buf.append(row)
            try:
                self._rotate_if_big()
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            except OSError:
                pass
            return row

    def tail(self, n=200, kind=None):
        with self._lock:
            rows = list(self.buf)
        if kind:
            rows = [r for r in rows if r["kind"] == kind]
        return rows[-n:]

    def since(self, seq):
        with self._lock:
            rows = list(self.buf)
        gap = bool(rows and seq < rows[0]["seq"] - 1)
        return {"rows": [row for row in rows if row["seq"] > seq], "gap": gap, "seq": self.seq}

    @staticmethod
    def _csv_safe(v):
        """挡 CSV 公式注入：以 = + - @ 或制表符开头的值，Excel 会当公式执行。
        日志正文里可能出现用户输入（点位名、程序名），所以一律加前缀单引号。"""
        s = str(v)
        return "'" + s if s[:1] in _CSV_INJECT_PREFIX else s

    def export_csv(self, kind=None):
        with self._lock:
            rows = list(self.buf)
        if kind:
            rows = [r for r in rows if r["kind"] == kind]
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["seq", "time", "kind", "text", "extra"])
        for r in rows:
            w.writerow([r["seq"], time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["t"])),
                        self._csv_safe(r["kind"]), self._csv_safe(r["text"]),
                        self._csv_safe(json.dumps(r.get("extra", {}), ensure_ascii=False))])
        return out.getvalue()

    def clear_file(self):
        with self._lock:
            self.buf.clear()
        try:
            self.path.write_text("", encoding="utf-8")
        except OSError:
            pass
