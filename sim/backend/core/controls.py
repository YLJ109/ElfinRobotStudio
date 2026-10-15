"""主操作按钮的状态推导：任何时刻只允许一个合法动作，标签、颜色、可点性都由它算。

设计约束（用户 2026-10-12 明确要求"就一个按钮"）：
  * 界面上不再有"许可等级"选择器 —— 能不能动**等于机器人是不是真在就绪态**，
    软件自己的开关不算数，免得出现"UI 显示运动、控制器其实没使能"的假象。
  * 每一步都要说清"下一步要发什么指令、为什么要发/为什么不能发"。

FSM 档位与推进指令是 2026-10-12 在这台 E05 上逐条实测出来的：
    7 --Electrify--> 14 --StartMaster--> 15 --> 24 --GrpEnable--> 33
  （ConnectToBox 在 FSM=14 实测回 20018，不在自动链里，只留作手动可选项。）
"""

from __future__ import annotations

# 各档位的英文名，HUD 的"中文主 + 英文小注"层级用
_SUB = {"connect": "CONNECT CONTROLLER", "link": "AWAIT CONTROLLER", "reset": "RESET ERROR",
        "electrify": "POWER UP", "start_master": "START MASTER", "ready": "READY",
        "cancel_ready": "CANCEL READY", "sim_run": "SIMULATION READY"}

# 每一步要发的指令：(指令名, 参数, 目标 FSM)。None 表示不占目标档位。
STEP_CMD = {
    "electrify": ("Electrify", (), 14),
    "start_master": ("StartMaster", (), 24),
    "ready": ("GrpEnable", (0,), 33),
}

READY_FSM = 33


def motion_granted(fsm, enabled):
    """运动许可 = 控制器真的处在就绪且使能态。这是唯一可信的真相来源。"""
    try:
        return bool(enabled) and int(fsm or 0) >= READY_FSM
    except (TypeError, ValueError):
        return False


