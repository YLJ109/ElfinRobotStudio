"""末端工具档案与安装状态。

工具身份可能无法从普通气动夹爪读取，因此“控制器配置”“自动识别”和“人工确认”
始终分开保存。这个模块不向机器人发送任何探测或写入命令。
"""
from __future__ import annotations

import json
import math
import time
from copy import deepcopy

from . import config
from .storage import write_json


DEFAULT_TOOLS = [
    {"id": "none", "name": "无工具", "type": "none", "revision": "r1",
     "identity": {"source": "builtin", "hardware_id": None},
     "mount": {"translation_mm": [0, 0, 0], "quaternion_xyzw": [0, 0, 0, 1]},
     "tcp": {"name": "flange", "translation_mm": [0, 0, 0], "rpy_deg": [0, 0, 0], "status": "verified"},
     "payload": {"mass_kg": 0, "com_mm": [0, 0, 0], "status": "verified"},
     "actions": {}, "io_mapping": {}, "verification": "builtin"},
    {"id": "gripper-template", "name": "两指夹爪（模板）", "type": "parallel_gripper", "revision": "r1",
     "identity": {"source": "manual", "hardware_id": None},
     "mount": {"translation_mm": [0, 0, 0], "quaternion_xyzw": [0, 0, 0, 1]},
     "tcp": {"name": "grip-center", "translation_mm": [0, 0, 100], "rpy_deg": [0, 0, 0], "status": "unvalidated"},
     "payload": {"mass_kg": None, "com_mm": None, "status": "unknown"},
     "actions": {"open": "unmapped", "close": "unmapped"}, "io_mapping": {}, "verification": "template"},
    {"id": "suction-template", "name": "吸盘（模板）", "type": "suction", "revision": "r1",
     "identity": {"source": "manual", "hardware_id": None},
     "mount": {"translation_mm": [0, 0, 0], "quaternion_xyzw": [0, 0, 0, 1]},
     "tcp": {"name": "suction-center", "translation_mm": [0, 0, 120], "rpy_deg": [0, 0, 0], "status": "unvalidated"},
     "payload": {"mass_kg": None, "com_mm": None, "status": "unknown"},
     "actions": {"vacuum_on": "unmapped", "vacuum_off": "unmapped"}, "io_mapping": {}, "verification": "template"},
]


class ToolStore:
    def __init__(self):
        self.path = config.DATA_DIR / "tools.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.items = []
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.items = loaded.get("items", [])
            elif isinstance(loaded, list):
                self.items = loaded
        except (OSError, ValueError):
            pass
        if not self.items:
            self.items = deepcopy(DEFAULT_TOOLS)
            self._save()
        self.mounted = self._load_mounted()

    def _save(self):
        write_json(self.path, {"version": 1, "items": self.items})

    def _load_mounted(self):
        mounted_path = config.DATA_DIR / "mounted_tool.json"
        try:
            value = json.loads(mounted_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else self._unknown()
        except (OSError, ValueError):
            return self._unknown()

    @staticmethod
    def _unknown():
        return {"state": "unknown", "tool_id": None, "source": "none", "hardware_id": None,
                "confirmed_at": None, "revision": 0, "reason": "尚未读取或确认实际安装工具"}

    def _save_mounted(self):
        write_json(config.DATA_DIR / "mounted_tool.json", self.mounted)

    def list(self):
        return deepcopy(self.items)

    def get(self, tool_id):
        return next((deepcopy(t) for t in self.items if t.get("id") == tool_id), None)

    def snapshot(self):
        tool = self.get(self.mounted.get("tool_id")) if self.mounted.get("tool_id") else None
        mounted = deepcopy(self.mounted)
        mounted.setdefault("action_state", "idle")
        mounted.setdefault("last_action", None)
        return {"items": self.list(), "mounted": mounted, "tool": tool}

    def action_mapping(self, action):
        tool = self.get(self.mounted.get("tool_id")) if self.mounted.get("tool_id") else None
        if not tool:
            return None, None
        value = (tool.get("actions") or {}).get(action)
        if value in (None, "", "unmapped", "unknown"):
            return tool, None
        if isinstance(value, str):
            return tool, {"alias": value, "value": 1}
        if isinstance(value, dict) and isinstance(value.get("alias"), str):
            return tool, {"alias": value["alias"], "value": 1 if value.get("value", 1) else 0}
        return tool, None

    def record_action(self, action, state, result=None):
        self.mounted["action_state"] = state
        self.mounted["last_action"] = {"action": action, "state": state,
                                        "result": result, "at": round(time.time(), 3)}
        self._save_mounted()
        return self.snapshot()

    def add_or_update(self, value):
        if not isinstance(value, dict) or not isinstance(value.get("id"), str) or not value["id"].strip():
            raise ValueError("工具 id 必须是非空字符串")
        if len(value["id"]) > 80 or len(str(value.get("name", ""))) > 120:
            raise ValueError("工具标识或名称过长")
        tcp = value.get("tcp") or {}
        pos = tcp.get("translation_mm")
        if pos is not None and (not isinstance(pos, list) or len(pos) != 3 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in pos)):
            raise ValueError("TCP平移需要三个有限数")
        payload = value.get("payload") or {}
        mass = payload.get("mass_kg")
        if mass is not None and (not isinstance(mass, (int, float)) or not math.isfinite(mass) or mass < 0):
            raise ValueError("工具负载质量必须是非负有限数")
        com = payload.get("com_mm")
        if com is not None and (not isinstance(com, list) or len(com) != 3 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in com)):
            raise ValueError("工具质心需要三个有限数")
        inertia = payload.get("inertia_kgm2")
        if inertia is not None and (not isinstance(inertia, list) or len(inertia) != 6 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in inertia)):
            raise ValueError("工具惯量需要六个有限数（Ixx,Iyy,Izz,Ixy,Ixz,Iyz）")
        current = next((t for t in self.items if t.get("id") == value["id"]), None)
        if current:
            current.update(deepcopy(value))
        else:
            self.items.append(deepcopy(value))
        self._save()
        return self.get(value["id"])

    def select(self, tool_id, source="manual", hardware_id=None, reason=""):
        tool = self.get(tool_id)
        if not tool:
            raise ValueError("找不到工具档案：" + str(tool_id))
        if source not in {"manual", "detected", "controller_config"}:
            raise ValueError("工具来源无效")
        self.mounted = {"state": "manual_confirmed" if source == "manual" else
                        ("detected_verified" if source == "detected" else "controller_config_only"),
                        "tool_id": tool_id, "source": source, "hardware_id": hardware_id,
                        "confirmed_at": round(time.time(), 3), "revision": int(self.mounted.get("revision", 0)) + 1,
                        "reason": reason or ("用户确认实际已安装" if source == "manual" else "读取到控制器工具配置")}
        self._save_mounted()
        return self.snapshot()

    def clear(self, reason="用户确认无工具"):
        self.mounted = {"state": "none_confirmed", "tool_id": "none", "source": "manual",
                        "hardware_id": None, "confirmed_at": round(time.time(), 3),
                        "revision": int(self.mounted.get("revision", 0)) + 1, "reason": reason}
        self._save_mounted()
        return self.snapshot()

    def mark_unknown(self, reason):
        self.mounted = dict(self._unknown(), revision=int(self.mounted.get("revision", 0)) + 1, reason=reason)
        self._save_mounted()
        return self.snapshot()
