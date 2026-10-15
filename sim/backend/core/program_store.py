"""程序库：把脚本存到 sim/backend/data/programs.json，关页面不再丢。"""

from __future__ import annotations

import json
import time

from . import config
from .storage import write_json


class ProgramStore:
    def __init__(self):
        self.path = config.DATA_DIR / "programs.json"
        self.items = {}
        if self.path.exists():
            try:
                self.items = json.loads(self.path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                self.items = {}

    def _save(self):
        write_json(self.path, self.items)

    def list(self):
        return [{"name": k, "updated": v.get("updated"), "lines": len(
            (v.get("src") or "").splitlines())} for k, v in sorted(self.items.items())]

    def get(self, name):
        return self.items.get(name)

    def put(self, name, src):
        name = (name or "").strip()
        if not name:
            raise ValueError("程序名不能为空")
        if len(name) > 40:
            raise ValueError("程序名太长（上限 40 字）")
        self.items[name] = {"src": src, "updated": round(time.time(), 1)}
        self._save()
        return self.items[name]

    def remove(self, name):
        if name in self.items:
            del self.items[name]
            self._save()
            return True
        return False
