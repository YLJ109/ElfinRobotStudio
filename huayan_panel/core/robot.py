"""连接层：10004 取流线程 + 10003 命令线程（严格串行）+ 安全联锁门面。

UI 只读 Snapshot、只调 Link 的方法，不碰 socket。
"""

from __future__ import annotations

import queue
import socket
import time
from dataclasses import dataclass, field
from enum import IntEnum

from PyQt6.QtCore import QObject, QTimer, QThread, pyqtSignal

from . import protocol as P


class Perm(IntEnum):
    READONLY = 0   # 只读取流，控制按钮全禁用
    POWER = 1      # 电源 / 清错 / 停止
    MOTION = 2     # 使能 + 运动


@dataclass
class Snapshot:
    stream_up: bool = False
    cmd_up: bool = False
    joints: list = field(default_factory=lambda: [None] * 6)
    pcs_base: list = field(default_factory=lambda: [None] * 6)
    override: float | None = None
    robot_state: int | None = None
    enabled: int | None = None
    moving: int | None = None
    inpos: int | None = None
    err_axis: int | None = None
    err_code: int | None = None
    brake: list = field(default_factory=list)
    axis_err: list = field(default_factory=list)
    reduce_mode: int | None = None
    free_drive: int | None = None
    auto_mode: str | None = None
    paused: int | None = None
    script_err: str = ""
    n_reset_safe: list = field(default_factory=list)
    end_di: list = field(default_factory=list)
    end_do: list = field(default_factory=list)
    end_button: list = field(default_factory=list)
    enable_end_btn: int | None = None
    end_ai: list = field(default_factory=list)
    box_di: list = field(default_factory=list)
    box_do: list = field(default_factory=list)
    box_ci: list = field(default_factory=list)
    box_co: list = field(default_factory=list)
    conveyor: str | None = None
    encode: int | None = None
    temps: list = field(default_factory=list)
    currents: list = field(default_factory=list)
    volt48_in: float | None = None
    volt48_out: float | None = None
    ecat_fps: int | None = None
    ecat_lost: int | None = None
    ecat_tx_err: int | None = None
    ecat_rx_err: int | None = None
    update_time: str = ""
    rate_hz: float = 0.0
    model: str = ""
    version: str = ""
    is_sim: str = ""
    fsm: int | None = None


@dataclass
class Reply:
    sent: str
    name: str
    ok: bool
    fields: list
    raw: str = ""
    err_code: int | None = None
    rtt_ms: float = 0.0
    timeout: bool = False


class StreamThread(QThread):
    """只读 10004，永不发送。"""

    frame = pyqtSignal(dict)
    up = pyqtSignal(bool, str)

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        buf = b""
        stamps = []
        sock = None
        silent = False
        while not self._stop:
            try:
                if sock is None:
                    sock = socket.create_connection((self.host, P.PORT_STREAM_JSON[0]), timeout=3)
                    sock.settimeout(1.0)
                    buf, stamps, silent = b"", [], False
                    self.up.emit(True, f"取流已连接 {self.host}:{P.PORT_STREAM_JSON[0]}")
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    if not silent:
                        silent = True
                        self.up.emit(True, "取流静默：超过 1s 没有 Data Sheet 帧")
                    continue
                if not chunk:
                    raise ConnectionError("控制器关闭了取流连接")
                silent = False
                buf += chunk
                payloads, buf, _ = P.split_frames(buf)
                now = time.monotonic()
                for pl in payloads:
                    try:
                        d = P.decode_state(pl)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    stamps = [t for t in stamps + [now] if now - t < 2.0]
                    d["_rate_hz"] = round(len(stamps) / 2.0, 1)
                    self.frame.emit(d)
            except (OSError, ConnectionError) as e:
                self.up.emit(False, f"取流断开：{e}")
                if sock:
                    try:
                        sock.close()
                    except OSError:
                        pass
                sock = None
                time.sleep(1.5)
            except Exception as e:
                # QThread.run 里的未捕获异常会直接终止整个进程，必须落成日志
                self.up.emit(False, f"取流线程异常 {type(e).__name__}: {e}")
                time.sleep(1.5)


