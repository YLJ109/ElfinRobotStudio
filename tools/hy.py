"""我给真机做探测/验证时的硬闸：**任何运动指令发出前，控制器 override 必须 ≤5%**。

这条只约束我自己跑脚本的路径，不碰网页界面 —— 用户明确要求
"不要限制用户操作真机,速度等等"，所以后端的 motion_guard 默认是关的。
所有会动机器的脚本都必须走这里的 call()/move()，否则不给发。

用法：
    from tools.hy import Guard
    g = Guard(); g.move("MoveRelJ", [0, 5, 1, 2.0])      # 超过 5% 直接抛错
"""

from __future__ import annotations

import gzip
import json
import math
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8710"
MAX_OVERRIDE = 0.05          # 5%：用户 2026-10-12 再次确认的口径
MAX_STEP_DEG = 5.0           # 单步幅度上限（用户口径 3~5°）
MOTION_CMDS = {"MoveRelJ", "MoveJTo", "MoveLTo", "ShortJogJ", "LongJogJ", "GrpContinue"}


class GuardError(RuntimeError):
    pass


def get(path, base=BASE):
    r = urllib.request.Request(base + path, headers={"Accept-Encoding": "gzip"})
    b = urllib.request.urlopen(r, timeout=15).read()
    try:
        b = gzip.decompress(b)
    except OSError:
        pass
    return json.loads(b.decode("utf-8"))


def post(path, body=None, base=BASE, timeout=40):
    data = json.dumps(body or {}).encode()
    r = urllib.request.Request(base + path, data=data, method="POST",
                               headers={"Content-Type": "application/json",
                                        "Accept-Encoding": "gzip"})
    b = urllib.request.urlopen(r, timeout=timeout).read()
    try:
        b = gzip.decompress(b)
    except OSError:
        pass
    return json.loads(b.decode("utf-8"))


class Guard:
    def __init__(self, base=BASE, max_override=MAX_OVERRIDE):
        self.base = base
        self.max_override = max_override

    def state(self):
        return get("/api/state", self.base)

    def move_j6(self, delta):
        """标准 MOVEJ，仅允许 J6；实际 override ≤5%，单次幅度 ≤2°。"""
        if type(delta) not in (int, float) or not math.isfinite(delta) or abs(delta) > 2:
            raise GuardError("J6 delta 必须是有限数字且绝对值 ≤2°")
        s = self.state()
        real = s.get("real") or {}
        ov = real.get("override")
        limit = min(MAX_OVERRIDE, self.max_override)
        if (s.get("mode") != "real" or type(ov) not in (int, float)
                or not math.isfinite(ov) or not 0 <= ov <= limit):
            raise GuardError("需要真机回读且实际 override ≤5%（同时满足 Guard 上限）")
        joints = real.get("joints")
        if (not isinstance(joints, list) or len(joints) != 6
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in joints)):
            raise GuardError("缺少有效六轴真机回读，不盲发 MOVEJ")
        target = list(joints)
        target[5] += delta
        preview=post('/api/preview',{'joints':target,'allowed_axes':[5]},self.base)
        if (preview.get('ok') is not True or not isinstance(preview.get('plan_id'),str)
                or not preview['plan_id'] or preview.get('preview')!=target
                or preview.get('target_plan',{}).get('allowed_axes')!=[5]):
            raise GuardError(preview.get('why') or '限定J6计划未被后端确认，不发送运动')
        return post("/api/movej", {"joints": target, "allowed_axes": [5],'plan_id':preview['plan_id']}, self.base)

    def check(self, name=None, params=None):
        """返回 (ok, 说明)。任何一条不满足就不该发这条指令。"""
        s = self.state()
        real = s.get("real") or {}
        ov = real.get("override")
        if s.get("mode") != "real":
            return True, "仿真模式，不发硬件指令"
        if ov is None:
            return False, "控制器 override 读不到（取流没连），不盲发"
        if ov > self.max_override + 1e-9:
            return False, (f"override={ov:.2f}（{ov * 100:.0f}%）超过我允许的 "
                           f"{self.max_override * 100:.0f}% —— 先把速度降回来再跑")
        if name in MOTION_CMDS and params:
            try:
                dist = abs(float(params[-1])) if name == "MoveRelJ" else None
            except (TypeError, ValueError):
                dist = None
            if dist is not None and dist > MAX_STEP_DEG:
                return False, f"MoveRelJ 单步 {dist}° 超过 {MAX_STEP_DEG}° 上限"
            if name == "MoveRelJ" and int(params[1]) != 5:
                return False, f"我只被允许动 J6（axis=5），这次要动 axis={params[1]}"
        return True, f"override={ov * 100:.0f}% 合规"

    def ask(self, name, params=()):
        """带闸的指令通道：先 check，不合规直接抛，不发报文。"""
        ok, why = self.check(name, list(params))
        if not ok:
            raise GuardError(why + f" —— 已拒绝发送 {name}")
        return post("/api/real/ask", {"name": name, "params": list(params)}, self.base)

    def set_speed(self, pct):
        if float(pct) > self.max_override * 100 + 1e-9:
            raise GuardError(f"我只允许把速度设到 ≤{self.max_override * 100:.0f}%，"
                             f"这次要设 {pct}% —— 拒绝下发")
        return post("/api/speed", {"pct": float(pct)}, self.base)
