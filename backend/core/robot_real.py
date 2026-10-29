"""真机链路（asyncio 版）：10004 只读取流 + 10003 串行指令。

编解码复用 backend/core/protocol.py —— 那套已在真机上验过（LTBR 帧、串行指令、
20099/20018 错误码、心跳 200ms 上限）。这里不重复实现，避免两套代码走偏。

诚实原则：连不上或没收到帧时相关字段一律 None，前端显示 "--"，绝不补假值。
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from . import protocol as P

HEARTBEAT_S = 0.15        # 协议 p215：LongMoveEvent 周期不得超过 200ms
REPLY_TIMEOUT_S = 3.0
# Cabinet power/master startup ACKs may take longer than ordinary commands.
# Do not lengthen motion/stop or identity deadlines with this exception.
POWER_REPLY_TIMEOUTS = {"Electrify": 15.0, "StartMaster": 15.0}
STOP_REQUEST_TIMEOUT_S = 2 * REPLY_TIMEOUT_S + .5  # 软件等待上界，不是物理停止时限。
IDENTITY_REPLY_TIMEOUT_S = 0.15  # Low-priority read only; never a Servo period.
HOLD_MAX_S = 15.0         # 连续点动最长按住多久：只是"标签页崩了别一直动"的兜底，
                          # 不是限制操作者（旧值 2s 会把一次正常的长点动硬生生掐断）
JOINT_BUFFER_S = 0.5      # 供前端插值的缓冲长度
RATE_WINDOW_S = 2.0       # 帧率统计窗口：太短会被重连时的突发帧顶歪
STATE_MAX_AGE_S = 0.5     # 通信有效性阈值；不是硬件安全认证的停止时限
STREAM_READ_TIMEOUT_S = 4.0  # 状态过期立即撤销运动，但复位时短暂停流不等于 TCP 已断开

# 官方 Data Sheet 字段 → 本软件快照键 的对照表。
# 这张表是**给测试和「关于」页用的元数据**，不是解析器的实现（解析在 _on_frame 里）：
# 两边对不上时 tools/check_datasheet_fields.py 会失败，防止"帧里明明有数、界面却不给看"。
# 每行：(节, 字段, 快照键, 页码/出处)
FIELD_MAP = [
    ("PosAndVel", "Actual_Position", "joints", "六轴关节角；后 6 个是外部轴位，本机未用"),
    ("PosAndVel", "Actual_PCS_Base", "pcs_base", "基座标系下的法兰位姿（与 FK 对账用）"),
    ("PosAndVel", "Actual_PCS_TCP", "pcs_tcp", "TCP 坐标系位姿"),
    ("PosAndVel", "Actual_PCS_Tool", "pcs_tool", "工具坐标系位姿"),
    ("PosAndVel", "Actual_Joint_Velocity", "vel", "关节速度 °/s"),
    ("PosAndVel", "Actual_Joint_Acceleration", "acc", "关节加速度"),
    ("PosAndVel", "Actual_Joint_Current", "currents", "关节电流"),
    ("PosAndVel", "Actual_Override", "override", "控制器实际速度倍率（0~1）"),
    ("StateAndError", "robotState", "fsm", "状态机档位"),
    ("StateAndError", "robotEnabled", "enabled", "使能"),
    ("StateAndError", "robotMoving", "moving", "正在运动"),
    ("StateAndError", "robotPaused", "paused", "暂停"),
    ("StateAndError", "robotBlendingDone", "blending", "轨迹过渡完成位"),
    ("StateAndError", "InPos", "inpos", "到位"),
    ("StateAndError", "Error_AxisID", "err_axis", "故障轴号"),
    ("StateAndError", "Error_Code", "err_code", "故障码"),
    ("StateAndError", "IsReduceMode", "reduce", "降速模式"),
    ("StateAndError", "IsFreeDriveMode", "free_drive", "手拖示教（松闸）状态回读"),
    ("StateAndError", "AutoMode", "auto_mode", "自动模式档位"),
    ("StateAndError", "BrakeState", "brake", "六轴抱闸"),
    ("StateAndError", "nAxisStatus", "axis_status", "各轴状态码（实测正常=3，未上电时不是 3）"),
    ("StateAndError", "nAxisErrorCode", "axis_err", "各轴错误码"),
    ("StateAndError", "nResetSafeSpace", "safe_space", "安全空间复位位"),
    ("StateAndError", "nAxisGroupStatus", "group_status", "轴组状态"),
    ("StateAndError", "nAxisGroupErrorCode", "group_err", "轴组错误码"),
    ("EndIO", "EndDI", "end_di", "末端 4 路 DI"),
    ("EndIO", "EndDO", "end_do", "末端 DO（可写 3 路，协议 p51）"),
    ("EndIO", "EndButton", "end_button", "末端按键（含 Free 键）"),
    ("EndIO", "EnableEndBTN", "end_btn_en", "末端按键使能位"),
    ("EndIO", "EndAI", "end_ai", "末端 2 路模拟量输入"),
    ("ElectricBoxIO", "BoxDI", "box_di", "电箱 8 路 DI"),
    ("ElectricBoxIO", "BoxDO", "box_do", "电箱 8 路 DO"),
    ("ElectricBoxIO", "BoxCI", "box_ci", "电箱 8 路计数输入"),
    ("ElectricBoxIO", "BoxCO", "box_co", "电箱 8 路脉冲输出"),
    ("ElectricBoxIO", "Conveyor", "conveyor", "传送带跟踪位置"),
    ("ElectricBoxIO", "Encode", "encode", "编码器值"),
    ("ElectricBoxAnalogIO", "BoxAnalogIn_1", "box_ai", "电箱 2 路 AI"),
    ("ElectricBoxAnalogIO", "BoxAnalogOut_1", "box_ao", "电箱 2 路 AO"),
    ("ElectricBoxAnalogIO", "BoxAnalogOutMode_1", "box_ao_mode", "电箱 AO 输出模式（0/1 对应 0~10V 或 4~20mA）"),
    ("FTData", "FTControlState", "ft_state", "力控状态"),
    ("FTData", "FTData", "ft_data", "六维力/力矩（补偿后）"),
    ("FTData", "FTSrcData", "ft_src", "六维力/力矩（原始）"),
    ("HardLoad", "Joint_temperature", "temps", "六轴温度"),
    ("HardLoad", "Joint_Voltage", "joint_volt", "各轴母线电压"),
    ("HardLoad", "Box48IN_Voltage", "volt48_in", "48V 输入电压"),
    ("HardLoad", "Box48IN_Current", "amp48_in", "48V 输入电流"),
    ("HardLoad", "Box48Out_Voltage", "volt48_out", "48V 输出电压"),
    ("HardLoad", "Box48Out_Current", "amp48_out", "48V 输出电流"),
    ("HardLoad", "EtherCAT_FramesPerSecond", "ecat_fps", "EtherCAT 实时帧率"),
    ("HardLoad", "EtherCAT_TotalFrame", "ecat_total", "累计帧"),
    ("HardLoad", "EtherCAT_TotalLostFrame", "ecat_lost", "累计丢帧"),
    ("HardLoad", "EtherCAT_TxErrorFrame", "ecat_tx_err", "发送错帧"),
    ("HardLoad", "EtherCAT_RxErrorFrame", "ecat_rx_err", "接收错帧"),
    ("Script", "errorCode", "script_err", "控制器脚本错误码"),
    ("Script", "ScriptChanged", "script_changed", "脚本改动标志"),
    ("Script", "cmdid", "script_cmdid", "控制器当前执行到的指令 id"),
    ("Script", "callstackcmdid", "script_callstack", "脚本调用栈"),
    ("RobotAuthorization", "DynDeviceCode", "device_code", "动态设备码"),
    ("RobotAuthorization", "DeviceSN", "device_sn", "序列号（导出默认打码）"),
    ("RobotAuthorization", "DeviceMac", "device_mac", "MAC（导出默认打码）"),
    ("RobotAuthorization", "LangAuthStatus", "lang_auth", "语言授权状态"),
    ("RobotAuthorization", "AuthorizedTimeLeftMinutes", "auth_left", "剩余授权分钟"),
    ("RobotAuthorization", "AuthorizedTimeUsedMinutes", "auth_used", "已用授权分钟"),
    ("RobotAuthorization", "SystemDate", "sys_date", "控制器日期"),
    ("MsgTitle", "UpdateTime", "update_time", "控制器帧时间戳"),
]
PARSED_KEYS = {row[2] for row in FIELD_MAP}


class Desync(ConnectionError):
    """回执超时等缓冲区异常：必须重连清缓冲，不能继续发。"""


class Reply:
    __slots__ = ("name", "ok", "fields", "err_code", "rtt_ms", "timeout", "sent", "outcome")

    def __init__(self, name, ok, fields=None, err_code=None, rtt_ms=0.0, timeout=False, sent=""):
        self.name, self.ok, self.fields = name, ok, fields or []
        self.err_code, self.rtt_ms, self.timeout, self.sent = err_code, rtt_ms, timeout, sent
        self.outcome = "acknowledged" if ok else "rejected"

    def as_dict(self):
        return {"name": self.name, "ok": self.ok, "fields": self.fields,
                "err_code": self.err_code, "rtt_ms": round(self.rtt_ms, 1),
                "timeout": self.timeout, "sent": self.sent,
                "outcome": self.outcome,
                "hint": P.ERROR_HINT.get(self.err_code or 0, "")}


class RealLink:
    def __init__(self, host, log=None):
        self.host = host
        self.log = log or (lambda k, m: None)
        self.stream_up = False
        self.cmd_up = False
        self.q: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=128)
        self.epoch = 0
        self.frame_seq = 0
        self._pending = {}
        self._session_bound = {}       # 停止绑定原命令会话和 writer，不绑定状态 epoch。
        self._stop_task = None
        self._stop_binding = None
        self._inflight = None
        self.command_trace = deque(maxlen=200)
        self.last_command = None   # Last non-query acknowledgement, not the frame Error_Code.
        self._command_connection = 0
        self._identity_pending = False
        self._identity_disabled = False
        self._controller_info = {"status": "not_queried", "version": None, "why": "尚未连接"}
        self.controller_timestamp = None
        self._controller_progress_at = 0.0
        self.jogging = None            # (axis, direction)
        self._jog_intent = None
        self._jog_generation = 0
        self.jog_until = 0.0
        self._hb = 0.0
        self._seq = 0
        self._futures: dict[int, asyncio.Future] = {}
        self._tasks = []
        self._closing = False
        self._reader = None
        self._writer = None
        self._pump = None            # 常驻读任务：绝不取消，否则回执会被吞掉
        self._data = asyncio.Event()
        self._dead = False
        self.raw = b""
        self.ascii = b""
        # 快照字段清单：与官方 Data Sheet 的字段一一对上，没收到帧的全部留 None。
        # tools/check_datasheet_fields.py 会拿真实帧样本比对这张表，防"帧里有数、界面不给看"。
        self.snap = {k: None for k in (
            # 位姿与运动
            "joints", "pcs_base", "pcs_tcp", "pcs_tool", "vel", "acc", "override", "rate_hz",
            # 状态机
            "fsm", "enabled", "moving", "inpos", "paused", "blending", "reduce",
            "free_drive", "auto_mode", "err_axis", "err_code",
            "axis_status", "axis_err", "group_status", "group_err", "safe_space", "brake",
            # 电源与热
            "temps", "currents", "joint_volt", "volt48_in", "amp48_in", "volt48_out", "amp48_out",
            # 通讯质量
            "ecat_fps", "ecat_total", "ecat_lost", "ecat_tx_err", "ecat_rx_err",
            # 力控
            "ft_state", "ft_data", "ft_src",
            # IO
            "end_di", "end_do", "end_button", "end_ai", "end_btn_en",
            "box_di", "box_do", "box_ci", "box_co", "box_ai", "box_ao", "box_ao_mode",
            "conveyor", "encode",
            # 控制器脚本执行位（真机程序跑到哪一条）
            "script_err", "script_changed", "script_cmdid", "script_callstack",
            # 设备身份与时钟（导出可脱敏）
            "device_sn", "device_mac", "device_code", "model_name", "lang_auth",
            "auth_left", "auth_used", "sys_date",
            "update_time")}
        self.snap["update_time"] = ""
        self.last_raw = {}          # 最近一帧原始 JSON，给诊断包与字段对齐测试用
        self._slow_at = 0.0         # 慢分组上次刷新时刻
        self.buf = deque(maxlen=64)     # (mono_ts, joints)
        self._rate = deque(maxlen=512)  # 到达时刻，只用于算帧率

    # ------------------------------------------------------------------ 生命周期
    @property
    def command_session(self):
        """命令连接身份；状态流失效只改变 epoch，不改变本编号。"""
        return self._command_connection

    def controller_info(self):
        """Cached identity only; reading status never issues controller commands."""
        if not self.cmd_up or self._dead or self._closing:
            return {"status": "disconnected", "version": None, "why": "命令通道未连接",
                    "command_connection": self._command_connection,
                    "command_session": self.command_session, "motion_qualified": False}
        return {**self._controller_info, "command_connection": self._command_connection,
                "command_session": self.command_session,
                "motion_qualified": False}

    def _begin_controller_identity(self):
        self._command_connection += 1
        self._identity_pending = not self._identity_disabled
        self._controller_info = {"status": "pending" if self._identity_pending else "unknown",
                                 "version": None, "why": "等待未使能且静止后读取版本" if self._identity_pending
                                 else "本次连接对象自动查询已失败，不循环重试"}

    async def _probe_controller_identity(self):
        # Do not steal time from jogging, queued commands, live motion or an
        # enabled arm. If the device is already enabled, defer until disabled.
        if (not self._identity_pending or self._closing or not self.cmd_up or self._dead
                or self.jogging or self._jog_intent or not self.q.empty() or not self.state_valid()
                or self.snap.get("enabled") not in (0, False)
                or self.snap.get("moving") not in (0, False)
                or self.snap.get("free_drive") not in (0, False)):
            return False
        self._identity_pending = False
        generation = self._command_connection
        self._controller_info = {"status": "querying", "version": None, "why": "正在只读查询版本"}
        try:
            reply = await self._roundtrip(P.encode("PackageVersion").decode(), "PackageVersion",
                                         reply_timeout=IDENTITY_REPLY_TIMEOUT_S)
        except asyncio.CancelledError:
            self._controller_info = {"status": "unknown", "version": None, "why": "版本查询已取消"}
            raise
        except Exception:
            # A late ASCII reply requires the normal channel desync recovery,
            # but must not cause an endless reconnect/auto-query loop.
            self._identity_disabled = True
            self._controller_info = {"status": "unknown", "version": None, "why": "版本查询失败，不自动重试"}
            raise
        if generation != self._command_connection or not self.cmd_up or self._dead or self._closing:
            return False
        value = reply.fields[0] if reply.ok and reply.name == "PackageVersion" and len(reply.fields) == 1 else None
        valid = (isinstance(value,str) and 1 <= len(value.strip()) <= 128
                 and all(32 <= ord(c) < 127 for c in value))
        if valid:
            self._controller_info = {"status": "known", "version": value.strip(), "why": "控制器只读回执"}
            self.log("link", "控制器软件版本："+value.strip())
        else:
            self._identity_disabled = True
            self._controller_info = {"status": "unknown", "version": None,
                                     "why": "版本查询未成功或回执格式无效"}
        # Never parse a version into an automatic motion-enable decision.
        return valid

    async def start(self):
        self._closing = False
        self._tasks = [asyncio.create_task(self._stream_loop()),
                       asyncio.create_task(self._cmd_loop())]

    async def stop(self):
        pending = self._stop_task
        if pending and not pending.done() and pending is not asyncio.current_task():
            # 正确：正常关闭先给原会话中已受理的停止事务完成机会。
            # World 的短等待超时不能在关闭 socket 时再次把队列里的停止删掉。
            try:
                await asyncio.wait_for(asyncio.shield(pending), STOP_REQUEST_TIMEOUT_S + .1)
            except asyncio.TimeoutError:
                self.log("alarm", "连接关闭前停止事务仍未确认，现场必须核对停止状态")
            except Exception as exc:
                self.log("alarm", "连接关闭前停止事务异常：" + type(exc).__name__)
        self._closing = True
        self.invalidate_pending("连接关闭")
        if self._stop_task and not self._stop_task.done():
            self._stop_task.cancel()
            await asyncio.wait({self._stop_task}, timeout=.1)
        self.jogging = None
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._tasks = []
        if self._pump:
            self._pump.cancel()
            try:
                await self._pump
            except asyncio.CancelledError:
                pass
            self._pump = None
        for w in (self._writer,):
            if w:
                try:
                    w.close()
                except Exception:
                    pass
        self._writer = self._reader = None
        self.stream_up = self.cmd_up = False

    # ------------------------------------------------------------------ 取流
    async def _stream_loop(self):
        while not self._closing:
            w = None
            try:
                r, w = await asyncio.wait_for(
                    asyncio.open_connection(self.host, P.PORT_STREAM_JSON[0]), timeout=4)
                self.stream_up = True
                self.epoch += 1
                self.invalidate_pending("取流连接更新", advance=False, preserve_inflight=True)
                self.snap["stamp"] = 0
                self.controller_timestamp = None
                self._controller_progress_at = 0.0
                self.buf.clear()
                self._rate.clear()
                self._slow_at = 0.0
                self.log("link", f"取流已连接 {self.host}:{P.PORT_STREAM_JSON[0]}")
                buf = b""
                while True:
                    chunk = await asyncio.wait_for(
                        r.read(65536), timeout=STREAM_READ_TIMEOUT_S)
                    if not chunk:
                        raise ConnectionError("控制器关闭了取流连接")
                    buf += chunk
                    payloads, buf, _ascii = P.split_frames(buf)
                    for pl in payloads:
                        try:
                            self._on_frame(P.decode_state(pl))
                        except (ValueError, UnicodeDecodeError):
                            continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.stream_up = False
                self.invalidate_pending("取流断开", preserve_inflight=True)
                if self.cmd_up and self._writer is not None:
                    # 正确：只向仍在线的原命令会话停止，不等待重连后重放。
                    self._begin_stop(self.command_session, "状态流断开，保护性停止")
                self.jogging = None
                self.log("link", f"取流断开：{type(e).__name__} {e}")
                if w:
                    w.close()
                    w = None
                await asyncio.sleep(1.5)
            finally:
                if w:
                    w.close()

    def pose_valid(self):
        """Read-only encoder freshness; enable/FSM flags do not gate display."""
        s=self.snap;stamp=s.get('stamp');joints=s.get('joints')
        return bool(self.stream_up and s.get('connection_epoch')==self.epoch
                    and type(stamp) in (int,float) and 0<=time.monotonic()-stamp<=STATE_MAX_AGE_S
                    and isinstance(joints,(list,tuple)) and len(joints)==6
                    and all(type(v) in (int,float) and math.isfinite(v) for v in joints))

    def state_valid(self):
        s = self.snap
        age = time.monotonic() - s.get("stamp", 0)
        joints = s.get("joints")
        flags = (s.get("fsm"), s.get("enabled"), s.get("moving"), s.get("inpos"))
        return bool(self.pose_valid() and s.get("connection_epoch") == self.epoch
                    and 0 <= age <= STATE_MAX_AGE_S and joints and len(joints) == 6
                    and (self.controller_timestamp is None or
                         time.monotonic() - self._controller_progress_at <= STATE_MAX_AGE_S)
                    and all(math.isfinite(float(v)) for v in joints)
                    and isinstance(flags[0], int) and not isinstance(flags[0], bool)
                    and all(v in (0, 1, True, False) for v in flags[1:]))

    def _bound_stop_valid(self, seq):
        binding = self._session_bound.get(seq)
        return bool(binding is not None and not self._closing and self.cmd_up and not self._dead
                    and binding[0] == self.command_session and binding[1] is self._writer)

    def invalidate_pending(self, reason, advance=True, preserve_inflight=False):
        if advance:
            self.epoch += 1
        retained = []
        while not self.q.empty():
            try:
                item = self.q.get_nowait()
                if self._bound_stop_valid(item[1]):
                    retained.append(item)
            except asyncio.QueueEmpty:
                break
        for item in retained:
            self.q.put_nowait(item)
        for seq, fut in list(self._futures.items()):
            name = next((row["name"] for row in reversed(self.command_trace)
                         if row.get("command_id") == seq), "?")
            # 状态流与命令口是两个连接。保留已发出的只读/停机/清错回执，
            # 不把真实拒绝码吞成“未知”；排队动作和上电/运动仍全部作废。
            independent = name.startswith(("Read", "Get", "Is")) or name in (
                "PackageVersion", "GrpReset", "GrpStop", "GrpDisable", "BlackOut")
            if self._bound_stop_valid(seq) and not fut.done():
                continue
            if preserve_inflight and seq == self._inflight and independent and not fut.done():
                continue
            if not fut.done():
                rep = Reply(name, False, sent=reason)
                rep.outcome = "unknown" if seq == self._inflight else "cancelled"
                fut.set_result(rep)
            self._futures.pop(seq, None)
            self._pending.pop(seq, None)
            self._session_bound.pop(seq, None)

    SLOW_EVERY_S = 1.0     # 变化慢的分组（电源/通讯计数/脚本/设备身份）按 1Hz 刷

    @staticmethod
    def _fl(v):
        """字段 → 浮点列表；缺字段返回 None（前端显示 --），绝不返回 [0,0,...] 冒充读到。"""
        if not isinstance(v, (list, tuple)):
            return None
        try:
            values = [float(x) for x in v]
            return values if all(math.isfinite(x) for x in values) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _f(v):
        try:
            value = float(v)
            return value if math.isfinite(value) else None
        except (TypeError, ValueError):
            return None

    def _on_frame(self, d):
        if not isinstance(d, dict):
            raise ValueError("Data Sheet 根节点必须是对象")
        for section in ("PosAndVel", "StateAndError", "EndIO", "ElectricBoxIO", "FTData",
                        "MsgTitle", "HardLoad", "ElectricBoxAnalogIO", "Script", "RobotAuthorization"):
            if section in d and not isinstance(d[section], dict):
                raise ValueError(f"Data Sheet {section} 必须是对象")
        pv, st = d.get("PosAndVel", {}), dict(d.get("StateAndError", {}))
        pos = pv.get("Actual_Position")
        joints = None
        if pos is not None:
            if not isinstance(pos, (list, tuple)) or len(pos) < 6:
                raise ValueError("Actual_Position 需要六轴数据")
            try:
                joints = [float(x) for x in pos[:6]]
            except (TypeError, ValueError) as exc:
                raise ValueError("Actual_Position 含无效数值") from exc
            if not all(math.isfinite(x) for x in joints):
                raise ValueError("Actual_Position 含非有限数值")
        for key in ("robotState", "robotEnabled", "robotMoving", "InPos", "robotPaused",
                    "robotBlendingDone", "Error_AxisID", "Error_Code", "IsReduceMode",
                    "IsFreeDriveMode", "AutoMode"):
            value = st.get(key)
            if isinstance(value, str):
                try:
                    st[key] = int(value)
                except ValueError:
                    st[key] = None
        self.last_raw = d
        s = self.snap
        now = time.monotonic()
        title = d.get("MsgTitle", {})
        token = title.get("Stamp") or title.get("UpdateTime") or None
        if token is not None and token != self.controller_timestamp:
            self._controller_progress_at = now
        self.controller_timestamp = token
        s["joints"] = joints
        if joints is not None:
            self.buf.append((now, list(s["joints"])))
            cutoff = now - JOINT_BUFFER_S
            while self.buf and self.buf[0][0] < cutoff:
                self.buf.popleft()
            if len(self.buf) >= 4:
                # 帧率单独用 2s 到达窗口算。插值缓冲只有 0.5s，重连时 socket 里积压的
                # 一批帧会在同一瞬间全部盖戳，0.5s 窗口能被突发帧顶到几百 Hz。
                self._rate.append(now)
                while self._rate and now - self._rate[0] > RATE_WINDOW_S:
                    self._rate.popleft()
                if len(self._rate) >= 4:
                    span = self._rate[-1] - self._rate[0]
                    s["rate_hz"] = round((len(self._rate) - 1) / span, 1) if span > 0.5 else None
        for key, src in (("pcs_base", "Actual_PCS_Base"), ("pcs_tcp", "Actual_PCS_TCP"),
                         ("pcs_tool", "Actual_PCS_Tool"), ("vel", "Actual_Joint_Velocity"),
                         ("acc", "Actual_Joint_Acceleration"), ("currents", "Actual_Joint_Current")):
            v = self._fl(pv.get(src))
            s[key] = v[:6] if v and len(v) >= 6 else v
        s["override"] = self._f(pv["Actual_Override"]) if "Actual_Override" in pv else None
        for src, dst in (("robotState", "fsm"), ("robotEnabled", "enabled"),
                         ("robotMoving", "moving"), ("InPos", "inpos"),
                         ("robotPaused", "paused"), ("robotBlendingDone", "blending"),
                         ("Error_AxisID", "err_axis"), ("Error_Code", "err_code"),
                         ("IsReduceMode", "reduce"), ("IsFreeDriveMode", "free_drive"),
                         ("AutoMode", "auto_mode"), ("nResetSafeSpace", "safe_space"),
                         ("nAxisGroupStatus", "group_status"),
                         ("nAxisGroupErrorCode", "group_err")):
            s[dst] = st.get(src)
        s["brake"] = st.get("BrakeState")
        s["axis_status"] = st.get("nAxisStatus")
        s["axis_err"] = st.get("nAxisErrorCode")
        eio, bio = d.get("EndIO", {}), d.get("ElectricBoxIO", {})
        s["end_di"], s["end_do"] = eio.get("EndDI"), eio.get("EndDO")
        s["end_button"], s["end_ai"] = eio.get("EndButton"), self._fl(eio.get("EndAI"))
        s["end_btn_en"] = eio.get("EnableEndBTN")
        s["box_di"], s["box_do"] = bio.get("BoxDI"), bio.get("BoxDO")
        s["conveyor"], s["encode"] = bio.get("Conveyor"), bio.get("Encode")
        ft = d.get("FTData", {})
        s["ft_state"] = ft.get("FTControlState")
        s["ft_data"] = self._fl(ft.get("FTData"))
        s["ft_src"] = self._fl(ft.get("FTSrcData"))
        s["update_time"] = d.get("MsgTitle", {}).get("UpdateTime", "")
        s["stamp"] = now
        self.frame_seq += 1
        s["frame_seq"] = self.frame_seq
        s["connection_epoch"] = self.epoch
        if now - self._slow_at >= self.SLOW_EVERY_S:
            self._slow_at = now
            self._parse_slow(d, s, now)

    def _parse_slow(self, d, s, now):
        """变化慢的分组每秒刷一次：这些值不是渲染路径要用的，进 20Hz 快照纯属浪费。"""
        hl, bio = d.get("HardLoad", {}), d.get("ElectricBoxIO", {})
        s["ecat_fps"] = self._f(hl.get("EtherCAT_FramesPerSecond"))
        for key, src in (("ecat_total", "EtherCAT_TotalFrame"),
                         ("ecat_lost", "EtherCAT_TotalLostFrame"),
                         ("ecat_tx_err", "EtherCAT_TxErrorFrame"),
                         ("ecat_rx_err", "EtherCAT_RxErrorFrame"),
                         ("amp48_in", "Box48IN_Current"), ("volt48_out", "Box48Out_Voltage"),
                         ("amp48_out", "Box48Out_Current")):
            s[key] = self._f(hl.get(src))
        s["temps"] = self._fl(hl.get("Joint_temperature"))
        s["volt48_in"] = self._f(hl["Box48IN_Voltage"]) if "Box48IN_Voltage" in hl else None
        s["joint_volt"] = self._fl(hl.get("Joint_Voltage"))
        s["box_ci"], s["box_co"] = bio.get("BoxCI"), bio.get("BoxCO")
        # 模拟量两路单独一节（ElectricBoxAnalogIO），不在 ElectricBoxIO 里 —— 读错节会一直是 None
        ao = d.get("ElectricBoxAnalogIO", {})
        s["box_ai"] = [self._f(ao.get("BoxAnalogIn_1")), self._f(ao.get("BoxAnalogIn_2"))]
        s["box_ao"] = [self._f(ao.get("BoxAnalogOut_1")), self._f(ao.get("BoxAnalogOut_2"))]
        s["box_ao_mode"] = [ao.get("BoxAnalogOutMode_1"), ao.get("BoxAnalogOutMode_2")]
        sc = d.get("Script", {})
        s["script_err"] = sc.get("errorCode")
        s["script_changed"] = sc.get("ScriptChanged")
        s["script_cmdid"] = sc.get("cmdid")
        s["script_callstack"] = sc.get("callstackcmdid")
        au = d.get("RobotAuthorization", {})
        for key, src in (("device_code", "DynDeviceCode"), ("device_sn", "DeviceSN"),
                         ("device_mac", "DeviceMac"), ("model_name", "RobotModel"),
                         ("lang_auth", "LangAuthStatus"), ("auth_left", "AuthorizedTimeLeftMinutes"),
                         ("auth_used", "AuthorizedTimeUsedMinutes"), ("sys_date", "SystemDate")):
            s[key] = au.get(src)
        rv = d.get("ReadRobotModel") or d.get("RobotModel")
        if rv and not s.get("model_name"):
            s["model_name"] = rv if isinstance(rv, str) else str(rv)

    def lag_ms(self):
        if not self.buf:
            return None
        return round((time.monotonic() - self.buf[-1][0]) * 1000, 1)

    # ------------------------------------------------------------------ 命令通道
    async def _pump_reader(self, reader):
        """唯一的 socket 读取者。到达的字节只往 self.raw 里堆，然后叫醒等待者。"""
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    raise ConnectionError("命令连接被控制器关闭")
                self.raw += data
                _frames, self.raw, text = P.split_frames(self.raw)
                self.ascii += text
                if len(self.ascii) > 65536:
                    raise ConnectionError("命令通道未消费文本超过上限")
                self._data.set()
        except (asyncio.CancelledError, Exception) as e:
            self._dead = True
            self.cmd_up = False
            self._data.set()
            if not isinstance(e, asyncio.CancelledError):
                self.log("link", "命令口读任务结束：" + type(e).__name__ + " " + str(e))

    async def _cmd_loop(self):
        while not self._closing:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, P.PORT_CMD), timeout=4)
                self._reader, self._writer = reader, writer
                self.invalidate_pending("命令连接更新")
                self._dead = False
                self._data.clear()
                self.raw, self.ascii = b"", b""
                self.cmd_up = True
                self._begin_controller_identity()
                self._pump = asyncio.create_task(self._pump_reader(reader))
                self.log("link", f"命令通道已连接 {self.host}:{P.PORT_CMD}")
                while True:
                    if self._dead:
                        raise ConnectionError("命令通道读任务已停止")
                    if self.jogging and time.monotonic() - self._hb >= HEARTBEAT_S:
                        self._hb = time.monotonic()
                        rep = await self._roundtrip(P.encode("LongMoveEvent", 0).decode(),
                                                    "LongMoveEvent")
                        if not rep.ok:
                            await self._kill_jog(f"心跳异常 code={rep.err_code}")
                            continue
                    try:
                        _prio, seq, text, name = self.q.get_nowait()
                    except asyncio.QueueEmpty:
                        await self._probe_controller_identity()
                    else:
                        meta = self._pending.get(seq)
                        fut = self._futures.get(seq)
                        bound_stop = seq in self._session_bound
                        valid_session = self._bound_stop_valid(seq) if bound_stop else bool(meta and meta[1] == self.epoch)
                        if not meta or not fut or fut.done() or not valid_session or time.monotonic() >= meta[0]:
                            if fut and not fut.done():
                                rep = Reply(name, False, sent="排队已过期，未发送")
                                rep.outcome = "expired"
                                fut.set_result(rep)
                            self._pending.pop(seq, None)
                            self._futures.pop(seq, None)
                            self._session_bound.pop(seq, None)
                            continue
                        if self.jogging and name not in ("LongJogJ", "GrpStop", "SetOverride", "GrpCloseFreeDriver"):
                            rep = Reply(name, False, sent="点动中暂不发送普通事务，避免侵占心跳")
                            rep.outcome = "not_sent"
                            fut.set_result(rep)
                            self._futures.pop(seq, None)
                            self._pending.pop(seq, None)
                            continue
                        try:
                            self._inflight = seq
                            rep = await self._roundtrip(text, name)
                        except Desync as e:
                            self.log("cmd", f"{name} 通道异常：{e} —— 重连清缓冲")
                            raise ConnectionError(str(e))
                        fut = self._futures.pop(seq, None)
                        self._pending.pop(seq, None)
                        self._session_bound.pop(seq, None)
                        self._inflight = None
                        if fut and not fut.done():
                            fut.set_result(rep)
                    if self.jogging and time.monotonic() > self.jog_until:
                        await self._kill_jog(f"按住超过 {HOLD_MAX_S:.0f}s 上限")
                    await asyncio.sleep(0.005)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.cmd_up = False
                self.jogging = None
                self.invalidate_pending("通道断开，执行结果需核对")
                self._inflight = None
                self.log("link", f"命令通道断开：{type(e).__name__} {e}")
                if self._writer:
                    try:
                        self._writer.close()
                    except Exception:
                        pass
                if self._pump:
                    self._pump.cancel()
                    await asyncio.gather(self._pump, return_exceptions=True)
                    self._pump = None
                self._writer = self._reader = None
                await asyncio.sleep(1.2)

    async def _roundtrip(self, text, name, *, reply_timeout=None):
        """发一条并等它的回执；命令口也会推 LTBR 帧，要先剥帧再解析 ASCII。"""
        _frames, self.raw, extracted = P.split_frames(self.raw)
        self.ascii += extracted
        if self.ascii:
            self.log("cmd", "清除发送前的残留回执，避免确认新的同名指令")
            self.ascii = b""
        t0 = time.monotonic()
        budget = (reply_timeout if reply_timeout is not None else
                  0.04 if self.jogging else POWER_REPLY_TIMEOUTS.get(name, REPLY_TIMEOUT_S))
        deadline = t0 + budget
        self._writer.write(text.encode("ascii"))
        try:
            await asyncio.wait_for(self._writer.drain(), timeout=max(0.001, deadline - time.monotonic()))
        except asyncio.TimeoutError as exc:
            raise Desync(f"{name} 写入超时") from exc
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
                rep = Reply(rname, rok, rfields, code, (time.monotonic() - t0) * 1000, False, text)
                if rep.name != name:
                    self.log("cmd", f"忽略未匹配的迟到回执：{rep.name}")
                    continue
                return rep
            if self._dead:
                raise ConnectionError("命令通道读任务已停止")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                # 等 Event 可以安全取消；等 read() 不行
                await asyncio.wait_for(self._data.wait(), timeout=min(0.25, remaining))
            except asyncio.TimeoutError:
                pass
            self._data.clear()
        # 超时说明缓冲区可能已经错位（半截帧吃掉回执）。抛出去让 _cmd_loop 重连，
        # 绝不带着脏缓冲继续发下一条。
        raise Desync(f"{name} 回执超时 {budget}s")

    async def _kill_jog(self, reason=""):
        j = self.jogging
        self.jogging = None
        if j and self._writer:
            try:
                await self._roundtrip(P.encode("LongJogJ", 0, j[0], j[1], 0).decode(), "LongJogJ")
            except (Desync, ConnectionError):
                # 回执通道已经不可信，交给 cmd_loop 统一断开/重连；吞掉会让迟到回执
                # 留在缓冲区，下一条同名命令可能被误配。
                self.log("link", f"长点动停止回执未知：{reason}")
                raise
        self.log("jog", f"长点动中止：{reason}")

    # ------------------------------------------------------------------ 对外接口
    def _remember_reply(self, name, reply, command_id=None, connection=None):
        if not name.startswith(("Read", "Get", "Is")) and name != "PackageVersion":
            self.last_command = {**reply.as_dict(), "name": name, "t": time.time(),
                                 "command_id": command_id,
                                 "connection_epoch": self.epoch if connection is None else connection}
        return reply

    def _validate(self, name, params):
        if not isinstance(name, str):
            return "命令名必须是字符串"
        spec = P.CMDS.get(name)
        if spec is None:
            return f"指令表里没有 {name}，不发未知格式"
        keys = [k for k in spec[0].split(",") if k]
        if len(params) != len(keys):
            return f"{name} 需要 {len(keys)} 个参数({','.join(keys)})，实际 {len(params)}（协议 p{spec[1]}）"
        for k, v in zip(keys, params):
            try:
                P.wire_token(v)
            except ValueError as exc:
                return f"{name} 的参数 {k} 无效：{exc}"
            if k.startswith("s"):
                if type(v) is not str:
                    return f"{name} 的参数 {k} 必须是字符串"
                continue
            rng = P.CMD_RANGES.get((name, k)) or P.LIMITS.get(k)
            try:
                x = float(v)
            except (TypeError, ValueError, OverflowError):
                return f"{name} 的参数 {k}={v!r} 不是数值"
            if not math.isfinite(x):
                return f"{name} 的参数 {k} 必须是有限数值"
            if rng is not None and not (rng[0] <= x <= rng[1]):
                return f"{name} 的参数 {k}={v} 超出允许范围 [{rng[0]},{rng[1]}]（协议 p{spec[1]}）"
            if (k.startswith(("n", "b")) or k == "boxID") and not x.is_integer():
                return f"{name} 的参数 {k} 必须是整数（协议 p{spec[1]}）"
        return None

    async def ask(self, name, *params, timeout=None, urgent=False, command_session=None):
        if timeout is None:
            timeout = POWER_REPLY_TIMEOUTS.get(name, REPLY_TIMEOUT_S) + 3.0
        if name == "GrpStop" and command_session is None:
            return await self.estop()
        err = self._validate(name, params)
        if command_session is not None and (name != "GrpStop" or type(command_session) is not int
                                            or command_session != self.command_session):
            err = "停止绑定的原命令会话已失效，禁止向新会话发送"
        if name != "GrpStop" and self._stop_task and not self._stop_task.done():
            err = "停止事务尚未结束，暂不接受其他命令"
        if err:
            self.log("interlock", f"已拒绝 {err}")
            rep = Reply(name, False, [], None, 0.0, False, err)
            rep.outcome = "not_sent"
            return self._remember_reply(name, rep)
        if not self.cmd_up:
            rep = Reply(name, False, [], None, 0.0, False, "命令通道未连接")
            rep.outcome = "not_sent"
            return self._remember_reply(name, rep)
        text = P.encode(name, *[str(p) for p in params]).decode()
        self._seq += 1
        seq = self._seq
        trace = {"command_id": seq, "connection_epoch": self.epoch, "name": name,
                 "t": time.time(), "outcome": "queued"}
        self.command_trace.append(trace)
        fut = asyncio.get_running_loop().create_future()
        self._futures[seq] = fut
        self._pending[seq] = (time.monotonic() + timeout, self.epoch)
        if command_session is not None:
            self._session_bound[seq] = (command_session, self._writer)
        try:
            self.q.put_nowait((0 if urgent else 1, seq, text, name))
        except asyncio.QueueFull:
            self._futures.pop(seq, None)
            self._pending.pop(seq, None)
            self._session_bound.pop(seq, None)
            rep = Reply(name, False, sent="命令队列已满，未发送")
            rep.outcome = "not_sent"
            return self._remember_reply(name, rep, seq, trace["connection_epoch"])
        try:
            rep = await asyncio.wait_for(fut, timeout=timeout)
            self._session_bound.pop(seq, None)
            trace.update(outcome=rep.outcome, rtt_ms=rep.rtt_ms, err_code=rep.err_code)
            self._remember_reply(name, rep, seq, trace["connection_epoch"])
            self.log("cmd", f"{text} -> {rep.name},{'OK' if rep.ok else 'Fail'}"
                            f"{',' + ','.join(map(str, rep.fields)) if rep.fields else ''} "
                            f"({rep.rtt_ms:.0f}ms)")
            return rep
        except asyncio.TimeoutError:
            self._futures.pop(seq, None)
            self._pending.pop(seq, None)
            self._session_bound.pop(seq, None)
            self.log("cmd", f"{text} -> 回执超时 {timeout}s")
            rep = Reply(name, False, [], None, timeout * 1000, True, text)
            rep.outcome = "unknown" if self._inflight == seq else "expired"
            trace["outcome"] = rep.outcome
            return self._remember_reply(name, rep, seq, trace["connection_epoch"])
        except asyncio.CancelledError:
            trace["outcome"] = "unknown" if self._inflight == seq else "cancelled"
            rep = Reply(name, False, sent="请求已取消，执行结果需核对")
            rep.outcome = trace["outcome"]
            self._remember_reply(name, rep, seq, trace["connection_epoch"])
            self._futures.pop(seq, None)
            self._pending.pop(seq, None)
            self._session_bound.pop(seq, None)
            raise

    def _begin_stop(self, command_session, reason="停止请求"):
        binding = (command_session, self._writer)
        if (type(command_session) is not int or command_session != self.command_session
                or not self.cmd_up or self._dead or self._closing):
            return None
        if self._stop_task and not self._stop_task.done():
            same = (self._stop_binding[0] == command_session and self._stop_binding[1] is self._writer)
            return self._stop_task if same else None
        self._jog_generation += 1
        self._jog_intent = None
        self.invalidate_pending(reason)
        self.jogging = None
        self._stop_binding = binding
        self._stop_task = asyncio.create_task(self._stop_on_session(binding))
        # 正确：调用方离开后任务仍由链路持有，异常也必须被回收。
        self._stop_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        return self._stop_task

    async def _stop_on_session(self, binding):
        session, writer = binding
        if (session != self.command_session or writer is not self._writer
                or not self.cmd_up or self._dead or self._closing):
            rep = Reply("GrpStop", False, sent="原命令连接已变化，停止未重放")
            rep.outcome = "unknown"
            return rep
        try:
            # 正确：由唯一命令循环串行发送，不取消在途回执，不新增 socket。
            # 原事务超时导致原连接失效时，只报告未知，不能在新连接补发。
            rep = await self.ask("GrpStop", 0, urgent=True, timeout=STOP_REQUEST_TIMEOUT_S,
                                 command_session=session)
            if (session != self.command_session or writer is not self._writer
                    or not self.cmd_up or self._dead or self._closing):
                unknown = Reply("GrpStop", False, sent="停止期间原命令连接失效，旧回执不能确认当前连接")
                unknown.outcome = "unknown"
                return self._remember_reply("GrpStop", unknown)
            return rep
        except (Exception, asyncio.CancelledError) as exc:
            rep = Reply("GrpStop", False, sent="停止事务结果未知：" + type(exc).__name__)
            rep.outcome = "unknown"
            return rep

    async def estop(self, *, command_session=None):
        session = self.command_session if command_session is None else command_session
        task = self._begin_stop(session)
        if task is None:
            rep = Reply("GrpStop", False, sent="原命令会话不可用，停止未发送且未重放")
            rep.outcome = "not_sent"
            return self._remember_reply("GrpStop", rep)
        # 正确：外层 wait_for/HTTP 取消不能撤销已经受理的停止意图。
        return await asyncio.shield(task)

    async def jog_start(self, axis, direction):
        epoch = self.epoch
        generation = self._jog_generation
        self._jog_intent = (axis, direction)
        rep = await self.ask("LongJogJ", 0, axis, direction, 1)
        if rep.ok and epoch == self.epoch and generation == self._jog_generation:
            self.jogging = (axis, direction)
            self.jog_until = time.monotonic() + HOLD_MAX_S
            self._hb = time.monotonic()
        if generation == self._jog_generation:
            self._jog_intent = None
        return rep

    async def jog_end(self):
        intent = self.jogging or self._jog_intent
        self._jog_generation += 1
        self._jog_intent = None
        self.invalidate_pending("点动松开", advance=False)
        if not intent:
            return Reply("LongJogJ", True, [], None, 0.0, False, "无点动在跑")
        axis, direction = intent
        self.jogging = None
        return await self.ask("LongJogJ", 0, axis, direction, 0, urgent=True, timeout=4.0)