class CmdThread(QThread):
    """唯一发送出口：一次一条，等回执再发下一条（协议 p12：多条只处理第一条）。"""

    reply = pyqtSignal(Reply)
    jog = pyqtSignal(str)
    up = pyqtSignal(bool, str)

    HEARTBEAT_S = 0.15        # 协议 p215：LongMoveEvent 周期不得超过 200ms
    REPLY_TIMEOUT_S = 3.0
    HOLD_MAX_S = 2.0          # 单次按住上限，本软件自设

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self._stop = False
        self.q = queue.PriorityQueue()
        self.jog_state = None      # (axis, direction, rbtid)；不能叫 self.jog，会覆盖同名信号
        self.jog_until = 0.0
        self.raw = b""
        self.ascii = b""
        self._seq = 0

    def stop(self):
        self._stop = True

    def submit(self, text, name, urgent=False):
        self._seq += 1
        # 优先级 0=紧急（急停/停点动），1=普通
        self.q.put((0 if urgent else 1, self._seq, text, name))

    def begin_jog(self, axis, direction, rbtid=0):
        self.jog_state = (axis, direction, rbtid)
        self.jog_until = time.monotonic() + self.HOLD_MAX_S
        self.jog.emit(f"长点动 J{axis + 1} {'正' if direction == 1 else '负'} 方向，心跳 "
                      f"{self.HEARTBEAT_S * 1000:.0f}ms，最长 {self.HOLD_MAX_S:.0f}s")

    def end_jog(self):
        j = self.jog_state
        self.jog_state = None
        if j:
            self.submit(P.encode("LongJogJ", j[2], j[0], j[1], 0).decode(), "LongJogJ", urgent=True)
            self.jog.emit("已释放长点动（发送 LongJogJ nState=0）")

    def _roundtrip(self, sock, text, name):
        """发一条并等它的回执。raw=socket 原始缓冲，ascii=已剥离帧后的文本缓冲。"""
        t0 = time.monotonic()
        sock.sendall(text.encode("ascii"))
        deadline = t0 + self.REPLY_TIMEOUT_S
        while time.monotonic() < deadline:
            payloads, self.raw, extracted = P.split_frames(self.raw)
            self.ascii += extracted
            r = P.parse_reply(self.ascii)
            if r:
                rname, rok, rfields, self.ascii = r
                code = None
                if not rok:
                    try:
                        code = int(rfields[0])
                    except (ValueError, IndexError):
                        code = None
                raw = f"{rname},{'OK' if rok else 'Fail'}" + ("," + ",".join(rfields) if rfields else "")
                rep = Reply(text, rname, rok, rfields, raw, code, (time.monotonic() - t0) * 1000)
                if rep.name != name:
                    self.reply.emit(Reply(text, rep.name, rep.ok, rep.fields, "未匹配的迟到回执",
                                          rep.err_code, rep.rtt_ms))
                    continue
                return rep
            try:
                sock.settimeout(min(0.2, max(0.01, deadline - time.monotonic())))
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                raise ConnectionError("命令连接被控制器关闭")
            self.raw += chunk
        return Reply(text, name, False, [], "", None, (time.monotonic() - t0) * 1000, timeout=True)

    def run(self):
        sock = None
        self.raw, self.ascii = b"", b""
        last_hb = time.monotonic()
        while not self._stop:
            try:
                if sock is None:
                    sock = socket.create_connection((self.host, P.PORT_CMD), timeout=3)
                    sock.settimeout(0.2)
                    self.raw, self.ascii = b"", b""
                    self.up.emit(True, f"命令通道已连接 {self.host}:{P.PORT_CMD}")

                # 1) 心跳优先：超过 200ms 没发 LongMoveEvent，控制器会自动停止点动
                if self.jog_state and time.monotonic() - last_hb >= self.HEARTBEAT_S:
                    last_hb = time.monotonic()
                    r = self._roundtrip(sock, P.encode("LongMoveEvent", self.jog_state[2]).decode(),
                                        "LongMoveEvent")
                    if not r.ok:
                        self._kill_jog(sock, f"心跳异常：{'回执超时' if r.timeout else 'Fail ' + str(r.err_code)}")
                        continue

                # 2) 队列指令
                try:
                    _prio, _seq, text, name = self.q.get(timeout=0.02)
                except queue.Empty:
                    pass
                else:
                    self.reply.emit(self._roundtrip(sock, text, name))

                # 3) 按住超时
                if self.jog_state and time.monotonic() > self.jog_until:
                    self._kill_jog(sock, f"按住超过 {self.HOLD_MAX_S:.0f}s 上限")
            except (OSError, ConnectionError) as e:
                self.up.emit(False, f"命令通道断开：{e}")
                self.jog_state = None
                if sock:
                    try:
                        sock.close()
                    except OSError:
                        pass
                sock = None
                time.sleep(1.2)
            except Exception as e:
                # QThread.run 里的未捕获异常会直接终止整个进程，必须落成日志
                self.up.emit(False, f"命令线程异常 {type(e).__name__}: {e}")
                self.jog_state = None
                if sock:
                    try:
                        sock.close()
                    except OSError:
                        pass
                sock = None
                time.sleep(1.2)

    def _kill_jog(self, sock, reason=""):
        j = self.jog_state
        self.jog_state = None
        if j and sock:
            try:
                self._roundtrip(sock, P.encode("LongJogJ", j[2], j[0], j[1], 0).decode(), "LongJogJ")
            except (OSError, ConnectionError):
                pass
        self.jog.emit(f"长点动中止：{reason}")


