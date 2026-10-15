"""遥测层：回放环形缓冲、影子孪生偏差、就绪前联锁体检。

都只读，不发指令，所以放进这里的东西永远可以安全调用 —— 界面随时能查。

诚实口径（用户铁律）：控制器没给的数一律 None，前端显示 "--"；
仿真算出来的东西必须带 sim 标记；体检阈值里软件自设的会写明"本软件自设"。
"""

from __future__ import annotations

import math
import time
from collections import deque

from .safety import ALARM, GREEN, RANK

# 回放缓冲：20Hz × 5 分钟。每格 (墙钟, 单调钟, 六轴, 安全等级序号)
PLAY_SEC = 300.0
PLAY_CAP = int(PLAY_SEC * 20)

# 影子孪生：FK(q) 与控制器上报 PCS 的偏差。超过这个值说明模型/标定/负载对不上。
DEV_POS_WARN_MM = 3.0
DEV_POS_BAD_MM = 10.0
DEV_ROT_WARN_DEG = 1.0
DEV_ROT_BAD_DEG = 3.0


class Playback:
    """固定内存的关节轨迹环形缓冲，给时间轴回放用。

    故意不做"无界历史"：长跑一晚上不能把内存吃掉，也不能让导出变得不可预期。
    """

    def __init__(self, cap=PLAY_CAP):
        self.cap = cap
        self.t0 = 0.0            # 首帧墙钟
        self.items = deque(maxlen=cap)
        self.marks = []          # (墙钟, 文本, tone) —— 告警/执行起止打在轨道上

    def push(self, wall, q, level):
        self.items.append((wall, [round(v, 3) for v in q], RANK.get(level, 0)))
        self.t0 = self.items[0][0]

    def mark(self, wall, text, tone="warn"):
        self.marks.append((wall, text, tone))
        if len(self.marks) > 200:
            del self.marks[:len(self.marks) - 200]

    def at(self, frac):
        """按 0~1 取一帧（游标 → 关节角），给前端当"回放臂"的姿态源。"""
        if not self.items:
            return None
        i = min(len(self.items) - 1, max(0, int(frac * (len(self.items) - 1))))
        it = self.items[i]
        return {"i": i, "t": round(it[0] - self.t0, 2), "q": it[1], "level": it[2]}

    def slice(self, max_n=1500):
        """整段缓冲一次交给前端：本地抽稀到 max_n 帧，之后拖游标/播放都不再打网络。"""
        n = len(self.items)
        if not n:
            return {"n": 0, "t": [], "q": [[] for _ in range(6)], "levels": [], "dur_s": 0.0,
                    "marks": []}
        stride = max(1, -(-n // max_n))            # 向上取整
        picked = list(self.items)[::stride]
        t0 = self.items[0][0]
        return {"n": len(picked), "dur_s": round(self.items[-1][0] - t0, 1),
                "t": [round(it[0] - t0, 2) for it in picked],
                "q": [[it[1][j] for it in picked] for j in range(6)],
                "levels": [it[2] for it in picked],
                "marks": [[round(m[0] - t0, 1), m[1], m[2]] for m in self.marks][-40:]}

    def summary(self):
        if not self.items:
            return {"n": 0, "dur_s": 0.0, "t0": None}
        return {"n": len(self.items), "dur_s": round(self.items[-1][0] - self.t0, 1),
                "t0": round(self.t0, 3), "marks": [[round(t - self.t0, 1), s, tone]
                                                   for t, s, tone in self.marks][-40:]}


def deviation(model, q, pcs_base):
    """FK(q) ↔ 控制器实测 PCS 的偏差：模型可信度指标。

    我们的正解在真机上对账到 0.09mm/0.001°（2026-10-11 实测）。偏差突然变大
    通常意味着：装了未标定的工具/负载、控制器角度上报漂移、或取流与姿态不同步。
    """
    if not pcs_base or len(pcs_base) < 6 or q is None:
        # 链路没起来时必须明说"没读数"，绝不能拿 0 冒充"偏差为 0"
        return {"ok": False, "why": "缺控制器 PCS 读数或关节角"}
    fk = model.flange_pose(q)
    a = fk["xyz_mm"] + fk["rpy_deg"]           # 前 3 项 mm，后 3 项度
    b = [float(x) for x in pcs_base[:6]]
    dpos = math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))
    from .kinematics import rpy_to_mat, rotation_error
    drot = [abs(v) for v in rotation_error(rpy_to_mat(*[math.radians(v) for v in a[3:]]),
                                          rpy_to_mat(*[math.radians(v) for v in b[3:]]))]
    rmax = max(drot)
    level = GREEN
    if dpos >= DEV_POS_BAD_MM or rmax >= DEV_ROT_BAD_DEG:
        level = ALARM
    elif dpos >= DEV_POS_WARN_MM or rmax >= DEV_ROT_WARN_DEG:
        level = "red"
    return {"ok": True, "pos_mm": round(dpos, 2), "rot_deg": round(rmax, 2),
            "per_axis_rot": [round(v, 2) for v in drot],
            "fk": [round(v, 1) for v in a], "ctrl": [round(v, 1) for v in b],
            "level": level,
            "note": "FK 取法兰+20mm 参考点，与 PCS_Base 同口径（2026-10-11 对账 ≤0.09mm）"}


