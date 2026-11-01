"""多现场摆放文件：每个文件只保存仿真道具布局，不包含真机控制状态。"""
from __future__ import annotations

import copy
import json
import re
import threading
import time
from pathlib import Path

from .storage import write_json
from .objects import ObjectStore
from ..paths import DATA_DIR as DATA

NAME_RE = re.compile(r"^[^\\/:*?\"<>|]{1,64}$")


class SceneStore:
    def __init__(self, path=None, *, object_sanitizer=None):
        self.path = path or DATA / "scenes.json"
        from .scene_assets import SceneAssetStore
        lookup=SceneAssetStore(self.path.parent/'scene_assets').get
        self._object_sanitizer = object_sanitizer or (lambda row:ObjectStore._sanitize(row,asset_lookup=lookup))
        self._lock = threading.RLock()
        self.items = {}
        self._load()

    @staticmethod
    def _name(name):
        value = str(name or "").strip()
        if not NAME_RE.fullmatch(value) or any(ord(c) < 32 for c in value):
            raise ValueError("现场名称不能为空，且不能包含 \\/ : * ? \\\" < > |")
        return value

    def _objects(self, objects):
        if not isinstance(objects, list):
            raise ValueError("现场 objects 必须是数组")
        if len(objects) > 1000:
            raise ValueError("现场最多1000个物体，不能截断后保存")
        out = []
        for item in objects:
            if not isinstance(item, dict):
                raise ValueError("现场中的每个物体必须是对象")
            # Normalize new visual fields and legacy defaults before storing;
            # export/load/array copies then retain one validated contract.
            out.append(self._object_sanitizer(item))
        return out

    def _load(self):
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return
        source = raw.get("items", raw) if isinstance(raw, dict) else {}
        if isinstance(source, dict):
            for name, item in source.items():
                try:
                    key = self._name(name)
                    objects = self._objects(item.get("objects", []) if isinstance(item, dict) else [])
                    stamp = float(item.get("updated_at", time.time())) if isinstance(item, dict) else time.time()
                    self.items[key] = {"name": key, "objects": objects, "updated_at": stamp,
                                       "created_at": float(item.get("created_at", stamp)) if isinstance(item, dict) else stamp}
                except (ValueError, TypeError):
                    continue

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.path, {"version": 1, "items": self.items})

    def list(self):
        with self._lock:
            return [{"name": item["name"], "object_count": len(item["objects"]),
                     "updated_at": item["updated_at"]} for item in
                    sorted(self.items.values(), key=lambda x: x["updated_at"], reverse=True)]

    def get(self, name):
        key = self._name(name)
        with self._lock:
            item = self.items.get(key)
            return copy.deepcopy(item) if item else None

    def put(self, name, objects):
        key = self._name(name)
        rows = self._objects(objects)
        from ..domain.object_contacts import validate_contacts
        validate_contacts(rows)
        now = time.time()
        with self._lock:
            old = self.items.get(key)
            candidate = {**self.items, key: {"name": key, "objects": rows,
                               "created_at": old["created_at"] if old else now,
                               "updated_at": now}}
            write_json(self.path, {"version": 1, "items": candidate})
            self.items = candidate
            return {"name": key, "object_count": len(rows), "updated_at": now}

    def remove(self, name):
        key = self._name(name)
        with self._lock:
            existed = key in self.items
            if existed:
                candidate = {k: v for k, v in self.items.items() if k != key}
                write_json(self.path, {"version": 1, "items": candidate})
                self.items = candidate
            return existed
