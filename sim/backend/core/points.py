"""点位存储：每个点记录关节角 + 当时的笛卡尔位姿 + 来源，来源不明的一律拒绝执行。"""

from __future__ import annotations

import json
import time
import math
import re

from . import config
from .storage import write_json


class PointStore:
    def __init__(self):
        self.path = config.DATA_DIR / "points.json"
        self.items = []
        if self.path.exists():
            try:
                self.items = json.loads(self.path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                self.items = []
        reserved = {p.get('code') or p.get('name') for p in self.items if re.fullmatch(r'P[1-9]\d*', p.get('code') or p.get('name', ''))}
        used = set()
        for p in self.items:
            code = p.get('code') or (p.get('name') if re.fullmatch(r'P[1-9]\d*', p.get('name', '')) else None)
            if not code or not re.fullmatch(r'P[1-9]\d*', code) or code in used:
                number = 1
                while f'P{number}' in used | reserved: number += 1
                code = f'P{number}'
            p['code'] = code
            used.add(code)
        self.meta_path = config.DATA_DIR / 'points_meta.json'
        self.next_number = max([int(p['code'][1:]) for p in self.items] or [0]) + 1
        if self.meta_path.exists():
            try: self.next_number = max(self.next_number, int(json.loads(self.meta_path.read_text(encoding='utf-8'))['next_number']))
            except (KeyError, ValueError, OSError): pass

    def _save(self):
        write_json(self.path, self.items)

    def list(self):
        return list(self.items)

    def get(self, pid):
        return next((p for p in self.items if str(p["id"]) == str(pid)), None)

    def resolve(self, ref):
        return self.get(ref) or next((p for p in self.items if ref in (p['code'], p['name'])), None)

    def _name(self, name, code, exclude=None):
        name = str(name or code).strip()
        if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise ValueError('点位名称需为 1–80 个可见字符')
        if any(p['id'] != exclude and name in (p['name'], p['code']) for p in self.items):
            raise ValueError('点位名称已存在或与其他点位编号冲突')
        if re.fullmatch(r'P[1-9]\d*', name) and name != code:
            raise ValueError('P 编号为固定编号，请使用其他名称')
        return name

    def add(self, name, joints, tcp=None, source="sim", note=""):
        if len(joints) != 6 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in joints):
            raise ValueError("点位必须是 6 个数值关节角")
        number = self.next_number
        code = f'P{number}'
        pid = max(int(time.time() * 1000) % 10**12, max([p['id'] for p in self.items] or [0]) + 1)
        p = {"id": pid, "code": code,
             "name": self._name(name, code),
             "joints": [round(float(v), 3) for v in joints],
             "tcp": tcp, "source": source, "note": note,
             "created": round(time.time(), 1)}
        self.items.append(p)
        self.next_number += 1
        write_json(self.meta_path, {'next_number': self.next_number})
        self._save()
        return p

    def update(self, pid, patch):
        p = self.get(pid)
        if not p:
            return None
        if set(patch) != {'name'}:
            raise ValueError('此接口只允许修改点位名称')
        if not isinstance(patch['name'], str) or not patch['name'].strip():
            raise ValueError('点位名称不能为空')
        p['name'] = self._name(patch['name'], p['code'], p['id'])
        self._save()
        return p

    def remove(self, pid):
        before = len(self.items)
        self.items = [x for x in self.items if str(x["id"]) != str(pid)]
        if len(self.items) != before:
            self._save()
            return True
        return False

    def reorder(self, ids):
        order = {str(v): i for i, v in enumerate(ids)}
        self.items.sort(key=lambda p: order.get(str(p["id"]), 999))
        self._save()
        return self.items
