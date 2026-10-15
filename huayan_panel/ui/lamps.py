"""绿/黄/红三态状态灯。

每条判据都标来源：DOC=协议/手册明确写的；SELF=本软件自设阈值（文档未给）。
未知一律 GRAY 并把数值留空，不猜成绿色。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

GREEN, YELLOW, RED, GRAY = "green", "yellow", "red", "gray"
HEX = {GREEN: "#5fce8b", YELLOW: "#e0a33b", RED: "#e4553f", GRAY: "#4d6563"}
TEXT = {GREEN: "正常", YELLOW: "注意", RED: "异常/禁止", GRAY: "未知"}

DOC = "DOC"
SELF = "SELF"

NOMINAL_DS_HZ = 20.0        # 10004 实测 50ms/帧
NOMINAL_ECAT_HZ = 1000.0    # 实测约 1014，文档未给标称值


class Lamp(QWidget):
    def __init__(self, name, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 4, 6, 4)
        v.setSpacing(2)
        self.dot = QLabel("●")
        self.dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name = QLabel(name)
        self.name.setObjectName("metricDim")
        self.name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail = QLabel("--")
        self.detail.setObjectName("metricDim")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.dot)
        v.addWidget(self.name)
        v.addWidget(self.detail)
        self.set_level(GRAY)

    def set_level(self, level, detail=None):
        self.dot.setStyleSheet(f"color: {HEX[level]}; font-size: 20px;")
        if detail is not None:
            self.detail.setText(detail)


def _link(snap, never):
    if never:
        return GRAY, "尚未连接"
    if snap.stream_up and snap.cmd_up:
        if snap.rate_hz >= NOMINAL_DS_HZ * 0.9:
            return GREEN, f"取流+命令 在线 {snap.rate_hz:.0f}fps"
        return YELLOW, f"在线但帧率低 {snap.rate_hz:.0f}fps（标称 {NOMINAL_DS_HZ:.0f}fps）"
    if snap.stream_up or snap.cmd_up:
        return YELLOW, f"部分在线 取流={int(snap.stream_up)} 命令={int(snap.cmd_up)}"
    return RED, "取流与命令均断开"


def rules(s, perm, never_connected):
    """返回 [(名称, 等级, 说明, 来源), ...]"""
    out = []
    out.append(("链路", _link(s, never_connected)[0], _link(s, never_connected)[1], SELF))

    if s.stream_up:
        if s.rate_hz >= NOMINAL_DS_HZ * 0.9:
            out.append(("Data Sheet 帧率", GREEN, f"{s.rate_hz:.0f} fps（10004 标称 50ms/帧 = 20fps，实测）", DOC))
        elif s.rate_hz > 0:
            out.append(("Data Sheet 帧率", YELLOW, f"{s.rate_hz:.0f} fps，低于标称 {NOMINAL_DS_HZ:.0f}fps", DOC))
        else:
            out.append(("Data Sheet 帧率", RED, "取流已连但 0 帧", DOC))
    else:
        out.append(("Data Sheet 帧率", GRAY, "无数据", DOC))

    fsm = s.robot_state
    if fsm is None:
        out.append(("上电状态", GRAY, "未取到 FSM", DOC))
    elif fsm >= 24:
        out.append(("上电状态", GREEN, f"FSM={fsm} 已上电且控制器已初始化", DOC))
    elif fsm >= 14:
        out.append(("上电状态", YELLOW, f"FSM={fsm} 已上电，尚未初始化完成（需就绪流程）", DOC))
    else:
        out.append(("上电状态", RED, f"FSM={fsm} 未上电（点【上电】或【就绪】）", DOC))

    if s.auto_mode is None:
        out.append(("运行模式", GRAY, "Data Sheet 无 AutoMode 字段", DOC))
    else:
        v = str(s.auto_mode)
        # 2026-10-11 现场对照：示教器手动时 AutoMode=1，切到自动后 AutoMode=0（文档未写死，按实测口径）
        m = {"1": "手动", "0": "自动"}.get(v)
        note = f"AutoMode={v}" + (f"（实测口径：{m}模式）" if m else "（取值未知）")
        if v == "0":
            note += "；自动模式下示教器程序可能接管运动，此时只应做现场授权内的动作"
        out.append(("运行模式", YELLOW if v == "0" else GREEN, note, SELF))

    if s.enabled is None:
        out.append(("使能", GRAY, "未取到", DOC))
    elif s.enabled:
        out.append(("使能", GREEN, "已使能，电机待命", DOC))
    elif fsm is not None and fsm >= 14:
        out.append(("使能", YELLOW, "未使能（已上电，抱闸吸合，不可运动）", DOC))
    else:
        out.append(("使能", RED, "未使能且未上电", DOC))

    codes = [c for c in (s.axis_err or []) if c]
    if not s.stream_up:
        out.append(("报错", GRAY, "无数据", DOC))
    elif s.err_code or codes:
        out.append(("报错", RED, f"Error_Code={s.err_code} 轴={s.err_axis} 轴错误码={s.axis_err}"
                                 f"（清错后才可发下一条运动，协议 p275）", DOC))
    elif s.reduce_mode or s.free_drive:
        out.append(("报错", YELLOW, f"无错误码，但缩减模式={s.reduce_mode} 零力示教={s.free_drive}", DOC))
    else:
        out.append(("报错", GREEN, "Error_Code=0，六轴无错误", DOC))

    if s.brake:
        held = sum(1 for b in s.brake if b == 0)
        if held == len(s.brake):
            out.append(("抱闸", GREEN if not s.enabled else YELLOW,
                        "六轴抱闸全部吸合（0=抱闸，Data Sheet p272 口径；SDK p42 极性相反，冲突未决）", DOC))
        elif held == 0:
            lvl = YELLOW if s.enabled else RED
            out.append(("抱闸", lvl, "六轴全部松闸" + ("" if s.enabled else " 且未使能 —— 有掉轴风险（协议 p93）"), DOC))
        else:
            out.append(("抱闸", RED, f"部分松闸：吸合 {held}/{len(s.brake)}", DOC))
    else:
        out.append(("抱闸", GRAY, "无数据", DOC))

    if s.moving is None:
        out.append(("运动", GRAY, "无数据", DOC))
    elif s.moving:
        out.append(("运动", YELLOW, f"运动中（到位标志={s.inpos}）", DOC))
    elif s.inpos:
        out.append(("运动", GREEN, "静止且到位", DOC))
    else:
        out.append(("运动", YELLOW, "静止未到位", DOC))

    if s.override is None:
        out.append(("速度比", GRAY, "无数据", DOC))
    elif s.override <= 0.10:
        out.append(("速度比", GREEN, f"{s.override * 100:.1f}%（≤10%，手动可接受）", SELF))
    elif s.override <= 0.30:
        out.append(("速度比", YELLOW, f"{s.override * 100:.1f}%（缩减模式默认上限 30%，手册 p25）", DOC))
    else:
        out.append(("速度比", RED, f"{s.override * 100:.1f}%，超过缩减上限，请确认安全等级", SELF))

    hot = max(s.temps) if s.temps else None
    if hot is None:
        out.append(("关节温度", GRAY, "无数据", DOC))
    elif hot < 60:
        out.append(("关节温度", GREEN, f"最高 {hot:.1f}℃", SELF))
    elif hot < 75:
        out.append(("关节温度", YELLOW, f"最高 {hot:.1f}℃", SELF))
    else:
        out.append(("关节温度", RED, f"最高 {hot:.1f}℃，阈值 75℃ 为本软件自设", SELF))

    if s.volt48_in is None:
        out.append(("48V 供电", GRAY, "无数据", DOC))
    elif 45.6 <= s.volt48_in <= 50.4:
        out.append(("48V 供电", GREEN, f"{s.volt48_in:.2f}V（±5% 内，阈值自设）", SELF))
    elif 43.0 <= s.volt48_in <= 52.8:
        out.append(("48V 供电", YELLOW, f"{s.volt48_in:.2f}V 偏离 ±5%", SELF))
    else:
        out.append(("48V 供电", RED, f"{s.volt48_in:.2f}V 超出 ±10%", SELF))

    if not s.stream_up:
        out.append(("EtherCAT", GRAY, "无数据", DOC))
    elif (s.ecat_lost or 0) or (s.ecat_tx_err or 0) or (s.ecat_rx_err or 0):
        out.append(("EtherCAT", YELLOW,
                    f"累计丢帧={s.ecat_lost} Tx={s.ecat_tx_err} Rx={s.ecat_rx_err}（字段名为 Total*，"
                    f"按开机以来累计处理，不代表当前正在丢；文档未给告警阈值）", SELF))
    elif s.ecat_fps:
        out.append(("EtherCAT", GREEN, f"{s.ecat_fps} fps，无丢帧", DOC))
    else:
        out.append(("EtherCAT", YELLOW, "从站帧率 0（总线未运行）", DOC))

    red_now = any(x[1] == RED for x in out)
    if perm.name == "MOTION" and red_now:
        out.append(("联锁提示", YELLOW, "存在红色项但运动许可仍开启，请手动降级", SELF))
    return out


class LampBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.h = QHBoxLayout(self)
        self.h.setContentsMargins(0, 0, 0, 0)
        self.h.setSpacing(4)
        self.lamps = {}
        self.stretch = self.h.addStretch(1)

    def sync(self, specs):
        for name, level, detail, src in specs:
            if name not in self.lamps:
                w = Lamp(f"{name} · {src}")
                self.lamps[name] = w
                self.h.insertWidget(self.h.count() - 1, w)
            self.lamps[name].set_level(level, detail)

    def clear_to_gray(self):
        for w in self.lamps.values():
            w.set_level(GRAY, "--")
