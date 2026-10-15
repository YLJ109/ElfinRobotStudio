"""World：整个仿真/监控系统的唯一状态源。

一个 50Hz 的 asyncio 循环推进物理、跑执行队列、评估安全、广播快照。
REST/WS 只做输入输出，不各自持有状态，避免出现两份真相。

真机模式的点位执行只用**已实测验证过的** MoveRelJ 逐轴逼近；
绝对点 MoveJ 的语法在协议里没取证，不猜、不发。
"""

from __future__ import annotations

import asyncio
import copy
import json
import math
import secrets
import time
from collections import deque

from . import ai_plan, config, controls, program, telemetry
from .eventlog import EventLog
from .motion import MotionExecutor
from .storage import write_json
from .calibration import CalibrationStore
from .planning import digest, inspect_path, waypoints
from .kinematics import RobotModel
from .objects import ObjectStore
from .points import PointStore
from .robot_real import RealLink
from .robot_sim import SimRobot
from .safety import ALARM, GREEN, evaluate
from .tools import ToolStore


# 快照分两个通道：FAST_KEYS 每帧（20Hz）发，TELE_KEYS 每秒发一次。
# 分错了会让 20Hz 报文越来越胖，前端反而更卡 —— 新加字段先想清楚它是不是每帧都要用。
FAST_KEYS = ("fsm", "enabled", "moving", "inpos", "paused", "err_code", "err_axis", "override",
             "rate_hz", "update_time", "brake", "pcs_base", "free_drive", "end_di", "end_do",
             "end_button", "auto_mode", "reduce", "blending")
TELE_KEYS = ("temps", "currents", "joint_volt", "vel", "acc", "volt48_in", "amp48_in",
             "volt48_out", "amp48_out", "ecat_fps", "ecat_total", "ecat_lost", "ecat_tx_err",
             "ecat_rx_err", "ft_state", "ft_data", "ft_src", "pcs_tcp", "pcs_tool",
             "end_ai", "end_btn_en", "box_di", "box_do", "box_ci", "box_co", "box_ai", "box_ao",
             "box_ao_mode",
             "conveyor", "encode", "axis_status", "axis_err", "group_status", "group_err",
             "safe_space", "script_err", "script_changed", "script_cmdid", "script_callstack",
             "device_sn", "device_mac", "device_code", "model_name", "lang_auth",
             "auth_left", "auth_used", "sys_date")


