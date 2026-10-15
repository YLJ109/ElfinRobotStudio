"""主窗口：连接栏 + 电源与状态 + 六轴手动控制 + 指令审计日志。"""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QPlainTextEdit, QPushButton, QRadioButton, QSlider,
    QStatusBar, QVBoxLayout, QWidget,
)

from ..core import protocol as P
from ..core.robot import Link, Perm
from . import theme as T
from .lamps import LampBar, rules

AXIS_RANGE_TXT = [f"[{lo:g}, {hi:g}]" for lo, hi in P.JOINT_LIMITS]


def lbl(text, obj=""):
    w = QLabel(text)
    if obj:
        w.setObjectName(obj)
    return w


class ArmDialog(QDialog):
    """运动许可：四项全勾才放行；每次启动都需重新确认，不做记忆。"""

    TEXTS = (
        "工作半径与机械臂运动包络内无人、无障碍物、无电缆干涉",
        "物理急停按钮触手可及，且已知手动模式下急停响应为 Stop0 不受控停止（手册 p183）",
        "速度比确认在 10% 以下（本软件进入运动许可时自动钳制）",
        "已知悉关节限位取自手册 p54 截图、与本机型(E05-1195_Pro)是否一致尚未核对",
    )

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("运动许可确认 / ARM MOTION")
        self.setModal(True)
        v = QVBoxLayout(self)
        v.addWidget(lbl("开启运动许可前，逐项确认：", "sub"))
        self.cbs = []
        for t in self.TEXTS:
            c = QCheckBox(t)
            self.cbs.append(c)
            v.addWidget(c)
        row = QHBoxLayout()
        self.b_ok = QPushButton("确认并开启运动许可")
        b_no = QPushButton("取消")
        self.b_ok.setProperty("role", "danger")
        self.b_ok.setEnabled(False)
        row.addWidget(self.b_ok)
        row.addWidget(b_no)
        v.addLayout(row)
        for c in self.cbs:
            c.stateChanged.connect(self._sync)
        self.b_ok.clicked.connect(self.accept)
        b_no.clicked.connect(self.reject)

    def _sync(self, _):
        self.b_ok.setEnabled(all(c.isChecked() for c in self.cbs))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("华沿 Elfin V6 手动控制台 / Huayan Manual Control Panel")
        self.link = Link(self)
        self.rows6 = []
        self.pbtns = []
        self.hold_timer = QTimer(self)
        self.hold_timer.setSingleShot(True)
        self.hold_timer.timeout.connect(self._hold_fired)
        self.pressing = None
        self._held = False
        self.step_deg = 1.0
        self.never_connected = True
        self._build()
        self.link.tick.connect(self.refresh)
        self.link.log.connect(self.log)
        self.link.busy.connect(lambda b: self.statusBar().showMessage("指令在途…" if b else ""))
        self.refresh()
        QShortcut(QKeySequence("Esc"), self, activated=self.link.emergency_stop)
        for i in range(6):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda a=i: self._pick(a))

    # ------------------------------------------------------------------ 布局
    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 12, 14, 8)
        outer.addWidget(self._top_bar())
        self.lampbar = LampBar()
        outer.addWidget(self.lampbar)
        mid = QHBoxLayout()
        mid.setSpacing(12)
        mid.addWidget(self._power_box(), 4)
        mid.addWidget(self._jog_box(), 6)
        outer.addLayout(mid, 5)
        bot = QHBoxLayout()
        bot.setSpacing(12)
        bot.addWidget(self._io_box(), 4)
        bot.addWidget(self._log_box(), 5)
        outer.addLayout(bot, 3)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("未连接：所有读数留空，不显示假设值")

    def _top_bar(self):
        box = QGroupBox("链路 / LINK")
        h = QHBoxLayout(box)
        h.addWidget(lbl("IP"))
        self.ip = QLineEdit("192.168.0.10")
        self.ip.setFixedWidth(128)
        h.addWidget(self.ip)
        self.b_conn = QPushButton("连接")
        self.b_disc = QPushButton("断开")
        self.b_disc.setEnabled(False)
        h.addWidget(self.b_conn)
        h.addWidget(self.b_disc)
        self.ind = lbl("● 离线", "danger")
        h.addWidget(self.ind)
        h.addSpacing(8)
        for key, cap in (("model", "型号"), ("version", "版本"), ("sim", "性质"), ("fsm", "FSM")):
            h.addWidget(lbl(cap + " ", "metricDim"))
            v = lbl("--", "metric")
            setattr(self, "v_" + key, v)
            h.addWidget(v)
        h.addStretch(1)
        h.addWidget(lbl("许可 ", "metricDim"))
        self.b_p = {}
        for lvl, cap in ((Perm.READONLY, "只读"), (Perm.POWER, "电源"), (Perm.MOTION, "运动")):
            b = QPushButton(cap)
            b.setCheckable(True)
            b.setProperty("role", "arm" if lvl == Perm.MOTION else "")
            b.clicked.connect(lambda _, l=lvl: self._perm_click(l))
            self.b_p[lvl] = b
            h.addWidget(b)
        self.b_p[Perm.READONLY].setChecked(True)
        self.b_conn.clicked.connect(self._connect)
        self.b_disc.clicked.connect(self._disconnect)
        return box

    def _power_box(self):
        box = QGroupBox("电源与状态 / POWER & STATE")
        g = QGridLayout(box)
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(5)
        rows = [
            ("就绪（上电+初始化）", self._ready, Perm.POWER,
             "按 SDK p386 顺序：Electrify→FSM14→ConnectToBox→FSM24。不含使能。"),
            ("使能 GrpEnable", lambda: self.link.ask("GrpEnable", 0, need=Perm.MOTION, why="使能"), Perm.MOTION,
             "使能后电机待命；松闸状态下受重力可能掉轴（协议 p93）"),
            ("去使能 GrpDisable", lambda: self.link.ask("GrpDisable", 0, need=Perm.MOTION, why="去使能"), Perm.MOTION,
             "去使能后靠抱闸悬停"),
            ("上电 Electrify", lambda: self.link.ask("Electrify", need=Perm.POWER, why="上电"), Perm.POWER,
             "只接通 48V，关节抱闸仍未解除，不能运动（手册 p15）"),
            ("下电 BlackOut", lambda: self.link.ask("BlackOut", need=Perm.POWER, why="下电"), Perm.POWER,
             "切断本体供电；恢复需现场操作，不可远程挽回"),
            ("清错 GrpReset", lambda: self.link.ask("GrpReset", 0, need=Perm.POWER, why="清错"), Perm.POWER,
             "报错后必须先清错，才能下发下一条运动指令（协议 p275）"),
            ("回Standby XToStandby", lambda: self.link.ask("XToStandby", 0, need=Perm.MOTION, why="回Standby"),
             Perm.MOTION, "外接急停/安全光幕条件未解除时无法带回（协议 p39）"),
            ("停止 GrpStop", lambda: self.link.ask("GrpStop", 0, need=Perm.POWER, why="停止"), Perm.POWER, ""),
            ("暂停 GrpInterrupt", lambda: self.link.ask("GrpInterrupt", 0, need=Perm.POWER, why="暂停"), Perm.POWER, ""),
            ("继续 GrpContinue", lambda: self.link.ask("GrpContinue", 0, need=Perm.POWER, why="继续"), Perm.POWER, ""),
            ("软急停 进入", lambda: self.link.ask("EnterSafeGuard", 0, 1, need=Perm.MOTION, why="软急停进入"),
             Perm.MOTION, "退出软急停后仍需有人在界面点「光幕恢复」，上位机无法单方面恢复（协议 p38 注1）"),
            ("软急停 退出", lambda: self.link.ask("EnterSafeGuard", 0, 0, need=Perm.MOTION, why="软急停退出"),
             Perm.MOTION, ""),
        ]
        for i, (cap, act, need, tip) in enumerate(rows):
            b = QPushButton(cap)
            b.setToolTip(f"{tip}\n需要许可：{need.name}" if tip else f"需要许可：{need.name}")
            b.setEnabled(False)
            if need == Perm.MOTION:
                b.setProperty("role", "arm")
            if cap.startswith("下电") or cap.startswith("软急停 进入"):
                b.setProperty("role", "danger")
            b.clicked.connect(act)
            g.addWidget(b, i // 2, (i % 2) * 2, 1, 2)
            self.pbtns.append((b, need))

        r = QHBoxLayout()
        r.addWidget(lbl("速度比"))
        self.ovr = QDoubleSpinBox()
        self.ovr.setRange(1.0, 100.0)
        self.ovr.setDecimals(0)
        self.ovr.setValue(1.0)
        self.ovr.setSuffix(" %")
        r.addWidget(self.ovr)
        b = QPushButton("应用 SetOverride")
        b.setEnabled(False)
        b.clicked.connect(lambda: self.link.set_override(self.ovr.value() / 100.0))
        r.addWidget(b)
        self.pbtns.append((b, Perm.MOTION))
        r.addStretch(1)
        g.addLayout(r, 7, 0, 1, 2)

        self.readout = QPlainTextEdit()
        self.readout.setReadOnly(True)
        g.addWidget(self.readout, 8, 0, 1, 2)
        b_web = QPushButton("打开网页示教器 http://IP/dist")
        b_web.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(f"http://{self.ip.text().strip()}/dist")))
        g.addWidget(b_web, 9, 0, 1, 2)
        return box

    def _jog_box(self):
        box = QGroupBox("六轴手动控制 / JOG & STEP")
        v = QVBoxLayout(box)

        s = QHBoxLayout()
        s.addWidget(lbl("步长", "metricDim"))
        for val in P.STEP_CHOICES:
            rb = QRadioButton(f"{val:g}°")
            rb.setChecked(val == 1.0)
            rb.toggled.connect(lambda on, x=val: on and self._set_step(x))
            s.addWidget(rb)
        self.step_custom = QDoubleSpinBox()
        self.step_custom.setRange(P.LIMITS["dDistance"][0], P.LIMITS["dDistance"][1])
        self.step_custom.setDecimals(3)
        self.step_custom.setSuffix(" °")
        self.step_custom.valueChanged.connect(self._set_step)
        s.addWidget(self.step_custom)
        s.addStretch(1)
        s.addWidget(lbl("单击 −/+ = MoveRelJ 精确步进；按住 = LongJogJ + LongMoveEvent 心跳连动", "metricDim"))
        v.addLayout(s)

        g = QGridLayout()
        g.setHorizontalSpacing(8)
        for c, cap in enumerate(["轴", "当前角°", "负", "相对点动量（松手生效 ±10°）", "正", "目标°", "温度°C", "软限位"]):
            g.addWidget(lbl(cap, "metricDim"), 0, c)
        for i in range(6):
            row = i + 1
            g.addWidget(lbl(f"J{i + 1}", "metric"), row, 0)
            cur = lbl("--", "metric")
            g.addWidget(cur, row, 1)
            btns = {}
            for key, d, cap in (("neg", 0, "−"), ("pos", 1, "+")):
                b = QPushButton(cap)
                b.setFixedWidth(42)
                b.setEnabled(False)
                b.pressed.connect(lambda a=i, dd=d, bb=b: self._press(a, dd))
                b.released.connect(lambda: self._release())
                btns[key] = b
                g.addWidget(b, row, 2 if d == 0 else 4, Qt.AlignmentFlag.AlignLeft)
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(-100, 100)
            sl.setValue(0)
            sl.setEnabled(False)
            sl.setToolTip("相对点动量：0.1° 一格，拖动预览目标角，松手发一次 MoveRelJ，随后自动归零")
            sl.sliderMoved.connect(lambda val, a=i: self._slider_preview(a, val))
            sl.sliderReleased.connect(lambda a=i, w=sl: self._slider_commit(a, w))
            g.addWidget(sl, row, 3)
            tgt = lbl("--", "metricDim")
            g.addWidget(tgt, row, 5, Qt.AlignmentFlag.AlignRight)
            temp = lbl("--", "metricDim")
            g.addWidget(temp, row, 6)
            g.addWidget(lbl(f"{AXIS_RANGE_TXT[i]}", "metricDim"), row, 7)
            self.rows6.append({"cur": cur, "neg": btns["neg"], "pos": btns["pos"],
                               "slider": sl, "target": tgt, "temp": temp, "value": None})
        g.setColumnStretch(3, 5)
        v.addLayout(g)

        b_estop = QPushButton("■  急 停 GrpStop  (Esc)")
        b_estop.setProperty("role", "estop")
        b_estop.clicked.connect(self.link.emergency_stop)
        v.addWidget(b_estop)
        v.addWidget(lbl("急停不受许可等级限制，任何时候可点；但它不替代物理急停按钮。"
                        "手册 p183：手动模式下急停走 Stop0 不受控停止。", "warn"))
        return box

    def _io_box(self):
        """末端 3 路 DO / 3 路 DI / 4 路按键 / 2 路模拟量 + 电箱 IO。

        吸气/放气就是某一路 EndDO 的通断，但哪一路对应哪个阀由现场接线决定，
        本软件不预设映射，只提供逐路置 1 / 置 0 并回读 DI 反馈。
        """
        box = QGroupBox("末端 / 电箱 IO · 吸气·放气·按钮·模拟量")
        v = QVBoxLayout(box)
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        self.io = {"enddo": [], "endi": [], "btn": [], "ai": []}

        g.addWidget(lbl("末端输出 EndDO（协议 p51，nBit 只有 0~2）", "metricDim"), 0, 0, 1, 5)
        for i in range(3):
            r = 1 + i
            g.addWidget(lbl(f"DO{i + 1}"), r, 0)
            cur = lbl("--", "metric")
            g.addWidget(cur, r, 1)
            b_on = QPushButton("置1/吸")
            b_off = QPushButton("置0/停")
            b_on.clicked.connect(lambda _, b=i, val=1: self._io_write(b, val))
            b_off.clicked.connect(lambda _, b=i, val=0: self._io_write(b, val))
            g.addWidget(b_on, r, 2)
            g.addWidget(b_off, r, 3)
            self.io["enddo"].append({"cur": cur, "btns": (b_on, b_off)})

        g.addWidget(lbl("末端输入 EndDI / 按键（只读）", "metricDim"), 0, 6, 1, 3)
        for i in range(3):
            g.addWidget(lbl(f"DI{i + 1}"), 1 + i, 6)
            cur = lbl("--", "metric")
            g.addWidget(cur, 1 + i, 7)
            self.io["endi"].append(cur)

        for i in range(4):
            g.addWidget(lbl(f"BTN{i + 1}"), i, 9)
            cur = lbl("--", "metric")
            g.addWidget(cur, i, 10)
            self.io["btn"].append(cur)

        g.addWidget(lbl("末端模拟量 EndAI（V，只读）", "metricDim"), 4, 0, 1, 3)
        for i in range(2):
            g.addWidget(lbl(f"AI{i + 1}"), 5 + i, 0)
            cur = lbl("--", "metric")
            g.addWidget(cur, 5 + i, 1)
            self.io["ai"].append(cur)

        self.box_io = lbl("电箱 IO --", "metricDim")
        self.box_io.setWordWrap(True)
        g.addWidget(self.box_io, 5, 2, 2, 9)

        b_read = QPushButton("用 Read* 指令即时复核一次")
        b_read.clicked.connect(self._io_refresh_read)
        g.addWidget(b_read, 1, 4, 3, 1)
        self.pbtns.append((b_read, Perm.READONLY))
        v.addLayout(g)
        return box

    def _io_refresh_read(self):
        """Data Sheet 是 20Hz 推送；这里再用同步 Read* 复核一次，两者对不上就是有问题。"""
        self.link.ask("ReadEO", 0, 0, 1, 2, why="ReadEO 复核")
        self.link.ask("ReadEI", 0, 0, 1, 2, why="ReadEI 复核")
        self.link.ask("ReadBoxDO", *range(8), why="ReadBoxDO 复核")
        self.link.ask("ReadBoxDI", *range(8), why="ReadBoxDI 复核")

    def _io_write(self, bit, val):
        if not self.link.snap.cmd_up:
            self.log("interlock", "已拒绝 IO 写入：命令通道未连接")
            return
        self.link.ask("SetEndDO", 0, bit, val, need=Perm.MOTION,
                      why=f"EndDO{bit + 1}={'通' if val else '断'}")

    def _refresh_io(self, s):
        def bits(lst, n):
            return [lst[i] if i < len(lst) else None for i in range(n)]
        for i, cur in zip(range(3), self.io["enddo"]):
            v = bits(s.end_do, 3)[i]
            cur["cur"].setText("--" if v is None else str(v))
        for i, w in enumerate(self.io["endi"]):
            v = bits(s.end_di, 3)[i]
            w.setText("--" if v is None else str(v))
        for i, w in enumerate(self.io["btn"]):
            v = bits(s.end_button, 4)[i]
            w.setText("--" if v is None else str(v))
        for i, w in enumerate(self.io["ai"]):
            v = s.end_ai[i] if i < len(s.end_ai) else None
            w.setText("--" if v is None else f"{v:.3f}")
        def arr(lst, n):
            return "[" + ",".join(str(x) for x in lst[:n]) + "]" if lst else "--"
        self.box_io.setText(
            f"电箱：BoxDO={arr(s.box_do, 8)}  BoxDI={arr(s.box_di, 8)}  "
            f"BoxCI={arr(s.box_ci, 8)}  BoxCO={arr(s.box_co, 8)}  "
            f"Conveyor={s.conveyor if s.conveyor is not None else '--'}  "
            f"Encode={s.encode if s.encode is not None else '--'}  "
            f"末端按键使能 EnableEndBTN={s.enable_end_btn if s.enable_end_btn is not None else '--'}")

    def _log_box(self):
        box = QGroupBox("指令审计日志 / COMMAND AUDIT")
        v = QVBoxLayout(box)
        self.logview = QPlainTextEdit()
        self.logview.setReadOnly(True)
        v.addWidget(self.logview)
        return box

    # ------------------------------------------------------------------ 许可/连接
    def _set_step(self, val):
        self.step_deg = float(val)

    def _perm_click(self, lvl):
        if lvl == Perm.MOTION:
            if self.link.perm == Perm.MOTION:
                self._apply_perm(Perm.POWER)
                return
            if ArmDialog(self).exec() == QDialog.DialogCode.Accepted:
                if self.ovr.value() > 10:
                    self.ovr.setValue(10.0)
                    self.log("interlock", "进入运动许可：速度比已自动钳制到 10%")
                self._apply_perm(Perm.MOTION)
            else:
                self._sync_perm_buttons()
                return
        else:
            self._apply_perm(lvl)

    def _apply_perm(self, lvl):
        self.link.grant(lvl)
        self._sync_perm_buttons()
        self._enable_widgets()

    def _sync_perm_buttons(self):
        for l, b in self.b_p.items():
            b.setChecked(l == self.link.perm)

    def _enable_widgets(self):
        up = self.link.snap.cmd_up
        for b, need in self.pbtns:
            b.setEnabled(bool(up and self.link.perm >= need))
        for r in self.rows6:
            for k in ("neg", "pos", "slider"):
                r[k].setEnabled(bool(up and self.link.perm >= Perm.MOTION))
        can_io = bool(up and self.link.perm >= Perm.MOTION)
        for row in self.io["enddo"]:
            for b in row["btns"]:
                b.setEnabled(can_io)

    def _connect(self):
        host = self.ip.text().strip()
        if not host:
            self.log("interlock", "IP 为空，未连接")
            return
        self.link.connect_to(host)     # 连上后 Link 自己会发 4 条只读自报指令
        self.never_connected = False
        self.b_conn.setEnabled(False)
        self.b_disc.setEnabled(True)

    def _disconnect(self):
        self.link.disconnect()
        self.link.grant(Perm.READONLY)
        self.b_conn.setEnabled(True)
        self.b_disc.setEnabled(False)
        self._sync_perm_buttons()
        self.lampbar.clear_to_gray()
        self.refresh()

    def _ready(self):
        self.link.begin_ready(then_enable=False)

    # ------------------------------------------------------------------ 点动
    def _press(self, axis, direction):
        self.pressing = (axis, direction)
        self._held = False
        self.hold_timer.start(300)

    def _hold_fired(self):
        if self.pressing:
            self._held = True
            self.link.jog_hold(self.pressing[0], self.pressing[1], True)

    def _release(self):
        self.hold_timer.stop()
        if self._held and self.pressing:
            self.link.jog_hold(self.pressing[0], self.pressing[1], False)
        elif self.pressing:
            axis, direction = self.pressing
            self.link.jog_step(axis, direction, self.step_deg)
        self.pressing = None
        self._held = False

    def _pick(self, a):
        self.statusBar().showMessage(f"焦点轴 J{a + 1}（−/+ 步进，Esc 停止）")

    def _slider_preview(self, axis, val):
        cur = self.rows6[axis]["value"]
        if cur is None:
            self.rows6[axis]["target"].setText("无当前角")
            return
        self.rows6[axis]["target"].setText(f"{cur + val / 10.0:+.3f}°")

    def _slider_commit(self, axis, sl):
        val = sl.value()
        sl.blockSignals(True)
        sl.setValue(0)
        sl.blockSignals(False)
        self.rows6[axis]["target"].setText("--")
        cur = self.rows6[axis]["value"]
        if not val:
            return
        if cur is None:
            self.log("interlock", f"已拒绝滑动条点动：未取到 J{axis + 1} 当前角，不用假设值")
            return
        self.link.jog_step(axis, 1 if val > 0 else 0, abs(val) / 10.0)

    # ------------------------------------------------------------------ 显示
    def log(self, kind, msg):
        self.logview.appendPlainText(f"{time.strftime('%H:%M:%S')} [{kind}] {msg}")

    def refresh(self):
        s = self.link.snap
        self._refresh_io(s)
        self.lampbar.sync(rules(s, self.link.perm, self.never_connected))
        both = s.stream_up and s.cmd_up
        if both:
            ind_txt, ind_col = "● 在线", T.OK
        elif s.stream_up or s.cmd_up:
            ind_txt, ind_col = f"● 部分在线 取流={int(s.stream_up)} 命令={int(s.cmd_up)}", T.WARN
        else:
            ind_txt, ind_col = "● 离线", T.DANGER
        self.ind.setText(ind_txt)
        self.ind.setStyleSheet(f"color:{ind_col};font-weight:600")
        self._enable_widgets()
        self.v_model.setText(s.model or "--")
        self.v_version.setText(s.version or "--")
        self.v_sim.setText(s.is_sim or "--")
        self.v_sim.setStyleSheet(
            f"color:{T.DANGER if s.is_sim == '仿真' else T.OK};font-weight:600")
        title = "华沿 Elfin V6 手动控制台 / Huayan Manual Control Panel"
        self.setWindowTitle("【仿真数据 · 非真机】" + title if s.is_sim == "仿真" else title)
        fsm = s.robot_state
        self.v_fsm.setText("--" if fsm is None else str(fsm))

        for r, i in zip(self.rows6, range(6)):
            v = s.joints[i]
            r["value"] = v
            r["cur"].setText("--" if v is None else f"{v:.3f}")
            r["temp"].setText("--" if i >= len(s.temps) else f"{s.temps[i]:.1f}")

        def txt(x, fmt="{}"):
            return "--" if x is None else fmt.format(x)

        def yes(v):
            return "--" if v is None else ("是" if v else "否")

        base = "--" if s.pcs_base[0] is None else " ".join(f"{x:.2f}" for x in s.pcs_base)
        self.readout.setPlainText(
            "状态机 FSM       " + txt(fsm) + "  " + P.FSM_HINT.get(fsm, "未在文档状态机表中(表缺失)") + "\n"
            "使能 / 运动 / 到位 " + f"{yes(s.enabled)} / {yes(s.moving)} / {yes(s.inpos)}\n"
            "速度比            " + ("--" if s.override is None else f"{s.override * 100:.1f}%") + "\n"
            "错误 轴/码        " + txt(s.err_axis) + " / " + txt(s.err_code) + "\n"
            "抱闸 BrakeState   " + (str(s.brake) if s.brake else "--") +
            "   （0=抱闸，Data Sheet p272；SDK p42 相反，文档冲突未决）\n"
            "48V 输入          " + ("--" if s.volt48_in is None else f"{s.volt48_in:.2f} V") + "\n"
            "EtherCAT fps/丢帧 " + txt(s.ecat_fps) + " / " + txt(s.ecat_lost) + "\n"
            "帧率/控制器时间   " + f"{s.rate_hz:.1f} fps / " + (s.update_time or "--") + "\n"
            "基座坐标 XYZ°     " + base)