# 体检项：(id, 中文, 英文小注, 检查函数名)
# 每条都必须给"不通过时怎么办"，否则就是把报错甩给用户。
def preflight(mode, link, st, settings, safety, points_n, objects_active):
    """就绪前的联锁清单。全绿才允许一键就绪；任一项红就挡住并给处置。

    仿真的项不阻塞（没硬件可验），但必须明写"仿真不适用"而不是假装通过。
    """
    sim = mode == "sim"
    rows = []

    def add(k, label, sub, ok, detail, fix, blocking=True):
        rows.append({"k": k, "label": label, "sub": sub, "state": ok,
                     "detail": detail, "fix": fix, "blocking": blocking and not sim})

    fsm = st.get("fsm")
    if sim:
        add("mode", "驱动模式", "DRIVE MODE", True, "仿真：本清单不阻塞真机链路",
            "要验真机请在设置里切到真机模式", blocking=False)
    else:
        add("link", "命令通道", "CMD CHANNEL 10003", bool(link.get("cmd_up")),
            "已连接" if link.get("cmd_up") else "未连接",
            "检查控制器 10003 与网线；点对点直连时确认本机 IP 在同网段")
        rate = st.get("rate_hz")
        add("stream", "取流帧率", "DATA SHEET RATE", bool(rate and rate >= 10),
            (str(rate) + " Hz") if rate is not None else "尚无帧",
            "10004/5/6 端口被占或网线抖动；断开重连一次")
        v = st.get("volt48_in")
        add("power", "48V 供电", "BOX 48V", v is None or v >= 40.0,
            ("--" if v is None else format(v, ".2f") + " V"),
            "先按主按钮上电（Electrify）；仍偏低检查电箱输入空开与线缆")
        lost = st.get("ecat_lost")
        add("ecat", "EtherCAT 丢帧", "FIELDBUS LOST", not lost,
            ("累计 " + str(lost or 0) + " 帧") if lost is not None else "--",
            "电磁干扰或网线质量问题；先复位清错，反复出现就换屏蔽线")
    t = st.get("temps")
    if t:
        hot = max(t)
        add("temp", "关节温度", "JOINT TEMP", hot < 60.0,
            "最高 " + format(hot, ".1f") + " ℃（阈值 60℃ 为本软件自设，官方未给）",
            "停机自然冷却；持续偏高检查负载与加减速设定")
    ax = st.get("axis_status")
    if ax:
        bad = [i + 1 for i, v in enumerate(ax) if v != ax[0]]
        add("axis", "各轴状态一致", "AXIS STATUS", not bad,
            "J" + ",".join(map(str, bad)) + " 与其余轴状态不同" if bad else "全部 " + str(ax[0]),
            "掉线轴重新上电；仍不一致看该轴错误码")
    ae = st.get("axis_err") or []
    errs = [(i + 1, c) for i, c in enumerate(ae) if c]
    add("axerr", "轴级故障码", "AXIS ERROR CODE", not errs,
        "、".join(f"J{a}={c}" for a, c in errs) if errs else "全部为 0",
        "先按主按钮复位清错；清不掉按错误码查手册")
    add("err", "控制器故障码", "ROBOT ERROR", not st.get("err_code"),
        "无" if not st.get("err_code") else str(st.get("err_code")),
        "复位清错；反复出现说明有真实触发源（碰撞/超限位/温度）")
    lvl = (safety or {}).get("level")
    add("safe", "当前位姿安全占用", "LIMIT / COLLISION", lvl in (GREEN, None),
        ("等级 " + str(lvl)) + ("：占用 " + format((safety or {}).get("max_pct", 0), ".1f") + "%")
        if safety else "--",
        "先把手动移出限位/围栏区域，或放宽围栏设定")
    f = settings.get("fence") or {}
    okf = all(f.get(k) and f[k][0] < f[k][1] for k in ("x", "y", "z"))
    add("fence", "围栏参数合法", "WORK VOLUME", okf,
        "keep-in 盒" + ("（已启用）" if f.get("enabled") else "（未启用）"),
        "设置里把围栏的 min<max 填对，或关掉围栏")
    add("obj", "摆放物未侵入", "SCENE OBJECTS", not objects_active,
        str(points_n) + " 个点位；摆放物" + ("有下落中" if objects_active else "静止/无"),
        "摆放物是仿真道具：不会真撞，但会把告警链路点亮，先移开再就绪", blocking=False)
    if not sim:
        reach = fsm is not None and fsm >= 7
        add("fsm", "状态机可推进", "FSM PROGRESSIBLE", reach,
            "FSM=" + str(fsm) if fsm is not None else "未上报",
            "电箱没开机或没上 220V：先查电箱，网络侧推不动硬件断电")
    return {"rows": rows, "all_ok": all(r["state"] or not r["blocking"] for r in rows),
            "blocking": [r["k"] for r in rows if r["blocking"] and not r["state"]],
            "sim_note": sim}