class World:
    def __init__(self):
        self.settings = config.get_settings()
        self.model = RobotModel("elfin5")
        self.log = EventLog()
        self.points = PointStore()
        self.objects = ObjectStore()
        self.sim = SimRobot(self.model, self._vmax())
        self.sim.speed_pct = self.settings["sim_speed_pct"]
        self.speed_ack = {"target": None, "req": None, "ok": None, "why": ""}
        self._speed_task = None
        self._apply_limits()
        self.real: RealLink | None = None
        self.runner = program.Runner()
        self.runner.input_reader = self._read_di
        self.executor = MotionExecutor(self.log.add)
        self._broadcast_at = 0.0
        self._link_lost = False
        self.stop_result = None
        self.session_id = secrets.token_hex(8)
        self.calibrations = CalibrationStore()
        self.tools = ToolStore()
        self.plans = {}
        self.blackbox = deque(maxlen=1200)
        self.sim_io = {"end_di": [0] * 4, "box_di": [0] * 8, "end_do": [0] * 3, "box_do": [0] * 8}
        self.queue = []              # 点位执行队列 [{"point":..., "joints":[...]}]
        self.queue_state = "idle"
        self.queue_step = None
        self.ghost = []
        self.obj_hits = []
        self._cfg_rev = 0             # 设置一改就 +1，用来判断安全结果能不能复用
        self._safe_cache = None         # 上一帧的安全结果
        self._safe_key = None           # 对应的 (姿态, 设置版本, 摆放物是否活动)
        self.subscribers = set()
        self._task = None
        self._last_tick = time.monotonic()
        self._alarm_latched = False
        self._last_level = GREEN
        # 确认凭据：只在"按下一键就绪"那一刻用一次。它不再是一个可以被单独调档的
        # 权限开关 —— 能不能动完全由控制器状态说了算（见 controls.motion_granted）。
        self._challenge = None        # (token, 过期时刻)：一次性确认凭据
        self._armed = False           # 本次会话是否已经做过场地确认（换过凭据）
        self.last_deny = ""           # 上一次被闸挡下的原因，带回到接口响应里
        self._ack_until = 0.0         # 确认的有效期：过期后按就绪要重新勾一遍
        self.sim_state = {"fsm": 7, "enabled": 0, "brake": [0] * 6, "err_code": 0}
        # 手拖示教（Free Drive）：本机软件侧的按住状态 + 控制器回读
        self.free = {"on": False, "since": 0.0, "why": "", "hold": False, "source": ""}
        # 残影（预览目标）与"真机是否已站到残影上"的判定
        self.preview_q = None
        self._inpos_since = 0.0
        self.playback = telemetry.Playback()
        self._tele_at = 0.0         # 上一次落回放格子的时刻
        self.trend = deque(maxlen=600)      # 1Hz 趋势缓冲（约 10 分钟）
        # 断点续跑：把"跑到第几步"落盘，重启后能接着跑而不是从头再来。
        self.exec_file = config.DATA_DIR / "exec_state.json"
        self.resume = None                  # {state, i, total, steps, src}
        self._exec_key = None
        self._exec_at = 0.0
        self._load_resume()
        self._slow_at = 0.0         # 上一次算慢速遥测（体检/偏差/曲线）的时刻
        self.telemetry_seq = 0
        self._tele_cache = None
        # 主循环第一帧之前也可能有 HTTP 请求进来要快照，这里先给一个合法的空安全态，
        # 否则 snapshot() 会因为 self.safety 不存在直接抛 AttributeError。
        self.safety = {"level": GREEN, "max_pct": 0.0, "per_joint": [0.0] * len(self.model.names),
                       "collisions": [], "reasons": [], "capsules": []}
        self.ready_state = {"running": False, "done": False, "why": "", "steps": []}
        self._ready_task = None
        self._just_blackout = False     # 刚下过电：状态机还在往下走，立刻就绪会撞 20018
        self._boot_connect = None       # 启动时那次自动连接的任务，供快照如实报进度
        if config.no_real() and self.settings.get("mode") == "real":
            self.settings["mode"] = "sim"
            config.save_settings({"mode": "sim"})
            self.log.add("sys", "HUYAN_NO_REAL=1：本进程禁止连真机，强制仿真（自检脚本的隔离闸）")
        elif self.settings.get("mode") == "real" and not self.settings.get("auto_connect", True):
            # 用户明确要"打开默认真机自动连接，但可以关掉"。关掉就落回仿真并说明。
            self.settings["mode"] = "sim"
            config.save_settings({"mode": "sim"})
            self.log.add("sys", "自动连接已关闭：本次以仿真启动，要连真机去「驱动」里手动连接")
        self.log.add("sys", "模型 " + self.model.variant + " 载入，限位与几何来自官方 URDF")

    # ------------------------------------------------------------------ 生命周期
    def limits(self):
        """当前生效的限位：控制器软限位（手册 p54 = 协议 p69 示例）或 URDF 建模限位。"""
        src = self.settings["limit_source"]
        if src == "manual":
            cl = self.settings.get("custom_limits")
            if not cl or len(cl) != 6:
                return list(self.model.lower), list(self.model.upper)   # 没填就退回 URDF，不猜
            return [float(c[0]) for c in cl], [float(c[1]) for c in cl]
        label, pairs = config.LIMIT_SOURCES.get(src, config.LIMIT_SOURCES["controller"])
        if pairs is None:
            return list(self.model.lower), list(self.model.upper)
        return [p[0] for p in pairs], [p[1] for p in pairs]

    def _apply_limits(self):
        self.sim.lower, self.sim.upper = self.limits()

    def _vmax(self):
        if self.settings["vel_source"] == "manual":
            return list(config.MANUAL_VEL)
        return list(self.model.vmax)

    def motion_model(self):
        """使用当前确认工具的 TCP 建立运动学模型；未知工具按法兰处理。"""
        mounted = self.tools.get(self.tools.mounted.get("tool_id")) if getattr(self, "tools", None) else None
        tcp = (mounted or {}).get("tcp") or {}
        return RobotModel(self.model.variant,
                          tcp_translation_mm=tcp.get("translation_mm") or [0, 0, 0],
                          tcp_rpy_deg=tcp.get("rpy_deg") or [0, 0, 0])

    async def start(self):
        self._task = asyncio.create_task(self._loop())
        if not config.no_real() and self.settings.get("auto_connect", True) \
                and self.settings.get("mode") != "real":
            # 上次是手动断开才落在仿真的：开了自动连接就照样连回去
            self.settings["mode"] = "real"
        if self.settings.get("mode") == "real" and not config.no_real():
            # 开机即连：他要求"打开默认是真机自动连接"。连不上就自动落回仿真，
            # 不弹窗、不阻塞 —— 界面靠 real.stream_up/cmd_up 自己显示"--"。
            self._boot_connect = asyncio.create_task(self._boot_real())

    async def _boot_real(self):
        host = self.settings["robot_ip"]
        self.log.add("link", f"启动自动连接真机 {host}（可在设置里关掉）")
        try:
            await self.connect_real(host)
        except Exception as e:
            self.log.add("link", f"自动连接失败：{type(e).__name__} {e} —— 本次以仿真运行，"
                                 f"三维里的姿态是仿真值，不是真机")
            if self.settings.get("mode") == "real":
                await self.disconnect_real()

    async def stop(self):
        if self._speed_task:
            self._speed_task.cancel()
        await self.executor.cancel("服务关闭")
        for task in (self._ready_task, self._boot_connect):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self.real:
            await self.real.stop()

    def subscribe(self):
        q = asyncio.Queue(maxsize=1)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q):
        self.subscribers.discard(q)

    # ------------------------------------------------------------------ 主循环
    async def _loop(self):
        while True:
            now = time.monotonic()
            dt = min(0.1, now - self._last_tick)
            self._last_tick = now
            try:
                self._tick(dt, now)
                if now - self._broadcast_at >= 0.05:
                    self._broadcast_at = now
                    snap = self.snapshot()
                    for q in list(self.subscribers):
                        if q.full():
                            q.get_nowait()
                        q.put_nowait(snap)
            except Exception as e:                       # 单帧异常不能拖死主循环
                self.log.add("sys", f"主循环异常 {type(e).__name__}: {e}")
            await asyncio.sleep(max(0.001, 0.02 - (time.monotonic() - now)))

    def current_joints(self):
        if self.settings["mode"] == "real" and self.real and self.real.snap.get("joints"):
            return list(self.real.snap["joints"])
        return list(self.sim.q)

    def joints_known(self):
        """真机模式下、链路没通时，关节角其实是**未知**的。

        这时不能拿仿真的 0 去顶 —— 界面会显示成"机器人停在零位"，那是假数据。
        """
        if self.settings["mode"] != "real":
            return True
        return self._real_fresh()

    def _real_fresh(self):
        return bool(self.real and self.real.state_valid())

    def _load_resume(self):
        """开机读上次的执行进度。只报"停在哪"，绝不自动续跑 —— 那是硬件动作，必须人点。"""
        try:
            d = json.loads(self.exec_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if d.get('dynamic') and d.get('state') in ('running', 'waiting', 'paused', 'aborted'):
            self.resume = d
            self.log.add('program', '上次脚本已中断，需重新载入代码从头执行')
        elif (d.get("state") in ("running", "waiting", "paused", "aborted") and d.get("steps")
                and int(d.get("total", 0)) - int(d.get("i", 0)) > 0):
            d["left"] = int(d.get("total", 0)) - int(d.get("i", 0))
            self.resume = d
            self.log.add("program", f"上次执行停在第 {d['i']}/{d['total']} 步"
                                    f"（{d['state']}），还剩 {d['left']} 步 —— "
                                    f"要接着跑请在程序页点续跑")

    def _persist_exec(self, now):
        r = self.runner
        if not r.steps and r.state == "idle":
            return
        key = (r.state, r.i)
        if key == self._exec_key and now - self._exec_at < 1.0:
            return
        self._exec_key, self._exec_at = key, now
        try:
            write_json(self.exec_file,
                {"version": 2, "state": r.state, "i": r.completed_index, "total": len(r.steps),
                 "active_index": r.active_index, "requires_reconcile": r.active_index is not None,
                 'dynamic': bool(r.script_spec),
                 "steps": [] if r.script_spec else r.steps,
                 'source': r.script_spec['source'] if r.script_spec else None,
                 "updated": round(time.time(), 1)})
        except OSError as e:
            self.log.add("sys", f"执行进度落盘失败：{e}")

    def resume_last(self):
        """从上次停下的那一步接着跑（点位/程序/AI 计划都走这条）。"""
        if not self.resume:
            return {"ok": False, "why": "没有可续跑的进度"}
        if not self._need(self.MOTION, "续跑"):
            return {"ok": False, "why": self.last_deny}
        d = self.resume
        if d.get('dynamic'):
            return {'ok': False, 'why': '含变量和控制流的程序需重新载入，不能从部分步骤恢复变量状态'}
        if d.get("version") != 2 or d.get("requires_reconcile"):
            return {"ok": False, "why": "中断动作尚未核对，请检查现场后重新载入程序"}
        if self.executor.busy:
            return {"ok": False, "why": "已有任务执行中"}
        self.runner.load(d.get("steps") or [])
        self.runner.i = max(0, min(int(d.get("i", 0)), len(self.runner.steps)))
        self.runner.completed_index = self.runner.i
        self.runner.active_index = None
        self.runner.state = "running"
        self.runner.error = ""
        self.log.add("program", f"从第 {self.runner.i + 1}/{len(self.runner.steps)} 步续跑")
        return {"ok": True, "from": self.runner.i, "total": len(self.runner.steps)}

    def forget_resume(self):
        self.resume = None
        try:
            self.exec_file.unlink()
        except OSError:
            pass
        return {"ok": True}

    def _tick(self, dt, now):
        if self.settings["mode"] == "real" and not self._real_fresh():
            if not self._link_lost:
                self._link_lost = True
                self.executor.revoke("状态流失效，旧任务已撤销")
                self.runner.stop("状态流失效，需核对现场后重新执行")
                if self.real:
                    self.real.invalidate_pending("状态流失效")
                if self._ready_task and not self._ready_task.done():
                    self._ready_task.cancel()
                    self.ready_state.update(running=False, done=True, why="状态流失效，就绪流程已撤销")
            if now - self._tele_at >= .05:
                self._tele_at = now
                self.blackbox.append({"t": time.time(), "joints": self.current_joints(), "mode": "real",
                                      "valid": False, "fsm": None, "moving": None, "inpos": None,
                                      "err_code": None, "exec": self.runner.snapshot(),
                                      "epoch": self.executor.epoch, "log_seq": self.log.seq})
            return
        self._link_lost = False
        moving = False
        if self.settings["mode"] == "sim":
            self.sim.speed_pct = self.settings["sim_speed_pct"]
            moving = self.sim.tick(dt)
        else:
            moving = bool(self.real and self.real.snap.get("moving"))
        self._tick_preview(moving, now)
        self._tick_free(now)
        self._drive_executor(now, moving)
        lo, up = self.limits()
        q = self.current_joints()
        # 复用条件：姿态没变、设置没变、也没有任何摆放物在活动。三者缺一就必须重算，
        # 否则正在下落的箱子会被缓存冻在半空。
        key = (tuple(round(v, 4) for v in q), self._cfg_rev)
        # 三选一就必须重算：机器人在动、有摆放物还在掉、或姿态/设置变了。
        # 少了第一条的话，姿态不变而箱子下落就会被缓存冻在半空。
        if (not moving and not self.objects.any_active()
                and key == self._safe_key and self._safe_cache is not None):
            self.safety = self._safe_cache
        else:
            caps = self.model.capsules(q)
            # 摆放物是纯仿真道具：先按上一帧的机器人包络推进，再一起交给安全层判定
            self.obj_hits = self.objects.step(dt, caps) if caps else []
            self.safety = evaluate(self.model, q, self.settings, lo, up, caps=caps,
                                   obj_hits=self.obj_hits)
            self._safe_key, self._safe_cache = key, self.safety
        if self.safety["level"] == ALARM and not self._alarm_latched:
            self._alarm_latched = True
            self._on_alarm()
        elif self.safety["level"] != ALARM:
            self._alarm_latched = False
        if self.safety["level"] != self._last_level:
            self.log.add("alarm" if self.safety["level"] != GREEN else "event",
                         f"安全等级 -> {self.safety['level']}：" +
                         ("；".join(self.safety["reasons"]) or "全部正常"))
            if self.safety["level"] == ALARM:
                self.playback.mark(time.time(), "安全告警 " +
                                   ("；".join(self.safety["reasons"])[:60] or "触发"), "bad")
            self._last_level = self.safety["level"]
        # 回放缓冲：只按 20Hz 落格，5 分钟一循环，内存固定。
        if now - self._tele_at >= 0.05:
            self._tele_at = now
            self.playback.push(time.time(), q, self.safety["level"])
            st = self.live_state()
            self.blackbox.append({"t": time.time(), "joints": list(q), "mode": self.settings["mode"],
                                  "valid": self.joints_known(), "frame_seq": st.get("frame_seq"),
                                  "fsm": st.get("fsm"), "moving": moving, "inpos": st.get("inpos"),
                                  "err_code": st.get("err_code"), "end_di": st.get("end_di"),
                                  "end_do": st.get("end_do"), "exec": self.runner.snapshot(),
                                  "epoch": self.executor.epoch, "log_seq": self.log.seq})
        self._persist_exec(now)

    def _tick_preview(self, moving, now):
        """残影什么时候能收：真机六轴都进到目标 ±0.3° 以内、并且**站住 0.5s**、
        控制器还说没在动（真机侧 InPos=1 且 moving=0）才算重合。

        之前是"下发指令就立刻清残影"，结果假手一闪就没了，等于没预览。
        """
        p = self.preview_q
        if self.settings["mode"] == "real" and not self._real_fresh():
            self._inpos_since = 0.0
            return
        if not p:
            self._inpos_since = 0.0
            return
        q = self.current_joints()
        tol = float(self.settings.get("ghost_align_tol_deg", 0.3))
        aligned = all(abs(p[i] - q[i]) <= tol for i in range(6))
        settled = True
        if self.settings["mode"] == "real" and self.real:
            s = self.real.snap
            settled = s.get("moving") == 0 and s.get("inpos") == 1
        if aligned and settled and not moving:
            if not self._inpos_since:
                self._inpos_since = now
            elif now - self._inpos_since >= self.settings.get("ghost_align_hold_s", 0.5):
                self.log.add("event", f"真机已站上残影目标（六轴残差 ≤{tol}°），残影自动收掉")
                self.preview_q = None
        else:
            self._inpos_since = 0.0

    def _tick_free(self, now):
        """手拖示教的看门狗：按住才生效，松手/超时/越围栏/受力异常立刻退出。

        松闸意味着机械臂会被重力拽着走，所以"没人在按住"这个状态必须自己收回去，
        不能依赖前端一定会发 stop。
        """
        if not self.free["on"] or not self.free["hold"]:
            return
        if now - self.free["since"] > float(self.settings.get("free_hold_max_s", 8.0)):
            asyncio.create_task(self.free_drive(False, source="按住超时（本软件自设上限）"))
            return
        if self.safety["level"] == ALARM:
            asyncio.create_task(self.free_drive(False, source="触发安全告警，自动退出手拖"))
            return
        ft = (self.real.snap.get("ft_data") if self.real else None) or []
        lim = float(self.settings.get("free_force_max_n", 0.0))
        if lim and ft and max(abs(v) for v in ft[:3]) > lim:
            asyncio.create_task(self.free_drive(
                False, source=f"末端外力 {max(abs(v) for v in ft[:3]):.1f}N 超过设定 {lim:.0f}N"))

    def set_preview(self, joints):
        """残影的目标姿态（前端滑块拖出来的预设）。None = 收掉残影。"""
        if joints is None:
            self.preview_q = None
            self._inpos_since = 0.0
            return None
        if len(joints) != 6 or not all(math.isfinite(float(v)) for v in joints):
            raise ValueError("预览需要六个有限关节角")
        self.preview_q = [float(v) for v in joints]
        return self.preview_q

    def _on_alarm(self):
        """100% 或碰撞：仿真自动暂停；真机只告警 + 停点动，不替你决定后续动作。"""
        if self.settings["mode"] == "sim":
            self.sim.paused = True
            self.runner.stop("安全告警自动暂停")
            self.queue_state = "aborted"
            self.queue_step = None
        else:
            self.executor.revoke("运行时安全告警")
            asyncio.create_task(self.real.jog_end()) if self.real else None
            self.runner.stop("安全告警，真机侧已停点动")
        self.log.add("alarm", "触发 100%/碰撞级：已自动停止执行，需人工复位后继续")

    # ------------------------------------------------------------------ 执行推进
    def _drive_executor(self, now, busy):
        if self.executor.state == "failed" and self.runner.active_index is not None and self.runner.state in ("running", "waiting"):
            self.runner.stop(self.executor.error)
        self.runner.busy = busy or self.executor.busy or self.queue_state == "running"
        # advance() 自己会区分 running/waiting；这里不能再加状态判断，否则 WAIT 计时器永不复查
        step = self.runner.advance(now)
        if step:
            self._run_step(step)

    def _run_step(self, step):
        k = step["kind"]
        async def run(epoch):
            if not self._need(self.MOTION, "程序步骤"):
                raise RuntimeError(self.last_deny)
            if k == "movej":
                await self._execute_move(step["joints"], step["src"], epoch)
            elif k == "movel":
                await self._execute_movel(step["pose"], step["src"], epoch)
            elif k == "speed":
                self.set_speed(step["pct"], schedule=False)
                if self.settings["mode"] == "real":
                    await self._real_set_override(step["pct"])
                    self.executor.check(epoch)
                    if self.speed_ack.get("ok") is not True:
                        raise RuntimeError(self.speed_ack.get("why") or "速度未确认")
            elif k == "tool_action":
                reply = await self.tool_action(step["action"], internal=True)
                self.executor.check(epoch)
                if not reply.get("ok"):
                    raise RuntimeError(reply.get("why", "工具动作失败"))
            elif k == "require_tool":
                mounted_id = self.tools.mounted.get("tool_id")
                tool = self.tools.get(mounted_id) if mounted_id else None
                if not tool or mounted_id != step["tool_id"]:
                    raise RuntimeError(f"当前工具不是要求的 {step['tool_id']}，程序已停止")
            elif k == "dout":
                reply = await self.write_alias(step["alias"], step["val"]) if step.get("alias") else await self.write_end_do(step["bit"], step["val"])
                self.executor.check(epoch)
                if not reply.get("ok"):
                    raise RuntimeError(str(reply))
            elif k == "wait_di":
                await self._wait_di(step, epoch)
            elif k == "if_di":
                if self._read_di(step["alias"]) == step["val"]:
                    self.runner.i = step["jump"]
        if not self.executor.submit(step["src"], run, self.runner.complete):
            self.runner.stop("已有任务执行中")
        self.log.add("program", f"执行第 {step['line']} 行：{step['src']}")

    def precheck(self, target, model=None):
        """动手之前先静态算一次风险，只给**警告**，不拦指令。

        2026-10-12 用户口径："不能拒绝用户的指令"。所以这里从"预检未通过就中止"
        降级成"照发，但把风险写进日志和回执"。真正的保护留在运行时：
        逐帧安全评估到告警级会自动停（_on_alarm），那是撞了才停，不是不许你发。
        """
        lo, up = self.sim.lower, self.sim.upper
        warns = []
        for i, (t, a, b) in enumerate(zip(target, lo, up)):
            if not (a <= t <= b):
                warns.append("J" + str(i + 1) + " 目标 " + format(t, ".3f") + "° 超出限位 ["
                             + format(a, ".1f") + ", " + format(b, ".1f") + "]（控制器会自己挡，"
                             + "软件不替你决定）")
        st = evaluate(model or self.model, target, self.settings, lo, up)
        if st["level"] == ALARM:
            kinds = "、".join(sorted({c["type"] for c in st["collisions"]})) or "越限"
            warns.append("目标位姿会触发 " + kinds + "（占用 " + format(st["max_pct"], ".1f")
                         + "%）—— 到位过程中安全层到告警级会自动停")
        return "；".join(warns) or None

    def _move_to(self, target, src=""):
        warn = self.precheck(target)
        if warn:
            self.log.add("alarm", "预检警告（照发，不拦）：" + warn)
        return self.executor.submit(src or "MOVEJ", lambda epoch: self._execute_move(target, src, epoch))

    async def _execute_move(self, target, src, epoch):
        self.executor.check(epoch)
        if self.settings["mode"] == "sim":
            # MOVEJ is one synchronized joint trajectory. The legacy real
            # MoveRelJ adapter is a stop/wait compatibility protocol, not a
            # model of the controller's continuous multi-axis trajectory.
            self.sim.move_j(target)
            while self.sim.traj:
                self.executor.check(epoch)
                await asyncio.sleep(0.02)
            self.executor.check(epoch)
        elif self.settings.get("real_move_cmd") == "movejto":
            await self._real_movejto(target, epoch)
        else:
            await self._real_rel_step(target, src, epoch)

    async def _execute_movel(self, target_pose, src, epoch):
        """仿真笛卡尔直线：按 TCP 位姿插值，逐段 IK 后驱动同步关节轨迹。

        真机暂不冒充已验证的 MoveLTo；需要厂家回执和现场路径资格后再接入。
        """
        if self.settings["mode"] != "sim":
            raise RuntimeError("MOVEL 真机通道尚未完成 MoveLTo 资格验证")
        if len(target_pose) != 6 or not all(math.isfinite(float(v)) for v in target_pose):
            raise RuntimeError("MOVEL 目标必须是六个有限数")
        model = self.motion_model()
        q = self.current_joints()
        start = model.pose_vec(q)
        distance = math.sqrt(sum((target_pose[i] - start[i]) ** 2 for i in range(3)))
        orientation_delta = max(abs(float(target_pose[i]) - start[i]) for i in range(3, 6))
        if distance < 0.5 and orientation_delta < 0.2:
            self.log.add("program", "MOVEL 目标已在当前位置：无可见位移")
            return
        steps = max(2, min(120, math.ceil(distance / 8.0)))
        lo, up = self.limits()
        for index in range(1, steps + 1):
            self.executor.check(epoch)
            t = index / steps
            pose = [start[i] + (float(target_pose[i]) - start[i]) * t for i in range(6)]
            q, emm, edeg, converged = model.ik(q, pose, lo, up, iters=80, max_restarts=2)
            if not converged:
                raise RuntimeError(f"MOVEL 第 {index}/{steps} 段逆解未收敛：{emm:.2f}mm/{edeg:.2f}°")
            self.sim.move_j(q)
            while self.sim.traj:
                self.executor.check(epoch)
                await asyncio.sleep(0.02)
        self.log.add("program", f"MOVEL 仿真完成：{steps} 个笛卡尔采样段")

    async def _real_movejto(self, target, epoch=None):
        """协议 p198 的绝对点动。文档有，但我还没在这台控制器上验证过，
        所以默认不走这条路（设置里显式切到 movejto 才用）。"""
        link = self.real
        connection = link.epoch
        stamp = link.snap.get("stamp", 0)
        after_seq = link.frame_seq
        rep = await link.ask("MoveJTo", 0, *[format(v, ".3f") for v in target])
        if not rep.ok:
            raise RuntimeError("MoveJTo 被控制器拒绝 code=" + str(rep.err_code))
        if not await self._wait_motion_idle(link=link, epoch=epoch, after=stamp, after_seq=after_seq, target=target, connection=connection):
            raise RuntimeError("MoveJTo 到位未确认")

    # 单条 MoveRelJ 的幅度上限。分段是为了"每段都能观察、能中断"，不是为了限制行程 ——
    # 分段数给到 240，任何关节行程都跑得满。
    REL_CHUNK_DEG = 10.0
    REL_MAX_CHUNKS = 240
    REL_SETTLE_TIMEOUT_S = 120.0      # 5% 速度下走完 10° 要十几秒，别把超时设得比运动还短
    REL_START_S = 0.35                # 发完到控制器真的开始动，中间有一小段回执/调度间隙

    async def _wait_motion_idle(self, timeout=None, *, link=None, epoch=None, after=0, target=None, connection=None, after_seq=None):
        """等真机把上一条运动跑完（moving=0 且 InPos=1）。

        2026-10-12 实测：控制器正在运动时再发 MoveRelJ 直接回 20018。
        旧代码发完只 sleep(0.15) 就发下一段，于是第一段之后的所有分段全部被拒，
        表现为"没到残影就停了"。
        """
        timeout = timeout or self.REL_SETTLE_TIMEOUT_S
        link = link or self.real
        if self.settings["mode"] != "real" or not link:
            return False
        connection = link.epoch if connection is None else connection
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if epoch is not None:
                self.executor.check(epoch)
            if self.real is not link or link.epoch != connection or not link.state_valid() or not link.cmd_up:
                raise RuntimeError("控制器状态已失效或连接已变化")
            s = link.snap
            aligned = target is None or all(abs(a - b) <= 0.05 for a, b in zip(target, s["joints"]))
            new_frame = link.frame_seq > after_seq if after_seq is not None else s.get("stamp", 0) > after
            if s.get("moving") == 0 and s.get("inpos") == 1 and new_frame and aligned:
                return True
            await asyncio.sleep(0.1)
        return False

    async def _real_rel_step(self, target, src="", epoch=None):
        """逐段逼近目标角：发一段 → 等它真停下 → 再发下一段。"""
        link = self.real
        if not link:
            raise RuntimeError("真机未连接")
        connection = link.epoch
        for n in range(self.REL_MAX_CHUNKS):
            if epoch is not None:
                self.executor.check(epoch)
            if self.real is not link or link.epoch != connection:
                raise RuntimeError("连接已变化，旧运动已撤销")
            if not await self._wait_motion_idle(link=link, epoch=epoch, connection=connection):
                raise RuntimeError("等真机停下超时")
            cur = self.current_joints()
            pend = [(i, target[i] - cur[i]) for i in range(6) if abs(target[i] - cur[i]) > 0.05]
            if not pend:
                self.log.add("point", f"已站上目标（{n} 段）：" + (src or ""))
                return
            i, d = max(pend, key=lambda x: abs(x[1]))      # 先走差得最多的那根轴
            step = max(-self.REL_CHUNK_DEG, min(self.REL_CHUNK_DEG, d))
            stamp = link.snap.get("stamp", 0)
            after_seq = link.frame_seq
            expected = list(cur)
            expected[i] += step
            rep = await link.ask("MoveRelJ", 0, i, 1 if step > 0 else 0, f"{abs(step):.3f}")
            if not rep.ok:
                raise RuntimeError(f"J{i + 1} 运动被控制器拒绝 code={rep.err_code} "
                                   f"{rep.as_dict().get('hint') or ''}")
            if not await self._wait_motion_idle(link=link, epoch=epoch, after=stamp, after_seq=after_seq, target=expected, connection=connection):
                raise RuntimeError("分段运动到位超时，剩余分段没有发")
        raise RuntimeError("分段次数用尽，目标仍未到位")

    # ------------------------------------------------------------------ 对外动作
    # ------------------------------------------------------------------ 许可与电源
    # 只有两档：只读 / 运动。上电·下电·使能·清错·停止不设闸 ——
    # "让它停下来"的动作加锁反而危险，出事时还得先过一遍确认框。
    PERM_NAMES = {0: "readonly", 1: "motion"}
    READONLY, MOTION = 0, 1
    CHALLENGE_TTL_S = 60.0        # 确认凭据的一次性有效期
    ARM_TTL_S = 900.0             # 场地确认记住时长：15 分钟内按就绪不用再勾一遍

    def live_state(self):
        """当前驱动模式下的状态读数。仿真那一路用 sim_state 顶替，字段名对齐，
        这样前端只有一套渲染逻辑，不会出现"仿真页显示不全"的精简克隆。"""
        if self.settings["mode"] == "real" and self.real:
            return self.real.snap
        return self.sim_state

    def motion_ok(self):
        """运动许可 == 机器人真的在就绪+使能态。软件里没有任何开关能绕过这条。"""
        st = self.live_state()
        if self.settings["mode"] == "sim":
            return True
        if self.settings["mode"] == "real" and (not self._real_fresh() or not self.real.cmd_up):
            return False
        return controls.motion_granted(st.get("fsm"), st.get("enabled"))

    @property
    def perm(self):
        """兼容旧调用点：许可等级是**推导**出来的，不是存出来的。"""
        return self.MOTION if self.motion_ok() else self.READONLY

    def _challenge_ok(self, token):
        """一次性凭据：用掉就作废，过期也作废。确认框的意义在于"人真的点过"，
        所以它必须不能被复用、不能被猜出来。"""
        if not token or not self._challenge:
            return False
        want, expires = self._challenge
        self._challenge = None
        return secrets.compare_digest(str(token), str(want)) and time.monotonic() < expires

    def begin_motion_ack(self, acks=None):
        """前端勾完两项确认后来换凭据；没勾全就不发。"""
        acks = acks or {}
        if not (acks.get("nobody") and acks.get("estop")):
            return {"ok": False, "why": "两项确认必须都勾：范围内无人无障碍、能拍到物理急停"}
        self._challenge = (secrets.token_urlsafe(18), time.monotonic() + self.CHALLENGE_TTL_S)
        self._armed = True
        self._ack_until = time.monotonic() + self.ARM_TTL_S
        self.log.add("perm", "已签发运动确认凭据（一次性，" + str(int(self.CHALLENGE_TTL_S))
                             + "s 内有效；场地确认记住 " + str(int(self.ARM_TTL_S // 60)) + " 分钟）")
        return {"ok": True, "challenge": self._challenge[0], "ttl_s": int(self.CHALLENGE_TTL_S)}

    def need_ack_for(self, key):
        """只有"把机器人推进到可运动状态"的动作要确认凭据；收回类动作永远不确认。"""
        if self.settings["mode"] == "sim":
            return False          # 仿真不动硬件，反复弹确认只会让人学会不看内容
        if key not in ("ready", "electrify", "start_master"):
            return False
        return not (self._armed and time.monotonic() < self._ack_until)

    def ctrl(self):
        """主按钮与状态徽标：前端只渲染这个结果，不再自己算状态机。"""
        link = None
        if self.settings["mode"] == "real":
            link = {"stream_up": self._real_fresh(),
                    "cmd_up": bool(self.real and self.real.cmd_up),
                    "host": self.real.host if self.real else None}
        c = controls.derive(self.settings["mode"], link, self.live_state(), self.settings)
        c["need_ack"] = self.need_ack_for((c.get("primary") or {}).get("key"))
        c["ack_left_s"] = int(max(0.0, self._ack_until - time.monotonic())) if self._armed else 0
        c["ready_running"] = bool(self._ready_task) and not self._ready_task.done()
        return c

    async def act(self, key, challenge=None):
        """主按钮的唯一入口：先验状态、再验凭据，最后才发指令。"""
        c = self.ctrl()
        cur = (c.get("primary") or {}).get("key")
        if key == "connect":
            await self.connect_real()
            return {"ok": True, "ctrl": self.ctrl()}
        if key not in ("cancel_ready", "reset", "stop", "electrify", "start_master", "ready"):
            return {"ok": False, "why": "未知动作 " + key}
        if key != cur:
            # 状态在按下去那一刻已经变了：不猜意图，如实告诉他现在该按什么
            return {"ok": False, "changed": True, "ctrl": c,
                    "why": "状态已变化：当前主操作是「" + c["primary"]["label"] + "」——"
                           + c["primary"]["why"] + "，这次没有执行。"}
        if key == "stop":
            return await self.stop_execution("人工停止")
        if key == "reset":
            return await self.power("reset")
        if key == "cancel_ready":
            return await self.cancel_ready()
        if self.need_ack_for(key) and not self._challenge_ok(challenge):
            self.log.add("interlock", "已拒绝 " + key + "：缺少或已过期的确认凭据"
                                      "（必须在前端勾完两项确认换取，不能直接调接口）")
            return {"ok": False, "need_ack": True,
                    "why": "需要先在确认框勾完两项拿到一次性凭据；直接调接口不算确认。"}
        if key in ("electrify", "start_master"):
            name, _p, want = controls.STEP_CMD[key]
            label = {"electrify": "上电", "start_master": "启动主控"}[key]
            if self.settings["mode"] == "sim":
                return self._power_sim({"Electrify": "electrify",
                                        "StartMaster": "init"}[name], want, label)
            rep = await self.real.ask(name)
            if not rep.ok and rep.err_code == 20018:
                # 状态还在过渡（实测下电后 FSM 走 14→6→7，这期间发什么都回 20018）：
                # 等它落定再补发一次，而不是把"被拒"直接甩给用户。
                settled = await self._wait_fsm_settle(timeout=10.0)
                self.log.add("power", f"{name} 被 20018 拒（状态过渡），等落定后 FSM={settled}，补发一次")
                rep = await self.real.ask(name)
            if not rep.ok and rep.timeout and (self.live_fsm() or 0) < want:
                # 回执超时不等于没生效：先看状态有没有走过去，走过了就按成功报
                after = await self._wait_for_fsm(want, timeout=6.0)
                if after is not None and after >= want:
                    self.log.add("power", f"{name} 回执超时，但 FSM 已到 {after} —— 按成功处理，"
                                          f"不谎报失败")
                    rep.ok = True
            self.log.add("ready", f"{name} -> {'OK' if rep.ok else rep.as_dict()}，"
                                  f"现在 FSM={self.live_fsm()}")
            return {"ok": rep.ok, "reply": rep.as_dict(), "ctrl": self.ctrl()}
        # ready：走泵，泵内部按 FSM 逐步推进
        return await self.ready()

    # ------------------------------------------------------------------ 手拖示教
    async def free_drive(self, on, hold=False, source="软件按钮"):
        """GrpOpenFreeDriver / GrpCloseFreeDriver（协议 p26 / p25）。

        这是手册 §5.11.5 说的 **Basic Free Drive**：松闸、但伺服不给重力补偿，
        所以机械臂会往下坠 —— 三条纪律由此而来：
          * 只在"就绪+使能"态允许开（FSM=33），否则现场看到的是自由落体；
          * 开着手拖时任何运动指令一律拒发（见 _need），不会一边松闸一边跑轨迹；
          * 松手 / 超时 / 触发告警 / 受力异常 自动关，看门狗在 _tick_free。
        仿真模式没有真实抱闸，只走状态流并在读数上标【仿真】。
        """
        if not on:
            if not self.free["on"]:
                return {"ok": True, "why": "本来就没在手拖状态，无需关闭", "free": dict(self.free)}
            self.free.update(on=False, hold=False, since=0.0, source="")
            if self.settings["mode"] == "sim":
                self.log.add("event", "【仿真】退出手拖示教")
                return {"ok": True, "free": dict(self.free), "sim": True}
            if not self.real or not self.real.cmd_up:
                self.log.add("free", "命令口已断开：软件侧手拖状态已清除，"
                                     "控制器那侧请以 FSM/抱闸读数为准")
                return {"ok": False, "free": dict(self.free), "why": "命令通道未连接"}
            rep = await self.real.ask("GrpCloseFreeDriver", 0, urgent=True, timeout=4.0)
            self.free["why"] = "" if rep.ok else self._fail_text(rep)
            self.log.add("free", "退出手拖（来源=" + source + "）："
                                 + ("OK" if rep.ok else str(rep.as_dict())))
            return {"ok": rep.ok, "reply": rep.as_dict(), "free": dict(self.free)}

        if self.free["on"]:
            self.free["hold"] = hold
            if hold:
                self.free["since"] = time.monotonic()      # 续按：把看门狗往前推
            return {"ok": True, "free": dict(self.free), "why": "已在手拖状态，只刷新按住计时"}
        if self.settings["mode"] == "sim":
            self.free.update(on=True, hold=hold, since=time.monotonic(), source=source,
                             why="【仿真】没有真实抱闸可松，这里只演示状态流")
            self.log.add("event", "【仿真】进入手拖示教（不发真机指令）")
            return {"ok": True, "free": dict(self.free), "sim": True}
        if self.executor.busy or self.runner.state in ("running", "waiting"):
            return {"ok": False, "why": "先停止任务再进入手拖", "free": dict(self.free)}
        st = self.live_state()
        if not self.motion_ok():
            return {"ok": False, "free": dict(self.free),
                    "why": "手拖需要就绪+使能态（当前 FSM=" + str(st.get("fsm")) +
                           "、enabled=" + str(st.get("enabled")) + "）：松闸必须在伺服上电的"
                           "前提下做，否则机械臂会被重力直接拽下去"}
        rep = await self.real.ask("GrpOpenFreeDriver", 0)
        if rep.ok:
            self.free.update(on=True, hold=hold, since=time.monotonic(), source=source, why="")
            self.runner.stop("进入手拖示教，程序执行已中止")
            self.preview_q = None
            self.log.add("free", "已进入手拖示教（Basic Free Drive：松闸、无重力补偿）。"
                                 "此刻任何运动指令都会被拒；松开按键或超时自动退出")
        else:
            self.free["why"] = self._fail_text(rep)
            self.log.add("free", "手拖被控制器拒绝：" + str(rep.as_dict()))
        return {"ok": rep.ok, "reply": rep.as_dict(), "free": dict(self.free)}

    def _need(self, level, what):
        """运动类动作的唯一闸：只看控制器实际状态，没有任何"软件里勾一下"能绕过它。"""
        if level == self.MOTION:
            st = self.live_state()
            if not self.motion_ok():
                why = ("当前不是就绪+使能态（FSM=" + str(st.get("fsm")) + "、enabled="
                       + str(st.get("enabled")) + "），系统处于只读。要动请按主按钮「一键就绪」")
                self.log.add("interlock", "已拒绝 " + what + "：" + why)
                self.last_deny = why
                return False
            if self.free["on"]:
                why = "手拖示教进行中：松闸状态下不下发运动指令，请先退出手拖"
                self.log.add("interlock", "已拒绝 " + what + "：" + why)
                self.last_deny = why
                return False
        self.last_deny = ""
        return True

    # 这里原来有 _guard_motion / _guard_axis 两道闸（可动轴白名单、单步角度上限）。
    # 2026-10-12 用户明确要求拆掉："我可以移动所有轴啊，不要限制用户操作真机。"
    # 软件不替操作者决定能动哪根轴、能走多大、能多快；对**我自己**跑脚本的限制在 tools/hy.py。

    # 电源时序按真机实测：已使能时直接 BlackOut 会回 20018，必须先 GrpDisable
    POWER_SEQ = {
        "electrify": ("Electrify", (), 14, "上电（只通 48V，抱闸仍吸合）"),
        "init": ("ConnectToBox", (), 24, "连接电箱 / 初始化"),
        "enable": ("GrpEnable", (0,), 33, "使能"),
        "disable": ("GrpDisable", (0,), 24, "去使能"),
        "blackout": ("BlackOut", (), 7, "下电（断 48V）"),
        "reset": ("GrpReset", (0,), None, "清错"),
        "stop": ("GrpStop", (0,), None, "停止运动"),
        "standby": ("XToStandby", (0,), 33, "回 Standby"),
        # 2026-10-12 实测：14→24 这一步是 StartMaster 做的，不是 ConnectToBox
        "start_master": ("StartMaster", (), 24, "启动主控（14→24，实测可用）"),
    }

    async def power(self, action):
        if action not in self.POWER_SEQ:
            return {"ok": False, "why": "未知电源动作 " + action}
        name, params, want, label = self.POWER_SEQ[action]
        if action == "stop":
            return await self.stop_execution("人工停止")
        if action in ("disable", "blackout"):
            await self.stop_execution(label)
        # 上电/下电/使能/清错/停止不设许可闸：这是用户明确要的口径，
        # 而且"让它停下来/把电断掉"这类动作加锁只会更危险。
        if self.settings["mode"] == "sim":
            return self._power_sim(action, want, label)
        if not self.real or not self.real.cmd_up:
            return {"ok": False, "why": "命令通道未连接"}
        if action == "blackout" and self.real.snap.get("enabled"):
            return {"ok": False, "why": "真机实测：已使能时不能直接下电（20018），请先去使能"}
        rep = await self.real.ask(name, *params)
        if action == "blackout" and rep.ok:
            self._just_blackout = True
        return {"ok": rep.ok, "reply": rep.as_dict(), "label": label}

    def _power_sim(self, action, want, label):
        st = self.sim_state
        cur = st["fsm"]
        if action == "electrify" and cur >= 14:
            return {"ok": False, "why": "仿真：当前 FSM=" + str(cur) + " 已过上电阶段"}
        if action == "init" and cur < 14:
            return {"ok": False, "why": "仿真：要先上电"}
        if action == "enable" and cur < 24:
            return {"ok": False, "why": "仿真：要先完成初始化(FSM24)"}
        if action == "blackout" and st["enabled"]:
            return {"ok": False, "why": "仿真：已使能时不能直接下电，先去使能（与真机一致）"}
        if want is not None:
            st["fsm"] = want
        if action == "enable":
            st["enabled"], st["brake"] = 1, [1] * 6
        elif action in ("disable", "blackout"):
            st["enabled"], st["brake"] = 0, [0] * 6
            self.sim.stop()
            if action == "blackout":
                self._just_blackout = True
        elif action == "reset":
            st["err_code"] = 0
        self.log.add("event", "【仿真】" + label + " -> FSM=" + str(st["fsm"])
                     + " enabled=" + str(st["enabled"]))
        return {"ok": True, "sim": True, "state": dict(st)}

    # ------------------------------------------------------------------ 就绪时序泵
    # 与桌面 PyQt6 面板同一套判据：状态优先、逐步推进、超时即停
    # 2026-10-12 在这台 E05 上逐条实测：Electrify 能把 7→14，StartMaster 能把 14→15→24，
    # GrpEnable 能把 24→33。ConnectToBox 在 FSM=14 实测回 20018，所以它不进自动链
    # （之前"网络侧推不动 14→24"的结论是我漏试了 StartMaster，现已更正）。
    READY_STEPS = (("Electrify", 14, (), "上电", 0),
                   ("StartMaster", 24, (), "启动主控", 0),
                   ("GrpEnable", 33, (0,), "使能", 0))
    READY_TIMEOUT_S = 30.0
    READY_POLL_S = 0.4
    READY_RETRY_S = 1.8        # 撞 20018 后的退避间隔：状态过渡期重发一次往往就过了
    READY_RETRY_MAX = 3
    SETTLE_S = 1.5             # 认为 FSM "落定"所需的稳定时长
    SIM_ACTION = {"Electrify": "electrify", "StartMaster": "init", "ConnectToBox": "init",
                  "GrpEnable": "enable"}

    def live_fsm(self):
        if self.settings["mode"] == "real":
            return self.real.snap.get("fsm") if self._real_fresh() else None
        return self.sim_state["fsm"]

    async def ready(self, then_enable=True):
        """按 FSM 目标值逐步推进（上电 → 启动主控 → 使能）；返回 {ok, steps, why}。

        已经在跑的流程不重复起。FSM 未知时不盲发，先等 Data Sheet 来帧。
        """
        if getattr(self, "_ready_task", None) and not self._ready_task.done():
            return {"ok": False, "why": "就绪流程已在进行中"}
        if self.settings["mode"] == "real" and (not self.real or not self.real.cmd_up):
            return {"ok": False, "why": "命令通道未连接：现在连指令都发不出去"}
        fsm = self.live_fsm()
        if fsm is None:
            return {"ok": False, "why": "还没收到控制器状态上报（FSM 未知），不盲发"}
        self._ready_task = asyncio.create_task(self._ready_pump(then_enable))
        return {"ok": True, "started": True, "from_fsm": fsm}

    async def power_down(self, reason="下电"):
        """去使能 → 下电。顺序不能跳：已使能时直接 BlackOut，真机实测回 20018。"""
        steps = []
        await self.stop_execution(reason)
        if self.settings["mode"] == "sim":
            for a in ("disable", "blackout"):
                r = self._power_sim(a, self.POWER_SEQ[a][2], self.POWER_SEQ[a][3])
                steps.append([self.POWER_SEQ[a][3], bool(r.get("ok")),
                              "OK" if r.get("ok") else (r.get("why") or "失败")])
                if not r.get("ok"):
                    break
            self._just_blackout = True
            return {"ok": all(x[1] for x in steps), "steps": steps, "sim": True}
        if not self.real or not self.real.cmd_up:
            return {"ok": False, "why": "命令通道未连接"}
        if self.real.snap.get("enabled"):
            rep = await self.real.ask("GrpDisable", 0)
            steps.append(["去使能", rep.ok, "OK" if rep.ok else self._fail_text(rep)])
            if not rep.ok:
                return {"ok": False, "steps": steps}
            # 去使能之后 FSM 要从 33 往下走，这段过渡期里 BlackOut 实测回 20018
            # （2026-10-12 就卡在这：取消就绪报"下电=20018"）。所以先等它落定再断 48V。
            await self._wait_fsm_settle(timeout=8.0)
        else:
            steps.append(["去使能", True, "本来未使能，跳过"])
        rep = await self.real.ask("BlackOut")
        if not rep.ok and (rep.err_code == 20018 or rep.timeout):
            # 状态还没走完：退避重发一次，别把"下不去电"直接甩给用户
            self.log.add("power", f"BlackOut 被拒（{self._fail_text(rep)}），等状态落定后重发一次")
            await self._wait_fsm_settle(timeout=6.0)
            rep = await self.real.ask("BlackOut")
        steps.append(["下电", rep.ok, "OK" if rep.ok else self._fail_text(rep)])
        self._just_blackout = True
        return {"ok": all(x[1] for x in steps), "steps": steps}

    @staticmethod
    def _fail_text(rep):
        """回执失败的简短说明。Reply 对象没有 hint 属性（hint 是 as_dict 里现算的），
        以前这里直接写 rep.hint，一走到失败分支就 AttributeError 把接口炸掉。"""
        d = rep.as_dict()
        if d.get("timeout"):
            return "回执超时"
        return (f"{d.get('err_code')} {d.get('hint') or ''}".strip() or "失败")

    async def cancel_ready(self):
        """取消就绪 = 停掉推进泵，然后按 去使能 → 下电 收回去。"""
        had = bool(self._ready_task) and not self._ready_task.done()
        if had:
            self._ready_task.cancel()
        prefix = "已停止就绪流程；" if had else "就绪流程本来没在跑；"
        self.ready_state.update(running=False, done=True, why=prefix + "正在去使能并下电…")
        r = await self.power_down("取消就绪")
        detail = "，".join(f"{n}={'OK' if ok else why}" for n, ok, why in r.get("steps", []))
        if r.get("ok"):
            msg = prefix + "已去使能并下电。" + (detail or "")
        else:
            msg = prefix + "收回流程没走完：" + (r.get("why") or detail) + "。控制器停在哪一步看 FSM 读数。"
        self.ready_state.update(why=msg)
        self.log.add("ready", msg)
        return {"ok": bool(r.get("ok")), "why": msg, "steps": r.get("steps", [])}

    async def _wait_for_fsm(self, want, timeout=8.0):
        """等 FSM 走到 >= want；到点没到就返回最后读到的值（不猜、不假装到了）。"""
        end = time.monotonic() + timeout
        cur = self.live_fsm()
        while time.monotonic() < end:
            cur = self.live_fsm()
            if cur is not None and cur >= want:
                return cur
            await asyncio.sleep(0.2)
        return self.live_fsm()

    async def _wait_fsm_settle(self, timeout=10.0):
        """等 FSM 连续 SETTLE_S 秒不再变化。刚下电/刚上电时它要跳几档，
        这时候发指令会撞 20018，所以先等它自己走完。"""
        end = time.monotonic() + timeout
        last, stable_since = self.live_fsm(), time.monotonic()
        while time.monotonic() < end:
            await asyncio.sleep(0.2)
            cur = self.live_fsm()
            if cur != last:
                last, stable_since = cur, time.monotonic()
            elif cur is not None and time.monotonic() - stable_since >= self.SETTLE_S:
                return cur
        return self.live_fsm()

    async def _ready_pump(self, then_enable):
        link = self.real if self.settings["mode"] == "real" else None
        connection = link.epoch if link else None
        try:
            await self._ready_pump_body(then_enable, link, connection)
        except asyncio.CancelledError:
            self.ready_state.update(running=False, done=True, why="就绪流程已取消")
            raise
        except RuntimeError as exc:
            self.ready_state.update(running=False, done=True, why=str(exc))

    def _ready_check(self, link, connection):
        if link:
            if self.real is not link or link.epoch != connection or not link.state_valid() or not link.cmd_up:
                raise RuntimeError("连接或状态已变化，就绪流程已撤销")
        elif self.settings["mode"] != "sim":
            raise RuntimeError("模式已变化，就绪流程已撤销")

    async def _ready_ask(self, name, params, link, connection):
        self._ready_check(link, connection)
        rep = await link.ask(name, *params)
        self._ready_check(link, connection)
        return rep

    async def _ready_pump_body(self, then_enable, link, connection):
        steps = [x for x in self.READY_STEPS if (then_enable or x[0] != "GrpEnable")]
        self.ready_state = {"running": True, "done": False, "why": "",
                            "steps": [{"cmd": n, "want": w, "label": lab,
                                       "at": "等待中"} for n, w, _p, lab, _perm in steps]}
        self.log.add("ready", "就绪流程：" + " → ".join(
            x[0] + "(目标FSM" + str(x[1]) + ")" for x in steps))
        if self.settings["mode"] == "real" and (self._just_blackout
                                                or (self.live_fsm() or 0) == 6):
            # 刚下过电、或正停在 6 这个下电过渡档：这时候发任何状态指令都是 20018
            self.ready_state["steps"][0]["at"] = "等 FSM 落定"
            settled = await self._wait_fsm_settle()
            self._just_blackout = False
            self.log.add("ready", f"等状态机落定：FSM={settled}，开始推进")
        for i, (name, want, params, label, perm) in enumerate(steps):
            st = self.ready_state["steps"][i]
            t0 = time.monotonic()
            sent = False
            while time.monotonic() - t0 < self.READY_TIMEOUT_S:
                self._ready_check(link, connection)
                fsm = self.live_fsm()
                if fsm is None:          # 状态未知就发指令 = 盲发，真机侧可能直接动
                    st["at"] = "等状态上报"
                    self.log.add("ready", "FSM 未知（Data Sheet 还没来帧），先等，不盲发")
                    await asyncio.sleep(self.READY_POLL_S)
                    continue
                if fsm >= want:
                    st["at"] = "FSM=" + str(fsm) + " 已达 " + str(want)
                    self.log.add("ready", name + "：FSM=" + str(fsm) + " 已达目标 " + str(want)
                                 + ("，跳过发送" if not sent else ""))
                    break
                if sent:
                    await asyncio.sleep(self.READY_POLL_S)
                    continue
                if self.settings["mode"] == "sim":
                    r = self._power_sim(self.SIM_ACTION[name], want, label)
                else:
                    rep = await self._ready_ask(name, params, link, connection)
                    r = {"ok": rep.ok, "why": rep.as_dict()}
                if not r.get("ok"):
                    code = (r.get("why") or {}).get("err_code") if isinstance(r.get("why"), dict) else None
                    if code == 20018:
                        # 状态不允许。判据只有当前 FSM：先看是不是别的端已经把状态推进了，
                        # 再退避重发几次（状态过渡期重发往往就过了）。
                        tries = 0
                        while tries < self.READY_RETRY_MAX:
                            grace = time.monotonic() + self.READY_RETRY_S
                            while time.monotonic() < grace:
                                await asyncio.sleep(0.3)
                                if (self.live_fsm() or 0) >= want:
                                    break
                            if (self.live_fsm() or 0) >= want:
                                st["at"] = "被别端推进了，FSM=" + str(self.live_fsm())
                                sent = True
                                break
                            tries += 1
                            st["at"] = f"20018，退避重发 {tries}/{self.READY_RETRY_MAX}"
                            await asyncio.sleep(0.2)
                            if self.settings["mode"] == "sim":
                                r = self._power_sim(self.SIM_ACTION[name], want, label)
                            else:
                                rep = await self._ready_ask(name, params, link, connection)
                                r = {"ok": rep.ok, "why": rep.as_dict()}
                            if r.get("ok"):
                                sent = True
                                break
                            if (r.get("why") or {}).get("err_code") != 20018:
                                break
                        if sent:
                            await asyncio.sleep(self.READY_POLL_S)
                            continue
                        st["at"] = "20018 被当前状态拒绝"
                        fsm_now = self.live_fsm()
                        self.ready_state.update(running=False, done=True,
                            why=f"{label}（{name}）被控制器拒绝：错误码 20018 当前状态不允许。"
                                f"当前 FSM={fsm_now}，本步要求推进到 FSM≥{want}。"
                                f"已退避重发 {self.READY_RETRY_MAX} 次仍被拒 —— "
                                f"实测这台机器 7→14 用 Electrify、14→24 用 StartMaster、"
                                f"24→33 用 GrpEnable 都能过；被拒通常是别的端（示教器/另一个程序）"
                                f"已经把这一步做过了，或者状态正处在过渡档。先看 FSM 读数再决定重按。")
                        self.log.add("ready", self.ready_state["why"])
                        return
                    st["at"] = "失败"
                    why = r.get("why")
                    if isinstance(why, dict):        # 真机回执
                        detail = ("回执超时" if why.get("timeout")
                                  else "错误码 " + str(why.get("err_code")) + " "
                                  + (why.get("hint") or ""))
                    else:
                        detail = str(why or "未知")
                    self.ready_state.update(running=False, done=True,
                                            why=label + " 失败：" + detail)
                    self.log.add("ready", self.ready_state["why"])
                    return
                st["at"] = "已发送，等 FSM 到位"
                sent = True
                await asyncio.sleep(self.READY_POLL_S)
            else:
                st["at"] = "超时"
                self.ready_state.update(running=False, done=True,
                                        why=name + " 后 " + str(self.READY_TIMEOUT_S)
                                        + "s 内 FSM 仍为 " + str(self.live_fsm())
                                        + "，流程中止 —— 请在示教器确认当前状态（FSM 语义表缺失，不替你猜）")
                self.log.add("ready", self.ready_state["why"])
                return
        self.ready_state.update(running=False, done=True, why="")
        self.log.add("ready", "就绪流程完成：FSM=" + str(self.live_fsm()))

    def set_speed(self, pct, target=None, schedule=True):
        """改速度。target=None 时按当前驱动模式落到 sim_speed_pct 或 real_speed_pct。

        真机那一路会顺带下发 SetOverride（协议 p72，0.01~1.0）；下发结果原样记在
        speed_ack 里 —— 成功与否以控制器回执为准，本地记录不代表控制器已经生效。
        """
        pct = round(max(1.0, min(100.0, float(pct))), 1)
        key = config.speed_key(target if target in ("sim", "real") else self.settings["mode"])
        self._cfg_rev += 1
        self.settings[key] = pct
        config.save_settings({key: pct})
        self.sim.speed_pct = self.settings["sim_speed_pct"]
        self.log.add("speed", f"{key} -> {pct}%")
        if key == "real_speed_pct" and self.settings["mode"] == "real":
            self.speed_ack = {"target": "real", "req": pct, "ok": None, "why": "等待下发"}
            if schedule:
                if self._speed_task and not self._speed_task.done():
                    self._speed_task.cancel()
                self._speed_task = asyncio.create_task(self._real_set_override(pct, self.real, self.real.epoch if self.real else None))
        elif key == "real_speed_pct":
            self.speed_ack = {"target": "real", "req": pct, "ok": None,
                              "why": "当前是仿真模式：只存值，没往控制器发 SetOverride"}
        else:
            self.speed_ack = {"target": "sim", "req": pct, "ok": True, "why": "仿真插值速度，不涉及控制器"}
        return pct

    async def _real_set_override(self, pct, requested_link=None, requested_epoch=None):
        if requested_link is not None and (self.real is not requested_link or self.real.epoch != requested_epoch):
            return
        if not self.real or not self.real.cmd_up:
            self.speed_ack.update(ok=False, why="命令通道未连接，只存了本地值，控制器速度未改动")
            self.log.add("speed", self.speed_ack["why"])
            return
        if not self.motion_ok():
            self.speed_ack.update(ok=False, why="只读态（未就绪+使能）：不下发 SetOverride，"
                                              "控制器速度未改动")
            self.log.add("speed", self.speed_ack["why"])
            return
        link = self.real
        connection, stamp = link.epoch, link.snap.get("stamp", 0)
        after_seq = link.frame_seq
        rep = await link.ask("SetOverride", 0, f"{pct / 100.0:.4f}")
        self.speed_ack.update(
            ok=rep.ok,
            outcome=rep.outcome,
            why="" if rep.ok else ("回执超时" if rep.timeout
                                   else self._fail_text(rep)))
        if rep.ok:
            end = time.monotonic() + 2.0
            while time.monotonic() < end:
                if self.real is not link or link.epoch != connection or not link.state_valid():
                    self.speed_ack.update(ok=False, outcome="unknown", why="连接已变化，实际倍率未确认")
                    return
                actual = link.snap.get("override")
                if link.frame_seq > after_seq and actual is not None and abs(actual - pct / 100.) <= 0.0005:
                    self.speed_ack.update(ok=True, outcome="confirmed", actual_pct=actual * 100.)
                    return
                await asyncio.sleep(0.02)
            self.speed_ack.update(ok=False, outcome="unknown", why="已收到回执，但实际倍率未确认")
            return
        if not rep.ok:
            self.log.add("speed", "SetOverride 未确认：" + self.speed_ack["why"]
                         + "（控制器当前 override 见状态读数）")

    def update_settings(self, patch):
        self._cfg_rev += 1
        self.settings = config.save_settings(patch)
        self.sim.set_vmax(self._vmax(), self.settings["vel_source"])
        self.sim.speed_pct = self.settings["sim_speed_pct"]
        self._apply_limits()
        self.log.add("sys", f"设置更新：{','.join(sorted(patch))}")
        return self.settings

    def tool_snapshot(self):
        return self.tools.snapshot()

    def select_tool(self, tool_id, source="manual", hardware_id=None, reason=""):
        if self.executor.busy or self.runner.state in ("running", "waiting"):
            return {"ok": False, "why": "作业执行中，必须先停止再切换工具"}
        if self.settings["mode"] == "real" and not self._real_fresh():
            return {"ok": False, "why": "真机状态已过期，无法确认停稳；工具只能在有效状态下切换"}
        try:
            snap = self.tools.select(tool_id, source, hardware_id, reason)
        except ValueError as e:
            return {"ok": False, "why": str(e)}
        self.preview_q = None
        self.plans.clear()
        self._cfg_rev += 1
        self.log.add("tool", f"工具切换为 {snap['tool']['name']}（来源：{source}），旧残影和轨迹已失效")
        return {"ok": True, **snap}

    def clear_tool(self, reason="用户确认无工具"):
        if self.executor.busy or self.runner.state in ("running", "waiting"):
            return {"ok": False, "why": "作业执行中，必须先停止再确认工具状态"}
        if self.settings["mode"] == "real" and not self._real_fresh():
            return {"ok": False, "why": "真机状态已过期，无法确认停稳"}
        snap = self.tools.clear(reason)
        self.preview_q = None
        self.plans.clear()
        self._cfg_rev += 1
        self.log.add("tool", "已确认法兰无工具，旧残影和轨迹已失效")
        return {"ok": True, **snap}

    async def tool_action(self, action, duration_ms=0, internal=False):
        if not internal and (self.executor.busy or self.runner.state in ("running", "waiting")):
            return {"ok": False, "why": "作业执行中，不能手动切换末端工具"}
        tool, mapping = self.tools.action_mapping(action)
        if not tool:
            return {"ok": False, "why": "尚未确认实际安装工具"}
        if tool.get("type") == "none":
            return {"ok": False, "why": "当前确认的是无工具"}
        if self.settings["mode"] == "real":
            if not self._real_fresh():
                return {"ok": False, "why": "真机状态已过期，拒绝工具动作"}
            if not mapping:
                return {"ok": False, "why": f"工具动作 {action} 尚未完成真实 IO 映射，未发送硬件指令"}
            reply = await self.write_alias(mapping["alias"], mapping["value"])
            if not reply.get("ok"):
                return {"ok": False, "why": reply.get("why", "工具 IO 写入失败"), "reply": reply}
            snap = self.tools.record_action(action, "active" if mapping["value"] else "idle", reply)
            self.log.add("tool", f"真机工具动作：{action} → {mapping['alias']}={mapping['value']}")
            return {"ok": True, "sim": False, **snap}
        state = "active" if action in {"close", "vacuum_on", "attach"} else "idle"
        snap = self.tools.record_action(action, state, {"sim": True, "duration_ms": duration_ms})
        self.log.add("tool", f"仿真工具动作：{action}（仅虚拟状态）")
        return {"ok": True, "sim": True, "note": "仿真模式：未向硬件发送指令", **snap}

    async def set_mode(self, mode):
        if mode not in ("sim", "real"):
            return False
        if mode == self.settings["mode"]:
            return True
        self.update_settings({"mode": mode})
        if mode == "real":
            await self.connect_real()
        else:
            await self.disconnect_real()
        return True

    async def connect_real(self, ip=None):
        if config.no_real():
            self.log.add("interlock", "HUYAN_NO_REAL=1：本进程不允许连控制器（自检脚本隔离闸），"
                                      "连接请求已拒")
            return False
        if self.real:
            await self.stop_execution("重新连接")
            await self.real.stop()
        host = ip or self.settings["robot_ip"]
        self.update_settings({"mode": "real"})     # 不切模式会出现"链路是真机、姿态是仿真"的假数据
        self.real = RealLink(host, log=lambda k, m: self.log.add(k, m))
        await self.real.start()
        self.log.add("link", f"正在连接真机 {host}，三维姿态改由 10004 取流驱动")
        return True

    async def disconnect_real(self):
        await self.stop_execution("断开控制器")
        if self.real:
            await self.real.stop()
            self.real = None
        self.update_settings({"mode": "sim"})
        self.log.add("link", "已断开真机，回到仿真模式")

    async def jog(self, axis, direction, on):
        """点动：仿真直接改速度状态；真机走 LongJogJ + 心跳。"""
        if not on:
            self.sim.end_jog()
            if self.real:
                rep = await self.real.jog_end()
                return {"ok": rep.ok, "reply": rep.as_dict()}
            return {"ok": True}
        if self.executor.busy or self.runner.state in ("running", "waiting"):
            return {"ok": False, "why": "作业执行中，请先停止"}
        if not self._need(self.MOTION, "点动"):
            return {"ok": False, "why": self.last_deny}
        if self.settings["mode"] == "sim":
            if on:
                self.sim.start_jog(axis, direction)
            else:
                self.sim.end_jog()
            return {"ok": True}
        if not self.real:
            return {"ok": False, "why": "真机未连接"}
        rep = await (self.real.jog_start(axis, direction) if on else self.real.jog_end())
        return {"ok": rep.ok, "reply": rep.as_dict()}

    async def move_joints(self, joints):
        if self.executor.busy or self.runner.state in ("running", "waiting"):
            return {"ok": False, "why": "已有任务执行中，请先停止"}
        if not self._need(self.MOTION, "手动 MOVEJ"):
            return {"ok": False, "why": self.last_deny}
        warn = self.precheck(list(joints))
        accepted = self._move_to(list(joints), src="手动 MOVEJ")
        return {"ok": accepted, "accepted": accepted, "warn": warn,
                "execution": self.executor.snapshot()}

    async def run_points(self, ids):
        if self.executor.busy or self.runner.state in ("running", "waiting"):
            return {"ok": False, "why": "已有任务执行中"}
        if not self._need(self.MOTION, "点位执行"):
            return {"ok": False, "why": self.last_deny}
        steps = []
        for pid in ids:
            p = self.points.get(pid)
            if not p:
                return {"ok": False, "why": f"点位 {pid} 不存在"}
            steps.append({"kind": "movej", "joints": p["joints"], "point": p["name"],
                          "line": 0, "src": f"MOVEJ {p['name']}"})
        self.runner.load(steps)
        self.runner.start()
        self.log.add("point", f"开始执行 {len(steps)} 个点位")
        return {"ok": True, "count": len(steps)}

    async def load_program(self, src, params=None):
        if self.executor.busy or self.runner.state in ("running", "waiting"):
            return {"ok": False, "why": "请先停止当前任务再载入程序"}
        try:
            steps = program.parse(src, self.points.resolve, params=params, point_items=self.points.list())
        except program.ProgramError as e:
            self.log.add("program", f"解析失败：{e}")
            return {"ok": False, "error": str(e)}
        self.runner.load(steps)
        self.log.add('program', '脚本语法检查通过，步骤由运行时生成' if self.runner.script_spec else f'解析成功，共 {len(steps)} 步')
        return {"ok": True, "steps": steps}

    async def write_end_do(self, bit, val):
        if not self._need(self.MOTION, "末端 DO 写入"):
            return {"ok": False, "why": self.last_deny}
        if self.settings["mode"] == "sim":
            self.sim_io["end_do"][bit] = val
            return {"ok": True, "note": "仿真模式：只记录，不驱动硬件"}
        rep = await self.real.ask("SetEndDO", 0, bit, val)
        return {"ok": rep.ok, "reply": rep.as_dict()}

    def _read_di(self, alias):
        channel = self.calibrations.items["io"].get(alias)
        if not channel or channel.get("direction") != "input":
            raise ValueError("输入别名尚未标定：" + alias)
        key = "end_di" if channel["group"] == "end" else "box_di"
        if self.settings["mode"] == "real":
            if not self._real_fresh():
                raise RuntimeError("输入数据已失效")
            data = self.real.snap.get(key)
        else:
            data = self.sim_io[key]
        bit = channel["bit"]
        if not data or bit >= len(data) or data[bit] not in (0, 1):
            raise RuntimeError("输入通道未知：" + alias)
        return data[bit]

    async def write_alias(self, alias, val):
        channel = self.calibrations.items["io"].get(alias)
        if not channel or channel.get("direction") != "output":
            return {"ok": False, "why": "输出别名尚未标定：" + alias}
        if not self._need(self.MOTION, "IO别名写入"):
            return {"ok": False, "why": self.last_deny}
        if channel["group"] == "end":
            return await self.write_end_do(channel["bit"], val)
        if self.settings["mode"] == "sim":
            self.sim_io["box_do"][channel["bit"]] = val
            return {"ok": True, "sim": True}
        reply = await self.real.ask("SetBoxDO", channel["bit"], val)
        return {"ok": reply.ok, "reply": reply.as_dict()}

    async def _wait_di(self, step, epoch):
        end, stable = time.monotonic() + step["timeout"], None
        calibration_version = self.calibrations.items["version"]
        connection = self.real.epoch if self.real else None
        while time.monotonic() < end:
            self.executor.check(epoch)
            if self.calibrations.items["version"] != calibration_version:
                raise RuntimeError("IO映射已变化，握手已撤销")
            if self.real and self.real.epoch != connection:
                raise RuntimeError("连接已变化，IO握手已撤销")
            if self._read_di(step["alias"]) == step["val"]:
                stable = stable or time.monotonic()
                if time.monotonic() - stable >= step["debounce"]:
                    return
            else:
                stable = None
            await asyncio.sleep(.02)
        raise RuntimeError("等待输入超时：" + step["alias"])

    def plan_context(self):
        # 只绑定会改变运动含义的工程状态；IP、主题、语音和自动连接偏好不应让
        # 操作者刚确认的 AI 计划无故失效。真机连接代次仍然必须绑定。
        motion_settings = {k: self.settings.get(k) for k in (
            "mode", "limit_source", "custom_limits", "fence", "self_collision",
            "ground_enabled", "vel_source", "real_move_cmd")}
        tool = self.tools.mounted if getattr(self, "tools", None) else {}
        return digest({"settings": motion_settings, "points": self.points.list(),
                       "objects": self.objects.list(), "calibration_version": self.calibrations.items.get("version", 0),
                       "tool": {"tool_id": tool.get("tool_id"), "revision": tool.get("revision", 0)},
                       "connection": self.real.epoch if self.settings["mode"] == "real" and self.real else None})

    async def inspect_plan(self, target):
        if not self.joints_known() or self.objects.any_active():
            return {"ok": False, "why": "等待有效姿态和静止的场景后再检查轨迹"}
        start, context = self.current_joints(), self.plan_context()
        settings, objects = copy.deepcopy(self.settings), self.objects.list()
        lower, upper = self.limits()
        model = self.motion_model()
        report = await asyncio.to_thread(inspect_path, model, start, target, settings, lower, upper, objects)
        if context != self.plan_context() or any(abs(a-b) > .05 for a, b in zip(start, self.current_joints())):
            return {"ok": False, "why": "检查期间起点或场景变化，请重新检查"}
        plan_id = secrets.token_urlsafe(18)
        self.plans[plan_id] = {"context": context, "start": start, "target": list(target),
                               "expires": time.monotonic() + 120, "report": report}
        while len(self.plans) > 32:
            self.plans.pop(next(iter(self.plans)))
        return {"ok": True, "plan_id": plan_id, "report": report, "ttl_s": 120}

    async def execute_plan(self, plan_id):
        plan = self.plans.pop(plan_id, None)
        if not plan or time.monotonic() > plan["expires"] or plan["context"] != self.plan_context():
            return {"ok": False, "why": "轨迹检查已过期或场景已变化，请重新检查"}
        if not self.joints_known() or any(abs(a-b) > .05 for a, b in zip(plan["start"], self.current_joints())):
            return {"ok": False, "why": "当前起点已变化，请重新检查"}
        return await self.move_joints(plan["target"])

    def blackbox_bundle(self):
        return {"version": 1, "session_id": self.session_id, "frames": list(self.blackbox),
                "events": self.log.tail(2000), "execution": self.executor.snapshot(),
                "commands": list(self.real.command_trace) if self.real else [],
                "note": "只读回放，不向硬件重放命令；姿态有效性逐帧记录"}

    async def estop(self):
        if self._armed:
            # 急停之后场地确认作废：想再动必须重新勾一遍确认，这是对操作者的保护
            self.log.add("perm", "急停后场地确认作废（要再动必须重新确认）")
            self._armed = False
            self._ack_until = 0.0
            self._challenge = None
        self.preview_q = None
        if self.free["on"]:
            # 松闸状态遇到急停必须收回去，否则机器停在半空、人以为还能推
            asyncio.create_task(self.free_drive(False, source="急停"))
        return await self.stop_execution("急停")

    async def stop_execution(self, reason="人工停止"):
        task = self.executor.revoke(reason)
        if self._speed_task:
            self._speed_task.cancel()
        self.runner.stop(reason)
        self.sim.stop()
        self.queue_state = "aborted"
        if self.real:
            self.real.invalidate_pending(reason)
        for ready_task in (self._ready_task,):
            if ready_task and not ready_task.done():
                ready_task.cancel()
        if task and task is not asyncio.current_task():
            try:
                await task
            except asyncio.CancelledError:
                pass
        link = self.real
        stamp = link.snap.get("stamp", 0) if link else 0
        reply = await link.estop() if link else None
        settled = not link
        if link and reply.ok:
            connection = link.epoch
            end = time.monotonic() + .5
            while time.monotonic() < end:
                if self.real is not link or link.epoch != connection:
                    break
                if link.state_valid() and link.snap.get("stamp", 0) > stamp and link.snap.get("moving") == 0 and link.snap.get("inpos") == 1:
                    settled = True
                    break
                await asyncio.sleep(.02)
        self.stop_result = {"ok": bool(reply.ok) if reply else True,
                            "reply": reply.as_dict() if reply else None,
                            "settled": settled,
                            "why": reason}
        self.log.add("alarm", reason, self.stop_result)
        return self.stop_result

    def start_program(self):
        if self.executor.busy or not self._need(self.MOTION, "程序启动"):
            return {"ok": False, "why": self.last_deny or "已有任务执行中"}
        return {"ok": self.runner.start(), **self.runner.snapshot()}

    async def ask_real(self, name, *params):
        if self.settings["mode"] != "real" or not self.real:
            return {"ok": False, "why": "当前不是真机模式"}
        return (await self.real.ask(name, *params)).as_dict()

    def ik_pose(self, target6, seed=None, allow_partial=False):
        """笛卡尔目标 → 关节角。只给"离当前位形最近的一组解"，且必须过预检才算可用。

        不收敛时如实返回 ok=False 带上真实残差 —— 绝不能拿一组差 300mm 的解去动机器。
        """
        if len(target6) != 6:
            return {"ok": False, "why": "目标需要 X,Y,Z,A,B,C 六个数"}
        if not self.joints_known():
            return {"ok": False, "why": "当前姿态未知，不能选择运动学初值"}
        cur = self.current_joints()
        lo, up = self.limits()
        model = self.motion_model()
        goal = [float(v) for v in target6]

        initial = list(seed) if seed is not None else list(cur)
        if len(initial) != 6 or not all(math.isfinite(float(v)) for v in initial + goal):
            return {"ok": False, "why": "目标和初始关节角必须是六个有限数值"}
        q, emm, edeg, conv = model.ik(initial, goal, lo, up,
                                     iters=60 if allow_partial else 100,
                                     max_restarts=4 if allow_partial else 8)
        residual = (emm, edeg)
        fraction = 1.0
        requested = goal[:]
        if not conv and allow_partial:
            # Slider preview explicitly permits clipping toward the last valid
            # pose. Never load an inaccurate IK result as though it reached the
            # requested target: solve a reachable intermediate pose instead.
            initial = [max(lo[i], min(up[i], float(v))) for i, v in enumerate(initial)]
            start = model.pose_vec(initial)
            low, high = 0.0, 1.0
            valid_q, valid_goal = initial, start
            for _ in range(8):
                t = (low + high) / 2
                trial = [start[i] + (requested[i]-start[i])*t for i in range(6)]
                cq, cp, cr, cc = model.ik(valid_q, trial, lo, up, iters=35, restarts=False)
                if cc:
                    low, valid_q, valid_goal = t, cq, trial
                else:
                    high = t
            if low > .002:
                q, goal, fraction, conv = valid_q, valid_goal, low, True
                e = model._err(q, goal)
                emm, edeg = max(abs(v) for v in e[:3]), max(abs(v) for v in e[3:])
        if not conv:
            # Preview dragging reports inline; it must not flood the audit log
            # with interlocks for every intermediate pointer position.
            if not allow_partial:
                self.log.add("interlock", f"逆解未收敛：残差 {emm:.1f}mm / {edeg:.1f}°，不载入")
            return {"ok": False, "converged": False, "pos_err_mm": round(emm, 3),
                    "rot_err_deg": round(edeg, 3),
                    "why": "当前轴方向未找到有效解；已保留上一组有效目标。可先调整其他轴或关节姿态后再试。"}
        # 解出来就交给前端，风险只标注不拦：拦不拦是你的决定，不是软件的。
        warn = self.precheck(q, model=model)
        if warn and not allow_partial:
            self.log.add("alarm", "逆解结果有静态风险（已载入，不拦）：" + warn)
        elif not allow_partial:
            self.log.add("event", f"逆解收敛：残差 {emm:.3f}mm / {edeg:.3f}°")
        return {"ok": True, "converged": True, "approx": False, "joints": [round(v, 6) for v in q],
                "achieved": model.pose_vec(q), "requested": requested, "fraction": fraction,
                "clipped": fraction < 1.0,
                "requested_pos_err_mm": round(residual[0], 4), "requested_rot_err_deg": round(residual[1], 4),
                "pos_err_mm": round(emm, 4), "rot_err_deg": round(edeg, 4), "warn": warn,
                "note": "已沿当前位形求解；多解情况下不保证全局最近"
                        + ("；滑块已退回可求解的中间目标，尚未到达原请求" if fraction < 1 else "")
                        + ("；有静态风险见 warn，执行与否你定" if warn else "")}

    def fk_pose(self, joints):
        """关节角正解，供点动界面同步显示 TCP。"""
        if len(joints) != 6:
            return {"ok": False, "why": "关节角需要六个数"}
        try:
            pose = self.motion_model().flange_pose([float(v) for v in joints])
        except (TypeError, ValueError) as exc:
            return {"ok": False, "why": str(exc)}
        return {"ok": True, **pose}

    async def explain_error(self, note=""):
        """故障码解说：读当前 Error_Code → 向控制器要官方字符串 → 让模型给处置建议。

        模型的输出**永远标注成"AI 建议"**，控制器的原始回执一起返回，两者不混。
        """
        st = self.live_state()
        code = st.get("err_code") or 0
        raw = None
        if self.settings["mode"] == "real" and self.real and self.real.cmd_up:
            rep = await self.real.ask("GetErrorCodeStr", code)
            raw = rep.as_dict()
        ctx = {"code": code, "axis": st.get("err_axis"), "fsm": st.get("fsm"),
               "enabled": st.get("enabled"), "controller_str":
                   (raw or {}).get("fields") if raw else None,
               "axis_err": st.get("axis_err"), "temps": st.get("temps"),
               "note": note}
        if not code:
            return {"ok": True, "code": 0, "advice": "", "ctx": ctx,
                    "why": "当前没有故障码（Error_Code=0）。这条只是把控制器状态读给你看。"}
        return await ai_plan.explain(ctx, config.zhipu_key(),
                                     log=lambda k, m: self.log.add(k, m))

    async def io_probe(self, group="end", bit=0, val=1):
        """IO 标定向导：单路点亮 → 采样前后 DI/AI 变化 → 把差异如实列出来。

        只写用户点的那一路，不盲扫全部通道（盲扫会把吸盘、气阀一起激活）。
        测不出差异就写"没测出"，不编映射表。
        """
        if self.settings["mode"] != "real" or not self.real:
            return {"ok": False, "why": "仿真模式没有真实 IO：这条只能在真机上做"}
        if not self.motion_ok():
            return {"ok": False, "why": "IO 写入需要就绪+使能态（当前只读）"}
        if group not in ("end", "box") or int(bit) < 0:
            return {"ok": False, "why": "IO 探测通道无效"}
        link = self.real
        connection = link.epoch
        cmd = "SetEndDO" if group == "end" else "SetBoxDO"
        before = {k: self.real.snap.get(k) for k in
                  ("end_di", "end_ai", "box_di", "box_ci", "end_button")}
        do_key = "end_do" if group == "end" else "box_do"
        original_do = list(link.snap.get(do_key) or [])
        if int(bit) >= len(original_do) or original_do[int(bit)] not in (0, 1):
            return {"ok": False, "why": "探测前无法确认该输出状态，拒绝盲目恢复"}
        r1 = await self.real.ask(cmd, 0, bit, val) if group == "end" else await self.real.ask(cmd, bit, val)
        if not r1.ok:
            return {"ok": False, "why": f"{cmd} 被拒：{r1.as_dict()}"}
        r0 = None
        restore_ok = False
        try:
            await asyncio.sleep(0.4)
            mid = {k: self.real.snap.get(k) for k in before}
        finally:
            # 只允许向探测时的同一连接恢复，禁止重连后误写另一台控制器。
            if self.real is link and link.epoch == connection:
                restore_val = int(original_do[int(bit)])
                r0 = await (link.ask(cmd, 0, bit, restore_val) if group == "end"
                            else link.ask(cmd, bit, restore_val))
                restore_ok = bool(r0 and r0.ok)
        await asyncio.sleep(0.4)
        after = {k: self.real.snap.get(k) for k in before}
        diff = []
        for k in before:
            b, m, a = before[k], mid[k], after[k]
            if isinstance(b, list) and isinstance(m, list) and isinstance(a, list) and len(b) == len(m):
                for i, (x, y) in enumerate(zip(b, m)):
                    if x != y:
                        diff.append({"ch": f"{k}[{i}]", "off": x, "on": y,
                                     "back": a[i] if i < len(a) else None})
        return {"ok": restore_ok, "restore_ok": restore_ok,
                "written": {"group": group, "bit": bit, "cmd": cmd},
                "off_reply": r1.as_dict(), "on_off_reply": r0.as_dict(),
                "diff": diff,
                "why": ("点亮 " + str(group) + " 通道 " + str(bit) + " 时观察到 "
                        + str(len(diff)) + " 处输入变化" +
                        ("；没有变化说明这一路没有可回读的到位信号，或它压根不驱动外设"
                         if not diff else ""))}

    def diag_bundle(self):
        """一键诊断包：状态 + 设置 + 最近事件 + 最近一帧原始 Data Sheet + 设备标识。

        脱敏：SN/MAC/授权码默认打码（现场排查有时要完整的，用 full=1 显式要）。
        """
        st = copy.deepcopy(self.live_state())
        raw = copy.deepcopy(self.real.last_raw) if self.real else {}
        identity = raw.get("RobotAuthorization", {})
        for key in ("DeviceSN", "DeviceMac", "DynDeviceCode", "LangnDeviceCode", "LangAuthData", "CheckSum"):
            if key in identity:
                identity[key] = "***"
        for k in ("device_sn", "device_mac", "device_code", "lang_auth", "LangnDeviceCode"):
            v = st.get(k) or (raw.get("RobotAuthorization", {}) or {}).get(k)
            if v:
                st[k] = str(v)[:4] + "***"
                if isinstance(raw.get("RobotAuthorization"), dict):
                    raw["RobotAuthorization"] = dict(raw["RobotAuthorization"])
        return {"generated": round(time.time(), 1), "ctrl": self.ctrl(),
                "state": {k: st.get(k) for k in FAST_KEYS + TELE_KEYS if k in st},
                "settings": {k: v for k, v in self.settings.items() if k != "ai_base_url"},
                "raw_frame": raw, "safety": {k: v for k, v in self.safety.items()
                                             if k != "capsules"},
                "events": self.log.tail(200), "playback": self.playback.summary(),
                "dev": self.model.variant}

    async def ai(self, text, history):
        context, start = self.plan_context(), self.current_joints()
        res = await ai_plan.make_plan(text, history, copy.deepcopy(self.points.list()), self.model,
                                      copy.deepcopy(self.settings), config.zhipu_key(),
                                      log=lambda k, m: self.log.add(k, m))
        if res.get("ok"):
            if context != self.plan_context() or any(abs(a-b) > .05 for a,b in zip(start,self.current_joints())):
                return {"ok": False, "error": "生成期间起点或场景变化，请重新生成"}
            plan_id = secrets.token_urlsafe(18)
            self.plans[plan_id] = {"context": context, "plan_hash": digest(res["plan"]),
                                   "expires": time.monotonic() + 120, "start": start}
            while len(self.plans) > 32:
                self.plans.pop(next(iter(self.plans)))
            res["plan_id"] = plan_id
            res["ttl_s"] = 120
        return res

    async def ai_run(self, plan, plan_id=None):
        """AI 计划必须人工确认后才走这里；执行前逐条复查白名单与限位。"""
        if self.executor.busy or self.runner.state in ("running", "waiting"):
            return {"ok": False, "why": "已有任务执行中"}
        if not self._need(self.MOTION, "AI 计划执行"):
            return {"ok": False, "why": self.last_deny}
        proposed = self.plans.get(plan_id)
        if not proposed:
            return {"ok": False, "why": "找不到这份 AI 计划，请重新生成并确认"}
        if time.monotonic() > proposed["expires"]:
            self.plans.pop(plan_id, None)
            return {"ok": False, "why": "AI计划已过期（有效期 120 秒），请重新生成并确认"}
        if proposed.get("plan_hash") != digest(plan):
            return {"ok": False, "why": "确认内容与生成的 AI 计划不一致，请重新生成并确认"}
        if proposed["context"] != self.plan_context():
            self.log.add("interlock", "AI计划上下文变化：工程运动状态已改变")
            return {"ok": False, "why": "AI计划对应的工程状态已变化（点位、场景、工具、模式或限位），请重新生成并确认"}
        if any(abs(a-b) > .05 for a,b in zip(proposed["start"], self.current_joints())):
            return {"ok": False, "why": "AI计划起点已变化，请重新生成"}
        try:
            plan = ai_plan.validate({"plan": plan, "summary": "人工确认后的计划", "warnings": []},
                                    self.points.list(), self.model)["plan"]
        except ValueError as e:
            return {"ok": False, "why": f"计划复查未通过：{e}"}
        self.plans.pop(plan_id, None)
        # 静态复查：模型说"能到"不算数，逐条拿我们自己的正解 + 碰撞包络再算一遍。
        # 但**不拦指令**（用户 2026-10-12 口径："不能拒绝用户的指令"）—— 有问题就带着
        # 结构化原因照发，前端可以拿这份原因去让模型重规划，运行时安全层该停还是会停。
        issues = []
        cur = self.current_joints()
        for n, o in enumerate(plan):
            if o.get("op") != "movej":
                continue
            bad = self.precheck(list(o["joints"]))
            if bad:
                issues.append({"step": n, "op": "movej",
                               "target": [round(v, 2) for v in o["joints"]],
                               "from": [round(v, 2) for v in cur], "why": bad})
            cur = list(o["joints"])
        if issues:
            self.log.add("ai", f"计划有 {len(issues)} 处静态风险，仍按你的确认执行：" +
                               "；".join(f"第{i['step'] + 1}步 {i['why']}" for i in issues))
        steps = []
        for o in plan:
            if o["op"] == "speed":
                steps.append({"kind": "speed", "pct": o["pct"], "line": 0, "src": f"SPEED {o['pct']}"})
            elif o["op"] == "movej":
                steps.append({"kind": "movej", "joints": o["joints"], "line": 0,
                              "src": f"MOVEJ {o.get('point') or o['joints']}"})
            elif o["op"] == "dout":
                steps.append({"kind": "dout", "bit": o["bit"], "val": o["val"], "line": 0,
                              "src": f"DOUT {o['bit']},{o['val']}"})
            else:
                steps.append({"kind": "wait", "sec": o["sec"], "line": 0, "src": f"WAIT {o['sec']}"})
        self.runner.load(steps)
        self.runner.start()
        self.log.add("ai", f"人工确认，开始执行 AI 计划 {len(steps)} 步")
        return {"ok": True, "count": len(steps), "issues": issues,
                "replan_hint": issues[0]["why"] if issues else ""}

    # ------------------------------------------------------------------ 快照
    def snapshot(self, force_tele=False):
        q = self.current_joints()
        pose = self.motion_model().flange_pose(q)
        real = None
        if self.settings["mode"] == "real" and not self.real:
            # 模式是真机但链路对象不存在：明确报断开，别让界面一片 -- 让人以为在读数
            real = {k: None for k in FAST_KEYS}
            real.update(stream_up=False, cmd_up=False, host=None, update_time="")
        elif self.real:
            snapd = self.real.snap
            real = {"stream_up": self.real.stream_up, "cmd_up": self.real.cmd_up,
                    "connection_epoch": self.real.epoch, "frame_seq": self.real.frame_seq,
                    "quality": "valid" if self._real_fresh() else "stale",
                    "lag_ms": self.real.lag_ms(), "host": self.real.host,
                    **{k: snapd.get(k) for k in FAST_KEYS}}
        out = {
            "t": round(time.time(), 3), "mode": self.settings["mode"], "config_rev": self._cfg_rev,
            "session_id": self.session_id,
            "speed_pct": self.settings[config.speed_key(self.settings["mode"])],
            "sim_speed_pct": self.settings["sim_speed_pct"],
            "real_speed_pct": self.settings["real_speed_pct"],
            "speed_ack": dict(self.speed_ack),
            "joints": {"names": self.model.names, "q": [round(v, 3) for v in q],
                       "known": self.joints_known(),
                       "lower": [round(v, 2) for v in self.sim.lower],
                       "upper": [round(v, 2) for v in self.sim.upper],
                       "limit_source": self.settings["limit_source"],
                       "urdf_limits": [[round(a, 2), round(b, 2)] for a, b in
                                       zip(self.model.lower, self.model.upper)],
                       "vmax": [round(v, 1) for v in self._vmax()]},
            "tcp": pose, "safety": self.safety,
            "moving": bool(self.sim.traj or self.sim.jog or self.executor.busy) if self.settings["mode"] == "sim"
                      else bool(self.real and self.real.snap.get("moving")),
            "paused": bool(self.sim.paused),
            "exec": {**self.runner.snapshot(), "queue_state": self.queue_state,
                     "execution": self.executor.snapshot(), "stop_result": self.stop_result},
            "real": real, "perm": self.perm, "perm_name": self.PERM_NAMES[self.perm],
            "motion_granted": self.perm >= self.MOTION, "power_granted": True,   # 电源类不设闸
            "ctrl": self.ctrl(), "free": dict(self.free),
            "preview": self.preview_q, "last_deny": self.last_deny,
            "resume": self.resume,
            "sim_state": dict(self.sim_state, is_sim=True),
            "live_fsm": self.live_fsm(), "ready": self.ready_state,
            "ack_left_s": int(max(0.0, self._ack_until - time.monotonic())) if self._armed else 0,
            "objects": self.objects.with_vel(), "obj_hits": self.obj_hits,
            "tools": self.tools.snapshot(),
            "settings": {k: self.settings.get(k) for k in
                         ("fence", "ghost_opacity", "ghost_mode", "ghost_align_tol_deg",
                          "ghost_align_hold_s", "tts", "tts_rate", "vel_source", "limit_source",
                          "safety_yellow_pct", "safety_red_pct", "safety_alarm_pct",
                          "self_collision", "ground_enabled", "real_move_cmd", "custom_limits",
                          "sim_speed_pct", "real_speed_pct", "auto_connect",
                          "free_hold_max_s", "free_force_max_n", "play_len_s", "trail_len")},
            "log": self.log.tail(60), "log_seq": self.log.seq,
        }
        # 遥测走 1Hz 慢通道：曲线/温度/力/脚本/设备身份这类不是渲染路径要用的东西，
        # 塞进 20Hz 快照只会把每帧字节数撑大，前端反而更卡。
        now = time.monotonic()
        if force_tele or now - self._slow_at >= 1.0:
            self._slow_at = now
            self.telemetry_seq += 1
            out["telemetry"] = self.build_telemetry()
            out["telemetry_seq"] = self.telemetry_seq
        return out

    def build_telemetry(self):
        """慢通道：遥测读数 + 影子孪生偏差 + 就绪前体检 + 回放索引。"""
        st = self.live_state()
        q = self.current_joints()
        sim = self.settings["mode"] == "sim"
        link = {"stream_up": bool(self.real and self.real.stream_up),
                "cmd_up": bool(self.real and self.real.cmd_up)}
        tele = {k: st.get(k) for k in TELE_KEYS}
        # 遥测里的任何一项算崩都不能把整个快照打挂 —— 界面会因此全黑，比少一个数严重得多。
        for key, fn in (("deviation", None if sim else
                         lambda: telemetry.deviation(self.model, q, st.get("pcs_base"))),
                        ("preflight", lambda: telemetry.preflight(
                            self.settings["mode"], link, st, self.settings, self.safety,
                            len(self.points.list()), self.objects.any_active())),
                        ("playback", lambda: self.playback.summary()),
                        ("health", lambda: self.health(st))):
            if fn is None:
                tele[key] = None
                continue
            try:
                tele[key] = fn()
            except Exception as e:
                tele[key] = {"ok": False, "why": f"{type(e).__name__}: {e}"}
                self.log.add("sys", f"遥测项 {key} 计算失败：{type(e).__name__} {e}"
                                    f"（pcs_base={st.get('pcs_base')}）")
        # 1Hz 落一格趋势：温度/电流/倍率/模型偏差。走单独接口，不塞进每帧快照。
        self.trend.append({"t": round(time.time(), 1),
                           "temps": st.get("temps"), "currents": st.get("currents"),
                           "ov": st.get("override"),
                           "dev": (tele.get("deviation") or {}).get("pos_mm")
                                  if isinstance(tele.get("deviation"), dict) else None,
                           "lost": st.get("ecat_lost")})
        return tele

    def trend_columns(self):
        """转置成列存给前端画曲线：6 轴 × N 点，前端按轴取一条线，不用逐帧解对象。"""
        rows = list(self.trend)
        n = len(rows)

        def col(key, i=None):
            if i is None:
                return [r.get(key) for r in rows]
            v = [r.get(key) for r in rows]
            return [x[i] if isinstance(x, list) and len(x) > i else None for x in v]

        return {"n": n, "dur_s": round(rows[-1]["t"] - rows[0]["t"], 0) if n > 1 else 0.0,
                "t": [r["t"] for r in rows],
                "temp": [col("temps", i) for i in range(6)],
                "cur": [col("currents", i) for i in range(6)],
                "ov": col("ov"), "dev": col("dev"), "lost": col("lost")}

    def health(self, st):
        """每轴一行健康摘要：温度/电流/限位占用/轴状态，遥测页与关节表着色用。"""
        lo, up = self.limits()
        q = self.current_joints()
        temps = st.get("temps") or []
        cur = st.get("currents") or []
        ax = st.get("axis_status") or []
        ae = st.get("axis_err") or []
        rows = []
        for i, name in enumerate(self.model.names):
            bound = abs(up[i]) if q[i] >= 0 else abs(lo[i])
            rows.append({"j": i + 1, "name": name, "q": round(q[i], 2),
                         "pct": round(abs(q[i]) / bound * 100.0, 1) if bound else None,
                         "temp": temps[i] if i < len(temps) else None,
                         "current": cur[i] if i < len(cur) else None,
                         "status": ax[i] if i < len(ax) else None,
                         "err": ae[i] if i < len(ae) else None})
        return {"axes": rows, "lost": st.get("ecat_lost"), "fps": st.get("ecat_fps"),
                "note": "温度阈值 60℃ 为本软件自设，官方未给单轴温度上限" if temps else
                        "无温度读数（取流未连或该字段缺失）"}