class Link(QObject):
    log = pyqtSignal(str, str)
    tick = pyqtSignal()
    busy = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.snap = Snapshot()
        self.perm = Perm.READONLY
        self._stream = None
        self._cmd = None
        self._inflight = 0
        self._id_done = False
        self._ready = []
        self._ready_timer = QTimer(self)
        self._ready_timer.setInterval(400)
        self._ready_timer.timeout.connect(self._ready_pump)

    # ------------------------------------------------------------------ 连接
    def connect_to(self, host):
        self.disconnect()
        self.snap = Snapshot()          # 不沿用上一台/上一次的读数
        self._id_done = False
        self._stream = StreamThread(host)
        self._cmd = CmdThread(host)
        self._stream.frame.connect(self._on_frame)
        self._stream.up.connect(lambda u, m: (setattr(self.snap, "stream_up", u), self.log.emit("link", m)))
        self._cmd.up.connect(self._on_cmd_up)
        self._cmd.reply.connect(self._on_reply)
        self._cmd.jog.connect(lambda m: self.log.emit("jog", m))
        self._stream.start()
        self._cmd.start()
        self.log.emit("link", f"正在连接 {host}（取流 10004 / 命令 10003）")

    def _on_cmd_up(self, up, msg):
        self.snap.cmd_up = up
        self.log.emit("link", msg)
        if up and not self._id_done:
            self._id_done = True
            # 全部只读，用来证明对面是真机还是仿真，并取型号/版本
            for c, prm in (("IsSimulation", ()), ("ReadRobotModel", ()), ("PackageVersion", ()),
                             ("ReadCurFSM", (0,))):        # ReadCurFSM 必须带 nRbtID，否则真机回 20007
                self.ask(c, *prm, why="连接后自报家门")

    def disconnect(self):
        if self._cmd:
            self._cmd.end_jog()
            self._cmd.stop()
            self._cmd.wait(3500)
            self._cmd.deleteLater()
            self._cmd = None
        if self._stream:
            self._stream.stop()
            self._stream.wait(2000)
            self._stream.deleteLater()
            self._stream = None
        self.snap.stream_up = self.snap.cmd_up = False

    # ------------------------------------------------------------------ 许可
    def grant(self, level):
        self.perm = level
        self.log.emit("perm", f"许可等级 -> {level.name}")

    def _gate(self, need, what):
        if self.perm < need:
            self.log.emit("interlock", f"已拒绝 {what}：需要「{need.name}」许可，当前为「{self.perm.name}」")
            return False
        if not self.snap.cmd_up:
            self.log.emit("interlock", f"已拒绝 {what}：命令通道未连接")
            return False
        return True

    # ------------------------------------------------------------------ 取流
    def _on_frame(self, d):
        s = self.snap
        pv, st = d.get("PosAndVel", {}), d.get("StateAndError", {})
        pos = pv.get("Actual_Position") or []
        s.joints = [float(x) for x in pos[:6]] if len(pos) >= 6 else [None] * 6
        base = pv.get("Actual_PCS_Base") or []
        s.pcs_base = [float(x) for x in base[:6]] if len(base) >= 6 else [None] * 6
        s.override = float(pv["Actual_Override"]) if "Actual_Override" in pv else None
        s.currents = [float(x) for x in pv.get("Actual_Joint_Current", [])]
        for k, attr in (("robotState", "robot_state"), ("robotEnabled", "enabled"),
                        ("robotMoving", "moving"), ("InPos", "inpos"),
                        ("Error_AxisID", "err_axis"), ("Error_Code", "err_code")):
            if k in st:
                setattr(s, attr, st[k])
        s.brake = st.get("BrakeState", [])
        s.axis_err = st.get("nAxisErrorCode", [])
        s.reduce_mode = st.get("IsReduceMode")
        s.free_drive = st.get("IsFreeDriveMode")
        am = st.get("AutoMode")
        s.auto_mode = None if am is None else str(am)
        s.paused = st.get("robotPaused")
        s.script_err = str(d.get("Script", {}).get("errorCode", ""))
        s.n_reset_safe = st.get("nResetSafeSpace", [])
        hl = d.get("HardLoad", {})
        s.temps = [float(x) for x in hl.get("Joint_temperature", [])]
        s.volt48_in = float(hl["Box48IN_Voltage"]) if "Box48IN_Voltage" in hl else None
        s.volt48_out = float(hl["Box48Out_Voltage"]) if "Box48Out_Voltage" in hl else None
        s.ecat_fps = hl.get("EtherCAT_FramesPerSecond")
        s.ecat_lost = hl.get("EtherCAT_TotalLostFrame")
        s.ecat_tx_err = hl.get("EtherCAT_TxErrorFrame")
        s.ecat_rx_err = hl.get("EtherCAT_RxErrorFrame")
        eio, bio = d.get("EndIO", {}), d.get("ElectricBoxIO", {})
        s.end_di = eio.get("EndDI", [])
        s.end_do = eio.get("EndDO", [])
        s.end_button = eio.get("EndButton", [])
        s.enable_end_btn = eio.get("EnableEndBTN")
        s.end_ai = [float(x) for x in eio.get("EndAI", []) if x not in (None, "")]
        s.box_di = bio.get("BoxDI", [])
        s.box_do = bio.get("BoxDO", [])
        s.box_ci = bio.get("BoxCI", [])
        s.box_co = bio.get("BoxCO", [])
        s.conveyor = bio.get("Conveyor")
        s.encode = bio.get("Encode")
        s.update_time = d.get("MsgTitle", {}).get("UpdateTime", "")
        s.rate_hz = d.get("_rate_hz", 0.0)
        self.tick.emit()

    def _on_reply(self, r):
        self._inflight = max(0, self._inflight - 1)
        self.busy.emit(self._inflight > 0)
        if r.timeout:
            self.log.emit("cmd", f"{r.sent} -> 等待 {r.name} 回执超时 {CmdThread.REPLY_TIMEOUT_S}s")
        elif r.ok:
            self.log.emit("cmd", f"{r.sent} -> {r.name},OK,{','.join(r.fields)},; ({r.rtt_ms:.0f}ms)")
        else:
            hint = P.ERROR_HINT.get(r.err_code or 0, "")
            self.log.emit("cmd", f"{r.sent} -> Fail code={r.err_code} {','.join(r.fields[1:])} {hint}")
        if r.name in ("ReadRobotModel",) and r.ok and r.fields:
            self.snap.model = r.fields[0]
        if r.name in ("PackageVersion",) and r.ok and r.fields:
            self.snap.version = r.fields[0]
        if r.name in ("IsSimulation",) and r.ok and r.fields:
            self.snap.is_sim = "仿真" if r.fields[0] == "1" else "真机"
        if r.name == "ReadCurFSM" and r.ok and r.fields:
            try:
                self.snap.fsm = int(float(r.fields[0]))
            except ValueError:
                pass

    # ------------------------------------------------------------------ 发送
    def ask(self, name, *params, need=Perm.READONLY, why=None, urgent=False):
        if not self._gate(need, why or name):
            return False
        spec = P.CMDS.get(name)
        if spec is None:
            self.log.emit("interlock", f"已拒绝 {name}：本软件指令表里没有这条，不发未知格式")
            return False
        keys = [k for k in spec[0].split(",") if k]
        if len(params) != len(keys):
            self.log.emit("interlock",
                          f"已拒绝 {name}：需要 {len(keys)} 个参数({','.join(keys)})，"
                          f"实际 {len(params)} 个（协议 p{spec[1]}）")
            return False
        for k, v in zip(keys, params):
            rng = P.CMD_RANGES.get((name, k)) or P.LIMITS.get(k)
            if rng is None:
                continue
            try:
                x = float(v)
            except (TypeError, ValueError):
                self.log.emit("interlock", f"已拒绝 {name}：参数 {k}={v!r} 不是数值")
                return False
            if not (rng[0] <= x <= rng[1]):
                self.log.emit("interlock",
                              f"已拒绝 {name}：参数 {k}={v} 超出协议允许范围 [{rng[0]}, {rng[1]}]"
                              f"（协议 p{spec[1]}）")
                return False
        text = P.encode(name, *[str(p) for p in params]).decode()
        self._inflight += 1
        self.busy.emit(True)
        self._cmd.submit(text, name, urgent=urgent)
        return True

    # ------------------------------------------------------------------ 动作
    def jog_step(self, axis, direction, step):
        """单击步进 —— MoveRelJ（协议 p184，nAxisId 0~5 对应 J1~J6）。"""
        if not self._gate(Perm.MOTION, f"步进 J{axis + 1}"):
            return
        lo, hi = P.JOINT_LIMITS[axis]
        edge = P.LIMIT_MARGIN_DEG if P.LIMITS_VERIFIED else 0.0
        cur = self.snap.joints[axis]
        if self.snap.moving:
            self.log.emit("interlock", "已拒绝步进：控制器报告正在运动中")
            return
        if not (P.LIMITS["dDistance"][0] <= step <= P.LIMITS["dDistance"][1]):
            self.log.emit("interlock",
                          f"已拒绝步进：{step}° 超出本软件允许的单次范围 "
                          f"[{P.LIMITS['dDistance'][0]}, {P.LIMITS['dDistance'][1]}]°")
            return
        if cur is not None:
            target = cur + step * (1 if direction else -1)
            if not (lo + edge <= target <= hi - edge):
                src = "手册 p54 截图，机型未核对" if not P.LIMITS_VERIFIED else "已核对"
                self.log.emit("interlock",
                              f"已拒绝步进：J{axis + 1} 目标 {target:.3f}° 超出限位 [{lo}, {hi}]（限位来源：{src}）")
                return
        self.ask("MoveRelJ", 0, axis, direction, f"{step:.3f}", need=Perm.MOTION, why="步进")

    def jog_hold(self, axis, direction, on):
        if on:
            if not self._gate(Perm.MOTION, f"连动 J{axis + 1}"):
                return
            if self.ask("LongJogJ", 0, axis, direction, 1, need=Perm.MOTION, why="长点动"):
                self._cmd.begin_jog(axis, direction)
        else:
            if self._cmd:
                self._cmd.end_jog()

    def set_override(self, ratio):
        lo, hi = P.LIMITS["dOverride"]
        if not (lo <= ratio <= hi):
            self.log.emit("interlock", f"已拒绝速度比 {ratio}：允许范围 {lo}~{hi}（协议 p72）")
            return
        if self.snap.moving:
            self.log.emit("interlock", "已拒绝速度比：协议 p72「运动状态不能修改速度比」")
            return
        self.ask("SetOverride", 0, f"{ratio:.3f}", need=Perm.MOTION, why="设置速度比")

    def emergency_stop(self):
        """不受许可等级限制，任何状态可点。"""
        if self._cmd:
            self._cmd.end_jog()
            self._cmd.submit(P.encode("GrpStop", 0).decode(), "GrpStop", urgent=True)
        self.log.emit("estop", "已发送 GrpStop。注意：协议 p38/手册 p183 —— 手动模式下急停响应为 Stop0 "
                              "不受控停止；本按钮不替代物理急停按钮")

    # ------------------------------------------------------------------ 就绪时序
    def begin_ready(self, then_enable=False):
        """按 SDK p386 顺序推进：Electrify→FSM14→ConnectToBox→FSM24→(GrpEnable→FSM33)。

        状态判据用 Data Sheet 的 robotState；完整状态机表在 HuayanRobot_ErrorCode.docx，
        仓库内缺失，所以这里的数字只做提示不做硬结论。
        """
        if not self._gate(Perm.MOTION if then_enable else Perm.POWER, "就绪流程"):
            return
        steps = [("Electrify", 14, (), "上电"), ("ConnectToBox", 24, (), "连接电箱/初始化")]
        if then_enable:
            steps.append(("GrpEnable", 33, (0,), "使能"))
        self._ready = [(n, w, (), 0.0, False) for n, w, _, _ in steps]
        self._ready_names = [lab for _, _, _, lab in steps]
        self.log.emit("ready", "就绪流程：" + " → ".join(
            f"{n}(目标FSM{w})" for n, w, _, _ in steps))
        self._ready_timer.start()

    def _ready_pump(self):
        if not self._ready:
            self._ready_timer.stop()
            return
        i = 0
        name, want, _p, sent_at, sent = self._ready[i]
        fsm = self.snap.robot_state
        if fsm is not None and fsm >= want:
            self._ready.pop(0)
            self.log.emit("ready", f"{name}：FSM={fsm} 已达目标 {want}，进入下一步")
            if not self._ready:
                self._ready_timer.stop()
                self.log.emit("ready", "就绪流程完成")
            return
        if sent:
            if time.monotonic() - sent_at > 20.0:
                self._ready_timer.stop()
                self.log.emit("ready", f"{name} 后 20s 内 FSM 仍为 {fsm}，流程中止——"
                                       f"请在示教器确认当前状态（FSM 语义表缺失，不替你猜）")
            return
        params = {"GrpEnable": (0,)}.get(name, ())
        if self.ask(name, *params, need=Perm.MOTION if name == "GrpEnable" else Perm.POWER, why=name):
            self._ready[i] = (name, want, params, time.monotonic(), True)