def derive(mode, link, st, settings):
    """link: {"stream_up","cmd_up","host"}；st: 当前状态读数（fsm/enabled/err_code/moving/free_drive）。

    返回主按钮 + 徽标 + 许可，全部一次算完，前端只渲染不算账。
    """
    fsm = st.get("fsm")
    enabled = st.get("enabled") or 0
    err = st.get("err_code") or 0
    sim = mode == "sim"
    out = {
        "mode": mode,
        "sim": sim,
        "fsm": fsm,
        "fsm_hint": _fsm_hint(fsm),
        "enabled": enabled,
        # 仿真不连接控制柜：允许直接执行运动/程序，电源状态仍由仿真状态机单独展示。
        # 真机继续严格要求控制器 FSM=33 且 enabled=1。
        "motion_granted": True if sim else motion_granted(fsm, enabled),
        "perm_name": "motion" if (True if sim else motion_granted(fsm, enabled)) else "readonly",
        "badges": [],
        "secondary": [],
    }
    if sim:
        out["badges"].append({"k": "sim", "label": "仿真数据", "tone": "info"})
        if motion_granted(fsm, enabled):
            out["primary"] = _btn("cancel_ready", "取消就绪", enabled=True,
                                  tone="warn", why="下电并收回使能")
        else:
            out["primary"] = _btn("ready", "一键就绪", enabled=True, tone="ok",
                                  why="仿真状态机直接推到 FSM=33 并使能（不发真机指令）")
        out["secondary"] = _secondary(sim=True, fsm=fsm, enabled=enabled)
        return out

    if not link or not (link.get("stream_up") or link.get("cmd_up")):
        out["primary"] = _btn("connect", "连接控制器", enabled=True, tone="acc",
                              why="连不上就只能看仿真，三维姿态不会是真机数据")
        out["badges"].append({"k": "down", "label": "未连接", "tone": "mute"})
        out["secondary"] = []
        return out

    if not link.get("stream_up"):
        out["primary"] = _btn("link", "等待状态上报", enabled=False, tone="mute",
                              why="还没收到 Data Sheet，状态未知时不发任何指令（盲发有风险）")
        out["badges"].append({"k": "stream", "label": "取流未就绪", "tone": "warn"})
        return out
    if not link.get("cmd_up"):
        out["primary"] = _btn("link", "等待命令通道", enabled=False, tone="mute",
                              why="10003 没连上，只能看不能动")
        out["badges"].append({"k": "cmd", "label": "命令口断开", "tone": "warn"})
        return out

    out["badges"].append({"k": "link", "label": "在线", "tone": "ok"})
    if st.get("rate_hz") is not None and st["rate_hz"] < 10:
        out["badges"].append({"k": "rate", "label": f"取流 {st['rate_hz']}Hz", "tone": "warn"})
    if st.get("free_drive"):
        out["badges"].append({"k": "free", "label": "手拖中", "tone": "warn"})

    if err:
        out["primary"] = _btn("reset", "复位清错", enabled=True, tone="danger",
                              why="有故障码时先把码清掉，否则后续指令一律 20018")
        out["badges"].append({"k": "err", "label": f"故障 {err}", "tone": "bad"})
        out["secondary"] = _secondary(sim=False, fsm=fsm, enabled=enabled)
        return out

    if fsm is None:
        out["primary"] = _btn("link", "等待 FSM 上报", enabled=False, tone="mute",
                              why="状态未知，不盲发")
        return out

    if fsm == 6:
        # 实测：刚下电时 FSM 会走 14→6→7，停在 6 这段时间里 Electrify 回 20018。
        # 所以这一档不给按钮，等它自己走完（后端 _wait_fsm_settle 也是这么做的）。
        out["primary"] = _btn("link", "下电中，等状态落定", enabled=False, tone="mute",
                              why="FSM=6 是下电过渡档，此时任何状态指令都会被拒（实测 20018）")
        out["badges"].append({"k": "settle", "label": "状态过渡中", "tone": "warn"})
        return out

    if fsm < 14:
        out["primary"] = _btn("electrify", "上电", enabled=True, tone="acc",
                              why="FSM=" + str(fsm) + " 未上电，Electrify 只通 48V，抱闸仍吸合")
    elif fsm < 24:
        out["primary"] = _btn("start_master", "启动主控", enabled=True, tone="acc",
                              why="FSM=14 已上电。实测这一步是 StartMaster（ConnectToBox 在此档回 20018）")
    elif fsm < READY_FSM:
        out["primary"] = _btn("ready", "一键就绪", enabled=True, tone="ok",
                              why="FSM=24 初始化完成，GrpEnable 即可进 33；按下即进入运动模式")
    else:
        out["primary"] = _btn("cancel_ready", "取消就绪", enabled=True, tone="warn",
                              why="FSM=" + str(fsm) + " 已就绪。收回顺序：去使能 → 下电")
    out["secondary"] = _secondary(sim=False, fsm=fsm, enabled=enabled)
    return out


def _btn(key, label, **kw):
    return {"key": key, "label": label, "sub": _SUB.get(key, ""),
            "enabled": kw.get("enabled", True), "tone": kw.get("tone", "acc"),
            "why": kw.get("why", "")}


def _secondary(sim, fsm, enabled):
    """主按钮之外的次要动作，收在驱动抽屉里，不进主界面。"""
    out = [{"key": "stop", "label": "停止运动", "cmd": "GrpStop"}]
    if enabled:
        out.append({"key": "disable", "label": "去使能", "cmd": "GrpDisable"})
    if (fsm or 0) >= 14 and not enabled:
        out.append({"key": "blackout", "label": "下电", "cmd": "BlackOut"})
    if not sim and (fsm or 0) >= 24:
        out.append({"key": "connect_box", "label": "连接电箱(手动)", "cmd": "ConnectToBox"})
    return out


def _fsm_hint(fsm):
    try:
        from huayan_panel.core import protocol as P
        return P.FSM_HINT.get(int(fsm), "")
    except Exception:
        return ""
