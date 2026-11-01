"""末端工具档案与安装状态。

工具身份可能无法从普通气动夹爪读取，因此“控制器配置”“自动识别”和“人工确认”
始终分开保存。这个模块不向机器人发送任何探测或写入命令。
"""
from __future__ import annotations

import json
import hashlib
import math
import time
from copy import deepcopy
from pathlib import Path

from . import config
from .storage import write_json
from ..domain.suction import validate_mapping, SUCTION_ACTIONS
from ..domain.tool_actions import TOOL_ACTIONS


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
     "actions": {action:'unmapped' for action in SUCTION_ACTIONS}, "io_mapping": {}, "verification": "template"},
]


class ToolStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else config.DATA_DIR / "tools.json"
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
        self.mounted = self._load_mounted()

    def _save(self):
        write_json(self.path, {"version": 1, "items": self.items, 'revisions':self._history()})

    def _history(self):
        if not hasattr(self,'_revision_history'):
            try:
                saved=json.loads(self.path.read_text(encoding='utf-8'))
                self._revision_history=saved.get('revisions',{}) if isinstance(saved,dict) else {}
                if not isinstance(self._revision_history,dict): self._revision_history={}
            except (ValueError,OSError):
                self._revision_history={}
        return self._revision_history

    def revisions(self, tool_id):
        return deepcopy(self._history().get(tool_id,[]))

    @staticmethod
    def _profile_changes(before, after, prefix=''):
        changes=[]
        for key in sorted(set(before)|set(after)):
            name=prefix+key
            old,new=before.get(key),after.get(key)
            if isinstance(old,dict) and isinstance(new,dict):
                changes.extend(ToolStore._profile_changes(old,new,name+'.'))
            elif old!=new:
                changes.append({'field':name,'before':deepcopy(old),'after':deepcopy(new)})
        return changes

    def _load_mounted(self):
        mounted_path = self.path.parent / "mounted_tool.json"
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
        write_json(self.path.parent / "mounted_tool.json", self.mounted)

    def _commit_mounted(self, candidate):
        write_json(self.path.parent / "mounted_tool.json", candidate)
        self.mounted = candidate

    def list(self):
        return deepcopy(self.items)

    def get(self, tool_id):
        return next((deepcopy(t) for t in self.items if t.get("id") == tool_id), None)

    def snapshot(self):
        tool = self.get(self.mounted.get("tool_id")) if self.mounted.get("tool_id") else None
        mounted = deepcopy(self.mounted)
        mounted.setdefault("action_state", "idle")
        mounted.setdefault("last_action", None)
        return {"items": self.list(), "mounted": mounted, "tool": tool,
                'profile_history':self.revisions(tool['id']) if tool else []}

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

    @staticmethod
    def profile_digest(tool):
        return hashlib.sha256(json.dumps(tool,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()

    def confirm_io(self, confirmation):
        candidate=deepcopy(self.mounted)
        candidate['io_confirmation']=deepcopy(confirmation)
        self._commit_mounted(candidate)
        return self.snapshot()

    def record_action(self, action, state, result=None):
        candidate = deepcopy(self.mounted)
        candidate["action_state"] = state
        candidate["last_action"] = {"action": action, "state": state,
                                        "result": result, "at": round(time.time(), 3)}
        self._commit_mounted(candidate)
        return self.snapshot()

    def add_or_update(self, value):
        if not isinstance(value, dict) or not isinstance(value.get("id"), str) or not value["id"].strip():
            raise ValueError("工具 id 必须是非空字符串")
        if len(value["id"]) > 80 or len(str(value.get("name", ""))) > 120:
            raise ValueError("工具标识或名称过长")
        allowed={'id','name','type','revision','identity','mount','tcp','payload','actions',
                 'io_mapping','verification','suction_io','envelope','collision','provenance','visual','geometry'}
        if set(value)-allowed:
            raise ValueError('工具档案包含不支持的字段')
        if 'name' in value and (not isinstance(value['name'],str) or not value['name'].strip()):
            raise ValueError('工具名称必须是非空文本')
        if 'type' in value and value['type'] not in ('none','suction','parallel_gripper','tool_changer'):
            raise ValueError('工具类型无效')
        for key in ('tcp','payload','identity','mount','actions','io_mapping','geometry'):
            if key in value and not isinstance(value[key],dict):
                raise ValueError(key+'必须是对象')
        def vector(data,count,label):
            if not isinstance(data,list) or len(data)!=count or any(type(v) not in (int,float) or not math.isfinite(v) for v in data):
                raise ValueError(label+'需要指定数量的有限数值')
        if 'geometry' in value:
            geometry=value['geometry']
            if (set(geometry)-{'status','frame','conservative','capsules','verification'}
                    or geometry.get('status') not in ('unknown','unvalidated','verified')
                    or geometry.get('frame')!='mount' or type(geometry.get('conservative')) is not bool
                    or not isinstance(geometry.get('capsules'),list) or not 1<=len(geometry['capsules'])<=128):
                raise ValueError('工具包络需要有界的安装坐标系胶囊数据')
            for cap in geometry['capsules']:
                if not isinstance(cap,dict) or set(cap)!={'a','b','r_mm'}:raise ValueError('工具包络胶囊字段无效')
                vector(cap['a'],3,'包络起点');vector(cap['b'],3,'包络终点')
                if (any(abs(v)>1e6 for v in cap['a']+cap['b']) or type(cap['r_mm']) not in (int,float)
                        or not math.isfinite(cap['r_mm']) or not 0<cap['r_mm']<=1e6):raise ValueError('工具包络尺寸无效')
            if 'verification' in geometry:
                record=geometry['verification']
                strings={'record_id','method','verified_by','verified_at','tool_revision','subject_sha256'}
                if (not isinstance(record,dict) or set(record)!=strings|{'mounted_revision'}
                        or any(not isinstance(record[k],str) or not 1<=len(record[k])<=256 for k in strings)
                        or type(record['mounted_revision']) is not int or record['mounted_revision']<1):
                    raise ValueError('工具包络验证记录格式无效')
            # Persist supplied evidence only. Trust, revision and digest checks
            # remain in the existing physical-tool resolver; no records issued.
        for section,key,count in (('mount','translation_mm',3),('mount','quaternion_xyzw',4),
                                  ('tcp','translation_mm',3),('tcp','rpy_deg',3),
                                  ('payload','com_mm',3),('payload','inertia_kgm2',6)):
            if key in value.get(section,{}) and value[section][key] is not None:
                vector(value[section][key],count,section+'.'+key)
        actions=value.get('actions',{})
        for name,entry in actions.items():
            if name not in TOOL_ACTIONS:
                raise ValueError('工具动作不在共享契约内')
            if isinstance(entry,str):
                if not 1<=len(entry)<=64: raise ValueError('动作映射别名无效')
            elif (not isinstance(entry,dict) or set(entry)-{'alias','value'}
                  or not isinstance(entry.get('alias'),str) or not entry['alias'].strip()
                  or type(entry.get('value',1)) is not int or entry.get('value',1) not in (0,1)):
                raise ValueError('动作映射必须明确别名和输出电平')
        if 'suction_io' in value:
            validate_mapping(value['suction_io'])
        if 'revision' in value and (not isinstance(value['revision'],str) or not 1<=len(value['revision'])<=120):
            raise ValueError('工具修订必须是1至120字符')
        tcp = value.get("tcp") or {}
        pos = tcp.get("translation_mm")
        if pos is not None and (not isinstance(pos, list) or len(pos) != 3 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in pos)):
            raise ValueError("TCP平移需要三个有限数")
        payload = value.get("payload") or {}
        mass = payload.get("mass_kg")
        if mass is not None and (type(mass) not in (int, float) or not math.isfinite(mass) or mass < 0):
            raise ValueError("工具负载质量必须是非负有限数")
        com = payload.get("com_mm")
        if com is not None and (not isinstance(com, list) or len(com) != 3 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in com)):
            raise ValueError("工具质心需要三个有限数")
        inertia = payload.get("inertia_kgm2")
        if inertia is not None and (not isinstance(inertia, list) or len(inertia) != 6 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in inertia)):
            raise ValueError("工具惯量需要六个有限数（Ixx,Iyy,Izz,Ixy,Ixz,Iyz）")
        candidate = deepcopy(self.items)
        current = next((t for t in candidate if t.get("id") == value["id"]), None)
        if current:
            current.update(deepcopy(value))
        else:
            candidate.append(deepcopy(value))
        previous=self.get(value['id'])
        updated=next(t for t in candidate if t['id']==value['id'])
        revisions=deepcopy(self._history())
        if previous!=updated:
            rows=revisions.setdefault(value['id'],[])
            if previous and not rows:
                rows.append({'revision':previous.get('revision'),'digest':self.profile_digest(previous),
                             'profile':previous,'source':previous.get('provenance',previous.get('identity')),
                             'at':None,'changes':[]})
            rows.append({'revision':updated.get('revision'),'digest':self.profile_digest(updated),
                         'profile':deepcopy(updated),'source':updated.get('provenance',updated.get('identity')),
                         'at':round(time.time(),3),'changes':self._profile_changes(previous or {},updated)})
            revisions[value['id']]=rows[-32:]
        if self.mounted.get('tool_id')==value['id'] and previous != updated:
            # Fail closed first: if profile persistence fails, old installation
            # confirmation stays revoked rather than qualifying edited geometry.
            self.mark_unknown('工具档案已修改，需重新确认安装与IO映射')
        write_json(self.path, {"version": 1, "items": candidate, 'revisions':revisions})
        self.items = candidate
        self._revision_history=revisions
        return self.get(value["id"])

    def select(self, tool_id, source="manual", hardware_id=None, reason=""):
        tool = self.get(tool_id)
        if not tool:
            raise ValueError("找不到工具档案：" + str(tool_id))
        if source not in {"manual", "detected", "controller_config"}:
            raise ValueError("工具来源无效")
        candidate = {"state": "manual_confirmed" if source == "manual" else
                        ("detected_verified" if source == "detected" else "controller_config_only"),
                        "tool_id": tool_id, "source": source, "hardware_id": hardware_id,
                        "confirmed_at": round(time.time(), 3), "revision": int(self.mounted.get("revision", 0)) + 1,
                        "profile_revision":tool.get('revision'), "profile_digest":self.profile_digest(tool),
                        "reason": reason or ("用户确认实际已安装" if source == "manual" else "读取到控制器工具配置")}
        self._commit_mounted(candidate)
        return self.snapshot()

    def clear(self, reason="用户确认无工具"):
        candidate = {"state": "none_confirmed", "tool_id": "none", "source": "manual",
                        "hardware_id": None, "confirmed_at": round(time.time(), 3),
                        "revision": int(self.mounted.get("revision", 0)) + 1, "reason": reason}
        self._commit_mounted(candidate)
        return self.snapshot()

    def mark_unknown(self, reason):
        candidate = dict(self._unknown(), revision=int(self.mounted.get("revision", 0)) + 1, reason=reason)
        self._commit_mounted(candidate)
        return self.snapshot()
