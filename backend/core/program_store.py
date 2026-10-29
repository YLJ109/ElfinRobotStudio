"""程序库：把脚本存到 backend/data/programs.json，关页面不再丢。"""

from __future__ import annotations

import json
import time
from copy import deepcopy

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
        return deepcopy(self.items.get(name))

    def put(self, name, src):
        if not isinstance(name, str):
            raise ValueError("程序名必须是文本")
        if not isinstance(src, str) or len(src) > 200000:
            raise ValueError("代码必须是文本，最多 200000 字符")
        name = (name or "").strip()
        if not name:
            raise ValueError("程序名不能为空")
        if len(name) > 40:
            raise ValueError("程序名太长（上限 40 字）")
        candidate = {**self.items, name: {"src": src, "updated": round(time.time(), 1)}}
        write_json(self.path, candidate)
        self.items = candidate
        return self.get(name)

    def remove(self, name):
        if name in self.items:
            candidate = {k: v for k, v in self.items.items() if k != name}
            write_json(self.path, candidate)
            self.items = candidate
            return True
        return False
