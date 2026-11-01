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
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from types import MappingProxyType
from pathlib import Path

from . import ai_plan, config, controls, program, telemetry
from .eventlog import EventLog
from .motion import MotionExecutor
from .storage import write_json
from .calibration import CalibrationStore
from .planning import digest, inspect_path, waypoints, prepare_movel
from .kinematics import RobotModel, capsule_distance, interpolate_pose, pose_rotation_distance
from .ik_reachability import reachability_bound
from .objects import ObjectStore, radius as object_radius, capsule_object_gap
from .points import PointStore
from .real_follow_link import ServoCapableRealLink as RealLink
from . import real_movel_qualification
from .robot_sim import SimRobot
from .safety import (ALARM, GREEN, RED, evaluate, tool_capsules, object_capsules,
                     contact_profile, recede_verdict)
from .tools import ToolStore
from .hand_follow import HandFollow
from .hand_track import HandTrack
from .real_jog_follow import RealJogFollow, MAX_STEP_DEG, MAX_OVERRIDE
from .real_follow_runtime import RealPalmFollowController
from . import sim_tool, tool_library
from ..domain import suction
from ..domain.motion_target import MotionTarget, ArrivalEvidence, axes as target_axes
from ..application.run_journal import RunJournal,world_context
from dataclasses import replace


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
    def __init__(self, *, data_dir=None, settings_store=None):
        self._data_dir = Path(data_dir) if data_dir is not None else config.DATA_DIR
        self._settings_store = settings_store if settings_store is not None else config
        self._stop_task = None
        self._stop_unknown = False
        self.settings = copy.deepcopy(self._settings_store.get_settings())
        self.model = RobotModel("elfin5")
        self.log = EventLog(keep=self.settings['log_keep'],path=self._data_dir/'events.jsonl')
        self.points = PointStore(self._data_dir/'points.json')
        self.objects = ObjectStore(self._data_dir/'objects.json',fence_getter=lambda:self.settings['fence'],
            capsule_getter=lambda:[{**cap,'contact_allowed':cap['link']==self.model.flange} for cap in self.motion_model().capsules(self.current_joints(),conservative=True)] if self.settings.get('mode')=='sim' and hasattr(self,'sim') else None,
            contact_getter=lambda:self.robot_play.contact_ids if getattr(self,'robot_play',None) else ())
        self.sim = SimRobot(self.model, self._vmax())
        self.sim.speed_pct = self.settings["sim_speed_pct"]
        self._saved_sim_q=None
        self.speed_ack = {"target": None, "req": None, "ok": None, "why": ""}
        self._speed_task = None
        self._apply_limits()
        self._restore_sim_pose()
        self.real: RealLink | None = None
        self.runner = program.Runner()
        self.runner.input_reader = self._read_di
        self.executor = MotionExecutor(self.log.add)
        self._broadcast_at = 0.0
        self._link_lost = False
        self.stop_result = None
        self.session_id = secrets.token_hex(8)
        self.calibrations = CalibrationStore(self._data_dir/'calibrations.json')
        self.tools = ToolStore(self._data_dir/'tools.json')
        self._sim_tool_action = {"action_state":"idle","last_action":None}
        self._sim_suction = {side:{'vacuum':False,'blow':False} for side in suction.SIDES}
        self.robot_play = None
        self.motion_session = None
        # Hand IK is isolated, but its accepted joints are mirrored to SimRobot;
        # it never reaches tool IO or RealLink.
        self.hand_follow = HandFollow(self._hand_model())
        # 云台追踪：与跟随互不替代，跟随管位置、追踪管朝向。
        self.hand_track = None
        self._hand_generation = 0
        self._hand_cfg_rev = None
        self._hand_camera_key = None
        self._hand_camera_reader = None
        self._hand_input_kind = "camera"
        self._hand_centering = False
        self._hand_moving = False
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
        self._motion_targets = {}
        self._preview_target = None
        self._arrival_evidence = None
        self._target_evidence = None
        self._target_execution = None
        self._simulation_feedback_seq = 0
        self._preview_safety_key = None
        self._preview_safety_value = None
        self.playback = telemetry.Playback(duration_s=self.settings['play_len_s'])
        self._tele_at = 0.0         # 上一次落回放格子的时刻
        self.trend = deque(maxlen=600)      # 1Hz 趋势缓冲（约 10 分钟）
        # 断点续跑：把"跑到第几步"落盘，重启后能接着跑而不是从头再来。
        self.exec_file = self._data_dir / "exec_state.json"
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
        self._real_jog_generation = 0
        self._real_jog_link = None
        self._real_jog_connection = None
        self._real_jog_command_session = None
        self._real_jog_checked = None
        self._real_jog_stop_confirmed = False
        self._real_jog_stop_reply = None
        # Existing interlocks/API retain one owner; no fallback to old motion.
        # Commissioning records are server-side and never created by this API.
        self.real_jog_follow = RealPalmFollowController(self)
        self._startup_notes = []
        if config.no_real() and self.settings.get("mode") == "real":
            self.settings["mode"] = "sim"
            self._startup_notes.append("HUYAN_NO_REAL=1：本进程禁止连真机，强制仿真（自检脚本的隔离闸）")
        elif self.settings.get("mode") == "real" and not self.settings.get("auto_connect", True):
            # 用户明确要"打开默认真机自动连接，但可以关掉"。关掉就落回仿真并说明。
            self.settings["mode"] = "sim"
            self._startup_notes.append("自动连接已关闭：本次以仿真启动，要连真机去「驱动」里手动连接")
        self._startup_notes.append("模型 " + self.model.variant + " 载入，限位与几何来自官方 URDF")
        self.run_journal=RunJournal(self.runner,path=self._data_dir/'run-records.json',context=lambda:world_context(self))

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
        """仿真共用显示工具TCP；真机仅使用确认工具，未知工具仍按法兰处理。"""
        if self.settings.get("mode") == "sim":
            descriptor = sim_tool.normalize(self.settings.get("sim_tool", sim_tool.DEFAULT_TOOL))
            key = json.dumps(descriptor, sort_keys=True)
            if getattr(self, "_sim_model_key", None) != key:
                self._sim_motion_model = sim_tool.model_for(self.model.variant, descriptor)
                self._sim_model_key = key
            return self._sim_motion_model
        mounted = self.tools.get(self.tools.mounted.get("tool_id")) if getattr(self, "tools", None) else None
        tcp = (mounted or {}).get("tcp") or {}
        key = (getattr(self, "_cfg_rev", 0), self.model.variant, self.tools.mounted.get("revision"),
               self.tools.mounted.get("tool_id"), json.dumps(tcp, sort_keys=True))
        if getattr(self, "_real_model_key", None) != key:
            self._real_motion_model = RobotModel(self.model.variant,
                tcp_translation_mm=tcp.get("translation_mm") or [0, 0, 0],
                tcp_rpy_deg=tcp.get("rpy_deg") or [0, 0, 0])
            self._real_model_key = key
        return self._real_motion_model

    def set_sim_tool(self, descriptor, *, expected_revision=None):
        if expected_revision is not None and expected_revision!=self._cfg_rev:
            return {'ok':False,'code':'tool_changed','why':'工具已变化，保留当前工具'}
        if self.robot_play and self.robot_play.held:
            return {'ok':False,'why':'请先释放仿真持件，再更换工具'}
        if not self._hand_available():
            return {"ok": False, "why": "只能在仿真模式调整显示工具"}
        try:
            value = sim_tool.normalize(descriptor)
        except (ValueError, TypeError) as exc:
            return {"ok": False, "why": str(exc)}
        if value == self.settings.get("sim_tool", sim_tool.DEFAULT_TOOL):
            return {"ok": True, **self.tool_snapshot()}
        if (self.sim.traj or self.sim.jog or self.executor.busy or self.hand_follow.active
                or self.runner.state in ("running","waiting","paused") or self.queue_state in ("running","paused")):
            return {"ok": False, "why": "请先停止运动再更换工具"}
        self._stop_hand_follow("仿真工具已变化")
        # update_settings() owns a detached snapshot. Saving to config alone
        # otherwise logs success while this World still renders the old tool.
        patch={"sim_tool":value}
        previous=self.settings.get('sim_tool',sim_tool.DEFAULT_TOOL)
        if isinstance(previous,dict) and previous.get('kind')=='vacuum-reference':
            patch['last_sim_vacuum_tool']=sim_tool.normalize(previous)
        self.settings = self._save_preferences(patch)
        self.preview_q = None
        self.plans.clear()
        self._sim_tool_action = {"action_state":"idle","last_action":None}
        self._sim_suction = {side:{'vacuum':False,'blow':False} for side in suction.SIDES}
        self._cfg_rev += 1
        self.log.add("tool", "仿真末端模型、TCP及包络已同步切换；旧残影与轨迹失效")
        return {"ok": True, **self.tool_snapshot()}

    def ensure_sim_task_tool(self, task):
        if task not in ('writing','drawing','chess'):
            return {'ok':False,'why':'不支持的自动工具任务'}
        if not self._hand_available():return {'ok':False,'why':'自动工具切换仅用于仿真'}
        if task in ('writing','drawing'):descriptor={'kind':'pen'}
        else:
            current=self.settings.get('sim_tool',sim_tool.DEFAULT_TOOL)
            descriptor=current if isinstance(current,dict) and current.get('kind')=='vacuum-reference' else self.settings.get('last_sim_vacuum_tool',sim_tool.DEFAULT_TOOL)
            if not isinstance(descriptor,dict) or descriptor.get('kind')!='vacuum-reference':descriptor=sim_tool.DEFAULT_TOOL
        # 自动换工具前先把上一轮任务攥着的仿真工件放下：末端换成笔还夹着
        # 上一个任务的工件既不真实，也没有别的出路。以前这里直接拒绝并让用户
        # 去"释放仿真持件"，写字/绘制因此卡在半路 —— 现在自动放下再换。
        released=self._release_sim_hold_for_tool()
        if not released.get('ok'):return released
        return self.set_sim_tool(descriptor)

    def _release_sim_hold_for_tool(self):
        """自动换工具前自动放下仿真持件；放不下时给出具体原因。"""
        play=self.robot_play
        if not (play and play.held):return {'ok':True}
        if play.active:return {'ok':False,'why':'程序运行中不能释放持件，请先停止作业'}
        try:
            play.release_manual()
        except RuntimeError as exc:
            return {'ok':False,'why':str(exc)}
        return {'ok':True,'released':True}

    async def start(self):
        if self._task is not None and not self._task.done():
            raise RuntimeError("控制循环已经启动")
        for note in self._startup_notes:
            self.log.add('sys',note)
        self._startup_notes.clear()
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
        # 正确：取消协程或关闭 TCP 不等于物理停止；先走同一停止事务。
        await self.stop_execution("服务关闭")
        self._save_sim_pose()
        self._stop_hand_follow("服务已关闭")
        # 后台任务必须回收：取消后不 await 会留下悬挂 task，
        # 关闭时其异常无处安放，且求解线程可能仍在算。
        for name in ('_speed_task', '_hand_solver_task', '_free_exit_task'):
            task = getattr(self, name, None)
            setattr(self, name, None)
            if task is None:
                continue
            if not task.done():
                task.cancel()
            try:
                await asyncio.wait({task})
            except asyncio.CancelledError:
                raise
            if not task.cancelled() and task.exception() is not None:
                self.log.add('sys', f'后台任务 {name} 异常结束：{task.exception()!r}')
        # stop_execution 已撤销且有界等待旧任务，不能在这里再次无限等待。
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
    def _restore_sim_pose(self):
        # This is simulated joint state, never controller feedback or a command.
        if self.settings.get('mode')!='sim' or config.no_real():return
        try:
            value=json.loads((self._data_dir/'sim_pose.json').read_text(encoding='utf-8'))
            q=value.get('q')
            if value.get('version')!=1 or value.get('model')!=self.model.variant or not isinstance(q,list) or len(q)!=6 or any(
                type(v) not in (int,float) or not math.isfinite(v) or not lo<=v<=hi for v,lo,hi in zip(q,self.sim.lower,self.sim.upper)):return
            self.sim.q=list(q);self._saved_sim_q=list(q)
        except (OSError,ValueError,TypeError):pass

    def _save_sim_pose(self):
        if config.no_real() or self.settings.get('mode')!='sim' or self.real is not None or self.sim.traj or self.sim.jog or self.hand_follow.active:return
        q=list(self.sim.q)
        if q==self._saved_sim_q:return
        write_json(self._data_dir/'sim_pose.json',{'version':1,'model':self.model.variant,'q':q})
        self._saved_sim_q=q

    async def _loop(self):
        self._hand_async_solver = True
        while True:
            now = time.monotonic()
            dt = min(0.1, now - self._last_tick)
            self._last_tick = now
            try:
                self._poll_hand_solver(now)
                self._tick(dt, now)
                if now-getattr(self,'_sim_save_at',0)>=1:
                    self._sim_save_at=now;self._save_sim_pose()
                # 有运动时按 30Hz 推送，静止时仍用 20Hz：动作的连贯性靠帧率撑着，
                # 20Hz 时笛卡尔直线的残影看起来是"停一下、换个姿势、再走"，
                # 而静止画面并不需要那么多帧。
                # 跟随跟的是手，节奏必须和 tick 对齐：tick 是 60Hz，推送也要
                # 60Hz。原来跟随也只有 30Hz，60fps 的画面每两帧才动一次，看着
                # 就是一顿一顿的。程序执行 30Hz 足够，静止 20Hz 更省。
                moving = (self.sim.traj is not None or self.hand_follow.active
                          or self.runner.state in ('running', 'waiting')
                          or self.executor.busy)
                if self.hand_follow.active:
                    broadcast_period = 1/60
                else:
                    broadcast_period = 1/30 if moving else .05
                if now - self._broadcast_at >= broadcast_period:
                    self._broadcast_at = now
                    snap = self.snapshot()
                    for q in list(self.subscribers):
                        if q.full():
                            q.get_nowait()
                        q.put_nowait(snap)
            except Exception as e:                       # 单帧异常不能拖死主循环
                self.log.add("sys", f"主循环异常 {type(e).__name__}: {e}")
            tick_period = 1/60 if self.hand_follow.active else .02
            await asyncio.sleep(max(0.001, tick_period - (time.monotonic() - now)))

    def current_joints(self):
        if self.settings["mode"] == "real":
            if self.real and self.real.snap.get("joints"):
                return list(self.real.snap["joints"])
            # 内部几何计算仍需六元向量；known=False，绝不复用仿真姿态充当回读。
            return [0.0] * 6
        return list(self.sim.q)

    def joints_known(self):
        """真机模式下、链路没通时，关节角其实是**未知**的。

        这时不能拿仿真的 0 去顶 —— 界面会显示成"机器人停在零位"，那是假数据。
        """
        if self.settings["mode"] != "real":
            return True
        return bool(self.real and (getattr(self.real,'pose_valid',None) or self.real.state_valid)())

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
        if any(step.get('kind') in ('sim_pick','sim_place') for step in d.get('steps',[])):
            return {'ok':False,'why':'取放程序不能从旧进度恢复；请核对工件并重新预检'}
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
        self._tick_hand_follow(dt, now)
        if self.settings["mode"] == "real" and not self._real_fresh():
            if self._target_evidence is not None:
                self._target_evidence={**self._target_evidence,'reached':False,'status':'unknown'}
            if self._arrival_evidence is not None:self._arrival_evidence.since=None
            stopping = getattr(self, '_stop_task', None)
            # The stop transaction intentionally invalidates the old epoch.
            # Its own post-ACK frame barrier must settle, not spawn a second
            # stop which changes epoch again and invalidates the first one.
            stop_pending = stopping is not None and not stopping.done()
            if not self._link_lost and not stop_pending:
                self._link_lost = True
                passive=self._passive_real_read()
                self._request_real_jog_stop("状态流失效，真机跟随已撤销")
                if not passive:self.executor.revoke("状态流失效，旧任务已撤销")
                if not passive:self.runner.stop("状态流失效，需核对现场后重新执行")
                if self.real and not self.real_jog_follow.busy and not passive:
                    self.real.invalidate_pending("状态流失效", preserve_inflight=True)
                    if self.real.cmd_up and not passive:
                        # 状态流失效不能只清软件队列，原命令通道仍可能在运动。
                        self._stop_unknown = True
                        try:
                            control_loop = asyncio.get_running_loop()
                        except RuntimeError:
                            # 同步诊断调用不能伪称已发送异步控制报文。
                            self.stop_result = {"ok": False, "settled": False,
                                "why": "控制循环未运行，保护停止未发送，运动保持闭锁"}
                            self.log.add("alarm", self.stop_result["why"])
                        else:
                            control_loop.create_task(self.stop_execution("状态流失效，保护性停止"))
                if not passive and self._ready_task and not self._ready_task.done():
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
            moving = self.sim.tick(dt) or self._hand_moving
        else:
            moving = bool(self.real and self.real.snap.get("moving"))
        self._tick_preview(moving, now)
        self._tick_free(now)
        self._drive_executor(now, moving)
        lo, up = self.limits()
        q = self.current_joints()
        # 复用条件：姿态没变、设置没变、也没有任何摆放物在活动。三者缺一就必须重算，
        # 否则正在下落的箱子会被缓存冻在半空。
        if self.robot_play:self.robot_play.tick()
        key = (tuple(round(v, 4) for v in q), self._cfg_rev,
               getattr(self.objects, "persisted_generation", 0))
        # 三选一就必须重算：机器人在动、有摆放物还在掉、或姿态/设置变了。
        # 少了第一条的话，姿态不变而箱子下落就会被缓存冻在半空。
        if (not moving and not self.objects.any_active()
                and key == self._safe_key and self._safe_cache is not None):
            self.safety = self._safe_cache
        else:
            active_model = self.motion_model()
            caps = active_model.capsules(q)
            # 摆放物是纯仿真道具：先按上一帧的机器人包络推进，再一起交给安全层判定
            contact_ids=self.robot_play.contact_ids if self.robot_play else ()
            # 承托用略保守的包络（防止箱子陷进臂里），命中判定必须与预检/安全层
            # 完全同一口径（object_capsules + 非膨胀胶囊）：否则"膨胀包络擦到"
            # 会被当成真穿透，摆件、上电、载入现场都会立刻报警锁定。
            if self.settings.get('mode')=='sim':
                physics_caps=[{**cap,'contact_allowed':cap['link']==active_model.flange}
                              for cap in active_model.capsules(q,conservative=True)]
                hit_caps=[{**cap,'contact_allowed':cap['link']==active_model.flange}
                          for cap in object_capsules(active_model,active_model.capsules(q))]
            else:
                physics_caps=hit_caps=tool_capsules(active_model,caps)
            object_caps=physics_caps
            self.obj_hits = self.objects.step(dt,object_caps,contact_ids,hit_caps) if object_caps else []
            self.safety = evaluate(active_model, q, self.settings, lo, up, caps=caps,
                                   obj_hits=self.obj_hits)
            self._safe_key, self._safe_cache = key, self.safety
        if self.safety["level"] in (RED, ALARM) and not self._alarm_latched:
            self._alarm_latched = True
            self._on_alarm(moving)
        elif self.safety["level"] not in (RED, ALARM):
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
        """Only the shared, plan-bound arrival proof may clear an executed endpoint."""
        if self._preview_target is not None:
            self._tick_motion_target(moving,now)

    def _exit_free_drive(self, source):
        """退出手拖的单槽后台任务。

        原先直接 asyncio.create_task(...) 丢弃引用：任务异常无人接收（只在事件循环
        关闭时打印 "Task exception was never retrieved"），关闭时也无法取消，
        可能与真机断链后的重连流程抢同一路命令。
        """
        task=getattr(self,'_free_exit_task',None)
        if task is not None and not task.done():
            return
        async def run():
            try:
                await self.free_drive(False, source=source)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.log.add('free', f'退出手拖失败（{source}）：{type(exc).__name__}: {exc}')
        self._free_exit_task=asyncio.create_task(run())

    def _tick_free(self, now):
        """手拖示教的看门狗：按住才生效，松手/超时/越围栏/受力异常立刻退出。

        松闸意味着机械臂会被重力拽着走，所以"没人在按住"这个状态必须自己收回去，
        不能依赖前端一定会发 stop。
        """
        if not self.free["on"] or not self.free["hold"]:
            return
        if now - self.free["since"] > float(self.settings.get("free_hold_max_s", 8.0)):
            self._exit_free_drive("按住超时（本软件自设上限）")
            return
        if self.safety["level"] == ALARM:
            self._exit_free_drive("触发安全告警，自动退出手拖")
            return
        ft = (self.real.snap.get("ft_data") if self.real else None) or []
        lim = float(self.settings.get("free_force_max_n", 0.0))
        if lim and ft and max(abs(v) for v in ft[:3]) > lim:
            self._exit_free_drive(
                f"末端外力 {max(abs(v) for v in ft[:3]):.1f}N 超过设定 {lim:.0f}N")

    def set_preview(self, joints):
        """残影的目标姿态（前端滑块拖出来的预设）。None = 收掉残影。"""
        self._preview_target = None
        self._arrival_evidence = None
        self._target_evidence = None
        self._target_execution = None
        if joints is None:
            self.preview_q = None
            self._preview_safety_key = self._preview_safety_value = None
            return None
        if len(joints) != 6 or not all(math.isfinite(float(v)) for v in joints):
            raise ValueError("预览需要六个有限关节角")
        self.preview_q = [float(v) for v in joints]
        self._preview_safety_key = None
        return self.preview_q

    def _tick_motion_target(self,moving,now):
        target=self._preview_target
        if target.context!=self.plan_context() or target.session!=self.session_id:
            self._target_evidence={'plan_id':target.plan_id,'status':'invalid','reached':False}
            self._arrival_evidence=None
            return
        proof=self._arrival_evidence
        if proof is None:return
        execution=self.executor.snapshot()
        if (self._target_execution is None
                or any(execution.get(k)!=self._target_execution.get(k) for k in ('epoch','started'))
                or execution.get('state') in ('cancelled','failed','stopped','aborted')):
            self._target_evidence={'plan_id':target.plan_id,'status':'invalid','reached':False}
            self._arrival_evidence=None
            return
        real_mode=self.settings['mode']=='real'
        if real_mode:
            link=self.real
            frame=dict(link.snap) if link else {}
            connection=link.epoch if link else None
            seq=frame.get('frame_seq',getattr(link,'frame_seq',-1))
            stamp=frame.get('stamp',0.)
            q=frame.get('joints')
            fresh=self._real_fresh()
            settled=frame.get('moving')==0 and frame.get('inpos')==1 and not moving
            # Raw PCS has no approved tool/UCS interpretation yet. Preserve it
            # for diagnosis; FK is never promoted to a measured TCP endpoint.
            pose=None
        else:
            self._simulation_feedback_seq+=1
            connection=None;seq=self._simulation_feedback_seq;stamp=now
            q=self.current_joints();pose=self.motion_model().pose_vec(q)
            fresh=True;settled=not moving
        result=proof.observe(connection=connection,frame_seq=seq,captured_at=stamp,now=now,
            joints=q,pose=pose,settled=settled,fresh=fresh)
        self._target_evidence={**result,'tcp_source':'unqualified-controller-pcs' if real_mode else 'simulation-fk',
            'controller_pcs':frame.get('pcs_base') if real_mode else None}
        if result['reached'] and self.preview_q is not None:
            self.preview_q=None

    def register_motion_target(self, solved, *, start, context,allowed_axes=None):
        """Bind an IK branch only while its original engineering/start evidence remains valid."""
        if (not solved.get('ok') or context != self.plan_context() or not self.joints_known()
                or any(abs(a-b)>.05 for a,b in zip(start,self.current_joints()))):
            return {'ok':False,'why':'求解期间起点或工程上下文已变化，请重新预览'}
        allowed_axes=target_axes(allowed_axes)
        if allowed_axes is not None and any(abs(solved['joints'][i]-start[i])>.05 for i in range(6) if i not in allowed_axes):
            return {'ok':False,'why':'排除轴目标差异超过0.05°，未登记计划'}
        target=MotionTarget.create(requested=solved['requested'],achieved=solved['achieved'],
            joints=solved['joints'],start=start,context=context,session=self.session_id,
            connection=self.real.epoch if self.settings['mode']=='real' and self.real else None,
            frame_seq=self.real.frame_seq if self.settings['mode']=='real' and self.real else self._simulation_feedback_seq,
            created_at=time.monotonic(),method='relative-joint-compatible' if self.settings['mode']=='real' else 'simulation-joint',
            start_captured_at=self.real.snap.get('stamp') if self.settings['mode']=='real' and self.real else time.monotonic(),
            allowed_axes=allowed_axes)
        self._motion_targets[target.plan_id]=target
        while len(self._motion_targets)>32:self._motion_targets.pop(next(iter(self._motion_targets)))
        return {'ok':True,'plan_id':target.plan_id,'target_plan':target.snapshot()}

    def confirm_motion_target(self,joints,plan_id,*,allowed_axes=None):
        target=self._motion_targets.get(plan_id)
        if (not target or target.allowed_axes!=target_axes(allowed_axes) or target.context!=self.plan_context() or target.session!=self.session_id
                or time.monotonic()-target.created_at>120
                or joints is None or len(joints)!=6 or any(abs(a-b)>1e-6 for a,b in zip(joints,target.joints))):
            return {'ok':False,'why':'目标计划已失效或分支不一致，请重新求解'}
        self.set_preview(joints)
        self._preview_target=target
        self._arrival_evidence=None
        self._target_evidence={'plan_id':target.plan_id,'status':'preview','reached':False}
        return {'ok':True,'preview':list(target.joints),'plan_id':target.plan_id,'target_plan':target.snapshot()}

    async def execute_motion_target(self,joints,plan_id,*,allowed_axes=None):
        target=self._motion_targets.get(plan_id)
        if (not target or target.allowed_axes!=target_axes(allowed_axes) or target is not self._preview_target or target.context!=self.plan_context()
                or target.session!=self.session_id or time.monotonic()-target.created_at>120
                or not self.joints_known() or len(joints)!=6 or any(abs(a-b)>1e-6 for a,b in zip(joints,target.joints))
                or any(abs(a-b)>.05 for a,b in zip(target.start,self.current_joints()))):
            return {'ok':False,'why':'执行计划、起点或上下文已变化，请重新预览'}
        # Consume before submitting: rejection is not permission to replay the old plan.
        self._motion_targets.pop(plan_id,None)
        result=(await self.move_joints(list(target.joints),allowed_axes=target.allowed_axes)
                if target.allowed_axes is not None else await self.move_joints(list(target.joints)))
        if result.get('ok'):
            accepted=replace(target,created_at=time.monotonic(),
                frame_seq=self.real.frame_seq if self.settings['mode']=='real' and self.real else self._simulation_feedback_seq)
            self._arrival_evidence=ArrivalEvidence(accepted,hold_s=self.settings.get('ghost_align_hold_s',.5),
                joint_tol_deg=float(self.settings.get('ghost_align_tol_deg',.3)))
            self._target_execution=self.executor.snapshot()
            self._target_evidence={'plan_id':plan_id,'status':'accepted','reached':False}
        else:self._target_evidence={'plan_id':plan_id,'status':'rejected','reached':False}
        return {**result,'plan_id':plan_id}

    def preview_safety(self):
        if self.preview_q is None:
            return None
        key = (tuple(round(v, 5) for v in self.preview_q), self._cfg_rev)
        if key != self._preview_safety_key:
            model = self.motion_model()
            caps = model.capsules(self.preview_q, conservative=True)
            lower, upper = self.limits()
            obj_hits = self._object_hits(object_capsules(model,caps) if self.settings.get('mode')=='sim' else tool_capsules(model,caps), self.objects.list(),simulation=self.settings.get('mode')=='sim')
            self._preview_safety_value = evaluate(model, self.preview_q, self.settings,
                                                   lower, upper, caps=caps, obj_hits=obj_hits)
            self._preview_safety_key = key
        return self._preview_safety_value

    @staticmethod
    def _object_hits(capsules, objects, *, simulation=False):
        from backend.domain.object_contacts import physical,penetrates
        hits = []
        for obj in objects:
            if not (physical(obj) if simulation else obj.get('collide',True)):
                continue
            for cap in capsules:
                if simulation:
                    if penetrates({**cap,'kind':'capsule'},obj):
                        gap=capsule_object_gap(cap,obj) if obj['kind']=='box' else None
                        hits.append({'link':cap['link'],'name':obj['name'],'gap_mm':gap,'penetrating':True})
                else:
                    gap=capsule_object_gap(cap,obj)
                    if gap<0:hits.append({'link':cap['link'],'name':obj['name'],'gap_mm':gap})
        return hits

    def robot_overlap(self, obj):
        """当前姿态下该摆放物是否与机器人连杆实心重叠；返回命中的连杆名或 None。

        用于新增/移动摆件时的即时拒绝：与其让用户放完再被安全告警锁住，不如在
        落位那一刻就说清楚"这里和机器人重叠"。真机模式不参与（仿真道具专用）。
        """
        if self.settings.get("mode") != "sim" or not isinstance(obj, dict):
            return None
        try:
            model = self.motion_model()
            caps = object_capsules(model, model.capsules(self.current_joints()))
        except Exception:
            return None
        hits = self._object_hits(caps, [obj], simulation=True)
        return hits[0].get("link") if hits else None

    def _on_alarm(self, moving=False):
        """红色安全边界：仿真暂停；真机请求控制器 GrpStop。"""
        if self.settings["mode"] == "sim":
            # SIM05：机器人本来就静止、也没有任务在跑时出现相交（例如现场里有物体
            # 摆在臂上），不能锁死仿真让用户"人工复位"。只提示一次，允许直接沿
            # 脱离方向退出；加深碰撞仍由预检/退离判据拒绝。
            moving = bool(moving or self.executor.busy or self.sim.traj or self.sim.jog
                          or self.runner.state in ("running", "waiting", "paused")
                          or self.queue_state in ("running", "paused"))
            if not moving:
                self.log.add("interlock", "摆放物与机器人已相交（静止）：可沿脱离方向退出，加深碰撞会被拒绝")
                return
            self.sim.paused = True
            self.runner.stop("安全告警自动暂停")
            self.queue_state = "aborted"
            self.queue_step = None
        else:
            asyncio.create_task(self.stop_execution("运行时触发红色安全边界"))
            self.runner.stop("安全告警，真机侧已停点动")
        self.log.add("alarm", "安全余量达到红色/碰撞级：已自动停止，需人工复位后继续")

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
        guard=getattr(self.runner,'execution_guard',None)
        guarded_steps,guarded_run=getattr(self.runner,'steps',None),getattr(self.runner,'run_id',None)
        def check_guard():
            if guard is not None:
                if (self.runner.steps is not guarded_steps or self.runner.run_id!=guarded_run
                        or self.runner.execution_guard is not guard):
                    raise asyncio.CancelledError()
                guard()
        async def run(epoch):
            self.executor.check(epoch)
            check_guard()
            if not self._need(self.MOTION, "程序步骤"):
                raise RuntimeError(self.last_deny)
            if k == "movej":
                if 'drawing_continuous' in step:
                    from backend.application.drawing import Drawing
                    owner=getattr(guard,'__self__',None)
                    if not isinstance(owner,Drawing):raise RuntimeError('连续文字缺少当前绘制所有者')
                    prepared=owner.prepared_step(step)
                    def validate_writing(value):
                        self.executor.check(epoch);check_guard()
                        if owner.prepared_step(step) is not value:raise RuntimeError('连续文字轨迹已被替换')
                    await self.executor.execute_prepared_sim(self.sim,prepared,epoch,validate_context=validate_writing)
                elif self.robot_play and self.robot_play.active:await self.robot_play.motion(step,epoch)
                else:await self._execute_move(step["joints"], step["src"], epoch,
                    **({'execution_guard':check_guard} if guard is not None else {}))
            elif k == "movel":
                if self.robot_play and self.robot_play.active:await self.robot_play.motion(step,epoch)
                else:await self._execute_movel(step["pose"], step["src"], epoch)
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
            elif k in ('sim_pick','sim_place'):
                if not self.robot_play or not self.robot_play.active:
                    raise RuntimeError('仿真取放命令必须先通过场景取放程序预检，不能直接作用真机')
                self.robot_play.grip(k,step['object_id'])
            elif k == "require_tool":
                current_tools = self.tool_snapshot()
                mounted_id = current_tools["mounted"].get("tool_id")
                tool = current_tools.get("tool")
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

    def _recede_allowed(self, start, target, model=None):
        """仿真中当前已相交时，判断目标是否只是沿脱离方向退出。

        判据只看几何量（最小间距必须变大、不得加深、不得新增相交面），
        与告警是否置位无关，也不缓存起点采样。真机一律不放行。
        """
        if self.settings.get("mode") != "sim":
            return False, {"ok": False, "why": "真机门槛不放松"}
        model = model or self.motion_model()
        objects = self.objects.list()
        start_caps = model.capsules(list(start), conservative=True)
        target_caps = model.capsules(list(target), conservative=True)
        # 摆放物与路径预检同一口径（全连杆），否则"小臂已相交"在这里看不见起点接触，
        # 退离会被误判成"新动作"而锁死。
        before = contact_profile(model, list(start), self.settings, start_caps, objects,
                                 object_monitored=object_capsules(model, start_caps))
        after = contact_profile(model, list(target), self.settings, target_caps, objects,
                                object_monitored=object_capsules(model, target_caps))
        verdict = recede_verdict(before, after)
        return verdict["ok"], verdict

    def _sim_recede_unpause(self):
        """退离放行后解除上一帧碰撞造成的仿真暂停，否则"允许退离"实际走不出去。"""
        if self.settings.get("mode") != "sim" or not self.sim.paused:
            return False
        self.sim.paused = False
        self.log.add("interlock", "沿脱离方向退离：已解除上次碰撞造成的仿真暂停（仅仿真）")
        return True

    def precheck(self, target, model=None):
        """评估目标姿态；黄色可执行，红色/碰撞级由调用方拦截。"""
        lo, up = self.limits()
        warns = []
        for i, (t, a, b) in enumerate(zip(target, lo, up)):
            if not (a <= t <= b):
                warns.append("J" + str(i + 1) + " 目标 " + format(t, ".3f") + "° 超出限位 ["
                             + format(a, ".1f") + ", " + format(b, ".1f") + "]（控制器会自己挡，"
                             + "软件不替你决定）")
        active_model = model or self.motion_model()
        caps = active_model.capsules(target, conservative=True)
        obj_hits = self._object_hits(caps if self.settings.get('mode')=='sim' else tool_capsules(active_model,caps), self.objects.list(),simulation=self.settings.get('mode')=='sim')
        st = evaluate(active_model, target, self.settings, lo, up, caps=caps, obj_hits=obj_hits)
        if st["level"] in (RED, ALARM):
            # 已相交时，沿脱离方向退出的目标不是"新的危险动作"；真机由
            # _recede_allowed 直接返回 False，门槛一分不放松。
            recede = st["collisions"] and self._recede_allowed(self.current_joints(), target, active_model)[0]
            if not recede:
                kinds = "；".join(st["reasons"]) or "安全限制"
                warns.append("红色安全预检，禁止执行：" + kinds + "（占用 "
                             + format(st["max_pct"], ".1f") + "%）")
        elif st["level"] == "yellow":
            warns.append("黄色安全预警：" + "；".join(st["reasons"]) + "（占用 "
                         + format(st["max_pct"], ".1f") + "%），允许执行")
        return "；".join(warns) or None

    def _move_to(self, target, src="", allowed_axes=None):
        if allowed_axes is not None:
            if (not isinstance(allowed_axes, (list, tuple)) or not allowed_axes
                    or any(type(i) is not int or not 0 <= i <= 5 for i in allowed_axes)
                    or len(set(allowed_axes)) != len(allowed_axes)):
                raise ValueError("allowed_axes 必须为非空、唯一的 0..5 整数轴列表")
            allowed_axes = tuple(allowed_axes)
        target = list(target)
        warn = self.precheck(target)
        if warn:
            self.log.add("alarm" if "红色安全预检" in warn else "event", warn)
        return self.executor.submit(src or "MOVEJ", lambda epoch: self._execute_move(
            target, src, epoch, allowed_axes=allowed_axes))

    async def _execute_move(self, target, src, epoch, allowed_axes=None, execution_guard=None):
        if getattr(self,'motion_session',None):await self.motion_session.checkpoint(epoch)
        self.executor.check(epoch)
        start = self.current_joints()
        if allowed_axes is not None:
            if (not isinstance(allowed_axes, (list, tuple)) or not allowed_axes
                    or any(type(i) is not int or not 0 <= i <= 5 for i in allowed_axes)
                    or len(set(allowed_axes)) != len(allowed_axes)):
                raise ValueError("allowed_axes 必须为非空、唯一的 0..5 整数轴列表")
            allowed_axes = tuple(allowed_axes)
            if any(not abs(target[i] - start[i]) <= .05 for i in range(6) if i not in allowed_axes):
                raise RuntimeError("排除轴目标差异超过 0.05°，已拒绝 MOVEJ")
        config_revision = self._cfg_rev
        settings, objects = copy.deepcopy(self.settings), self.objects.list()
        lower, upper = self.limits()
        report = await asyncio.to_thread(inspect_path, self.motion_model(), start, list(target),
                                         settings, lower, upper, objects)
        if execution_guard is not None:execution_guard()
        if config_revision != self._cfg_rev:
            raise RuntimeError("运动期间安全设置已变化，已取消本次执行")
        if report["risk_detected"]:
            issue = report["issues"][0]
            why = ("安全轨迹预检为红色，已阻止执行（采样点 " + str(issue["sample"] + 1) +
                   "，" + ("；".join(issue.get("reasons") or []) or "接近围栏/地面") + "）")
            self.last_deny = why
            self.log.add("interlock", why)
            raise RuntimeError(why)
        if report.get("recede_exempted"):
            self._sim_recede_unpause()
        self.executor.check(epoch)
        if getattr(self,'motion_session',None):await self.motion_session.checkpoint(epoch)
        if allowed_axes is not None:
            current = self.current_joints()
            if any(not abs(current[i] - start[i]) <= .05 or not abs(target[i] - current[i]) <= .05
                   for i in range(6) if i not in allowed_axes):
                raise RuntimeError("排除轴漂移超过 0.05°，已拒绝 MOVEJ")
            if self.settings["mode"] == "sim":
                target = [target[i] if i in allowed_axes else current[i] for i in range(6)]
        if self.settings["mode"] == "sim":
            # MOVEJ is one synchronized joint trajectory. The legacy real
            # MoveRelJ adapter is a stop/wait compatibility protocol, not a
            # model of the controller's continuous multi-axis trajectory.
            self.sim.move_j(target)
            while self.sim.traj:
                self.executor.check(epoch)
                if execution_guard is not None:execution_guard()
                await asyncio.sleep(0.02)
            self.executor.check(epoch)
        elif self.settings.get("real_move_cmd") == "movejto" and allowed_axes is None:
            await self._real_movejto(target, epoch)
        else:
            await self._real_rel_step(target, src, epoch, allowed_axes=allowed_axes,
                **({'execution_guard':execution_guard} if execution_guard is not None else {}))

    async def _execute_movel(self, target_pose, src, epoch):
        if getattr(self,'motion_session',None):await self.motion_session.checkpoint(epoch)
        """仿真笛卡尔直线：按 TCP 位姿插值，逐段 IK 后驱动同步关节轨迹。

        真机走控制器的 MoveLTo：协议编码早已就位，缺的是一条现场标定记录，
        没有记录仍然拒绝下发，见 _real_movelto。
        """
        if self.settings["mode"] != "sim":
            return await self._real_movelto(target_pose, epoch)
        if len(target_pose) != 6 or not all(math.isfinite(float(v)) for v in target_pose):
            raise RuntimeError("MOVEL 目标必须是六个有限数")
        model = self.motion_model()
        q = self.current_joints()
        start = model.pose_vec(q)
        distance = math.sqrt(sum((target_pose[i] - start[i]) ** 2 for i in range(3)))
        orientation_delta = pose_rotation_distance(start,target_pose)
        if distance < 0.5 and orientation_delta < 0.2:
            self.log.add("program", "MOVEL 目标已在当前位置：无可见位移")
            return
        lo, up = self.limits()
        context={"mode":"sim","real_connected":False,"plan":self.plan_context(),
                 "model":model.variant,"tcp":list(model.tcp_translation_mm),"rpy":list(model.tcp_rpy_deg)}
        def validate_context(prepared=None):
            self.executor.check(epoch)
            if not self._hand_available() or context["plan"]!=self.plan_context() or (prepared and prepared.context_signature!=digest(context)):
                raise RuntimeError("MOVEL 模式、工具、工位或场景已变化，请重新预检")
        validate_context()
        prepared=await asyncio.to_thread(prepare_movel,model,list(q),list(target_pose),
            copy.deepcopy(self.settings),list(lo),list(up),copy.deepcopy(self.objects.list()),context=context,
            check=lambda:self.executor.check(epoch))
        validate_context(prepared)
        if getattr(self,'motion_session',None):await self.motion_session.checkpoint(epoch)
        await self.executor.execute_prepared_sim(self.sim,prepared,epoch,validate_context=validate_context)
        self.log.add("program", f"MOVEL 仿真完成：{prepared.report['cartesian_ik_samples']} 个采样，单条连续关节曲线（近似直线）")

    async def _real_movelto(self, target_pose, epoch):
        """真机笛卡尔直线：资格门 → 前置检查 → 下发 MoveLTo → 等到位。

        资格门是硬门槛：没有本机现场标定记录就不发。代码先就位，等上机跑完
        一次标定生成记录即可启用，不需要再改这里。
        """
        link = self.real
        if not link or not link.cmd_up or not link.state_valid():
            raise RuntimeError("真机反馈已过期或连接无效，不能下发直线运动")
        snap = link.snap
        if (snap.get('moving') != 0 or snap.get('inpos') != 1
                or snap.get('free_drive') not in (0, False)):
            raise RuntimeError("真机未处于静止且非手拖状态，不能下发直线运动")
        if len(target_pose) != 6 or not all(math.isfinite(float(v)) for v in target_pose):
            raise RuntimeError("MOVEL 目标必须是六个有限数")
        model = self.motion_model()
        start = model.pose_vec(self.current_joints())
        distance = math.sqrt(sum((float(target_pose[i]) - start[i]) ** 2 for i in range(3)))
        orientation_delta = pose_rotation_distance(start, [float(v) for v in target_pose])
        if distance < 0.5 and orientation_delta < 0.2:
            self.log.add("program", "MOVEL 目标已在当前位置：无可见位移")
            return
        mounted = self.tools.mounted if getattr(self, 'tools', None) else {}
        profile = self.tools.get(mounted.get('tool_id')) if mounted else None
        tcp_name = str((profile or {}).get('name') or mounted.get('tool_id') or '')
        ucs_name = 'Base'
        lo, up = self.limits()
        if not tcp_name:
            raise RuntimeError("尚未选择已确认的末端工具；真机直线运动必须绑定工具坐标系")
        record = real_movel_qualification.load_record(self._data_dir / 'real_movel_qualification.json')
        gate = real_movel_qualification.resolve_record(
            record, version=str(snap.get('version') or ''), variant=self.model.variant,
            tool_context=digest({'tool_id': mounted.get('tool_id'),
                                 'revision': mounted.get('revision'),
                                 'tcp': (profile or {}).get('tcp')}),
            lower=list(lo), upper=list(up), tcp_name=tcp_name, ucs_name=ucs_name)
        if not gate['ok']:
            blocker = gate['blockers'][0]
            raise RuntimeError(f"真机直线运动未获资格（{blocker['code']}）：{blocker['detail']}")
        ack = self.speed_ack
        pct = self.settings.get('real_speed_pct')
        if ack.get('ok') is not True or abs(float(snap.get('override', -1)) * 100 - float(pct)) > .05:
            raise RuntimeError("请先通过现有倍率入口设置并确认所选低速倍率")
        self.executor.check(epoch)
        pose = [float(v) for v in target_pose]
        reply = await link.ask("MoveLTo", 0, *pose, tcp_name, ucs_name)
        if getattr(reply, 'ok', None) is not True:
            detail = getattr(reply, 'text', '') or getattr(reply, 'error', '') or '控制器未接受'
            raise RuntimeError(f"MoveLTo 被控制器拒绝：{str(detail)[:180]}")
        deadline = time.monotonic() + self.REAL_MOVEL_SETTLE_S
        while True:
            self.executor.check(epoch)
            now = link.snap
            if now.get('moving') == 0 and now.get('inpos') == 1:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("MoveLTo 已下发但超时未确认到位；请检查现场状态后再继续")
            await asyncio.sleep(.05)
        self.log.add("program", f"MOVEL 真机完成：直线 {distance:.1f}mm，TCP {tcp_name} / 坐标系 {ucs_name}")

    async def _real_movejto(self, target, epoch=None):
        """Long-press-to-point is not ordinary MOVEJ and needs heartbeat ownership."""
        raise RuntimeError("MoveJTo 长按心跳尚未完成资格验证，禁止下发；请使用已验证的 MoveRelJ 策略")

    # 单条 MoveRelJ 的幅度上限。分段是为了"每段都能观察、能中断"，不是为了限制行程 ——
    # 分段数给到 240，任何关节行程都跑得满。
    REL_CHUNK_DEG = 10.0
    REL_MAX_CHUNKS = 240
    REL_SETTLE_TIMEOUT_S = 120.0      # 5% 速度下走完 10° 要十几秒，别把超时设得比运动还短
    # 真机 MoveLTo 的下发到位超时。低速长距离直线可能走很久，取与分段关节运动
    # 同一量级；超时本身不算失败，但必须报出来让人看现场，不能静默当成功。
    REAL_MOVEL_SETTLE_S = 120.0
    REL_START_S = 0.35                # 发完到控制器真的开始动，中间有一小段回执/调度间隙
    # 跟随的速度下限（百分数）。低于这个值时跟随会明显跟不上手：实测 10%
    # 下跟 64mm 要 1.92 秒，看起来就是"卡"。只作用于跟随，不影响程序执行。
    HAND_FOLLOW_MIN_SPEED_PCT = 70.0

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
            if getattr(self,'motion_session',None) and self.motion_session.hold:
                paused_at=time.monotonic()
                await self.motion_session.checkpoint(epoch)
                end+=time.monotonic()-paused_at
                continue
            if self.runner.state == "paused" or s.get("paused"):
                paused_at=time.monotonic()
                await asyncio.sleep(.05)
                end += time.monotonic()-paused_at
                continue
            aligned = target is None or all(abs(a - b) <= 0.05 for a, b in zip(target, s["joints"]))
            new_frame = link.frame_seq > after_seq if after_seq is not None else s.get("stamp", 0) > after
            if s.get("moving") == 0 and s.get("inpos") == 1 and new_frame and aligned:
                return True
            await asyncio.sleep(0.1)
        return False

    async def _real_rel_step(self, target, src="", epoch=None, allowed_axes=None, execution_guard=None):
        """逐段逼近目标角：发一段 → 等它真停下 → 再发下一段。"""
        link = self.real
        if not link:
            raise RuntimeError("真机未连接")
        if allowed_axes is not None:
            if (not isinstance(allowed_axes, (list, tuple)) or not allowed_axes
                    or any(type(i) is not int or not 0 <= i <= 5 for i in allowed_axes)
                    or len(set(allowed_axes)) != len(allowed_axes)):
                raise ValueError("allowed_axes 必须为非空、唯一的 0..5 整数轴列表")
            allowed_axes = tuple(allowed_axes)
        axes = range(6) if allowed_axes is None else allowed_axes
        baseline = self.current_joints()
        connection = link.epoch
        for n in range(self.REL_MAX_CHUNKS):
            if execution_guard is not None:execution_guard()
            if getattr(self,'motion_session',None):await self.motion_session.checkpoint(epoch)
            if epoch is not None:
                self.executor.check(epoch)
            if self.real is not link or link.epoch != connection:
                raise RuntimeError("连接已变化，旧运动已撤销")
            if not await self._wait_motion_idle(link=link, epoch=epoch, connection=connection):
                raise RuntimeError("等真机停下超时")
            if execution_guard is not None:execution_guard()
            cur = self.current_joints()
            if allowed_axes is not None and any(
                    not abs(target[i] - cur[i]) <= .05 or not abs(baseline[i] - cur[i]) <= .05
                    for i in range(6) if i not in axes):
                raise RuntimeError("排除轴目标差异或漂移超过 0.05°，剩余分段没有发")
            pend = [(i, target[i] - cur[i]) for i in axes if abs(target[i] - cur[i]) > 0.05]
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
        if self.settings["mode"] == "real":
            return self.real.snap if self.real else {k: None for k in FAST_KEYS + TELE_KEYS}
        return self.sim_state

    def motion_ok(self):
        if self.settings.get("mode") == "real" and self._stop_interlocked():
            return False
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
        c["motion_granted"] = self.motion_ok()
        c["perm_name"] = "motion" if c["motion_granted"] else "readonly"
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
            result = await self.power(key)
            return {**result, "ctrl": self.ctrl()}
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
        if on and self._real_jog_conflict("进入手拖示教"):
            return {"ok": False, "why": self.last_deny, "free": dict(self.free)}
        if not on:
            if self.real_jog_follow.busy:
                self._request_real_jog_stop("退出手拖示教")
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
            tool_lock=getattr(self,'_tool_action_lock',None)
            if tool_lock is not None and tool_lock.locked() and not self._tool_lock_current():
                self.last_deny='工具输出正在等待确认，请等待当前动作完成'
                return False
            if self.robot_play and self.robot_play.held and not self.robot_play.active:
                self.last_deny='仍吸附仿真工件，请先手动停止吸气并整理工件，再进行其他运动'
                return False
            if self.settings.get("mode") == "real" and self._stop_interlocked():
                self.last_deny = "停止尚未确认，禁止追加运动或危险输出；请先重试停止并核对现场"
                self.log.add("interlock", "已拒绝 " + what + "：" + self.last_deny)
                return False
            if self._real_jog_conflict(what):
                return False
            st = self.live_state()
            if self.settings.get("mode") == "sim" and self.hand_follow.active:
                self.last_deny = "手掌跟随已启动，请先主动停止跟随再执行其他运动"
                self.log.add("interlock", "已拒绝 " + what + "：" + self.last_deny)
                return False
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
        "init": ("ConnectToBox", (), None, "连接电箱（不启动主控）"),
        "enable": ("GrpEnable", (0,), 33, "使能"),
        "disable": ("GrpDisable", (0,), 24, "去使能"),
        "blackout": ("BlackOut", (), 7, "下电（断 48V）"),
        "reset": ("GrpReset", (0,), None, "清错"),
        "stop": ("GrpStop", (0,), None, "停止运动"),
        "standby": ("XToStandby", (0,), 33, "回 Standby"),
        # 2026-10-12 实测：14→24 这一步是 StartMaster 做的，不是 ConnectToBox
        "start_master": ("StartMaster", (), 24, "启动主控（14→24，实测可用）"),
    }
    POWER_FROM = {"electrify": (7,), "start_master": (14,),
                  "enable": (24,), "standby": (24,)}

    @staticmethod
    def _power_precondition(action, state):
        fsm, enabled = state.get("fsm"), state.get("enabled")
        if type(fsm) is not int or fsm not in World.POWER_FROM[action] or enabled not in (0, False):
            return f"当前状态不允许 {action}：FSM={fsm}、enabled={enabled}"
        if state.get("err_code") is None:
            return "控制器错误码未知，等待无故障状态回读"
        if state.get("err_code") != 0:
            return f"控制器故障尚未清除：{state['err_code']}"
        return ""

    async def _wait_power_state(self, link, connection, want, after_seq, timeout=None, require_disabled=False):
        """只使用本连接 ACK 之后的新帧确认；超时与换连接均不能算成功。"""
        end = time.monotonic() + (self.READY_TIMEOUT_S if timeout is None else timeout)
        while time.monotonic() < end:
            observing = self._power_observation_valid(link, connection)
            self._ready_check(link, connection, observe_power=observing)
            if not link.state_valid():
                await asyncio.sleep(min(self.READY_POLL_S, .05))
                continue
            state = link.snap
            fsm = state.get("fsm")
            if state.get("err_code") is None:
                raise RuntimeError("控制器错误码未知，无法确认电源状态")
            if state.get("err_code") != 0:
                raise RuntimeError(f"控制器故障：{state['err_code']}")
            if type(fsm) is not int or fsm not in controls.KNOWN_FSMS:
                # ACK does not mean a power/servo transition has finished.
                # An unclassified intermediate enum never grants permission;
                # only a fresh known target below can confirm this operation.
                await asyncio.sleep(min(self.READY_POLL_S, .05))
                continue
            # Going up may accept a later ready state; disabling must never
            # accept FSM33/enabled1 just because it is "past" FSM24.
            reached = (fsm == 24 and state.get("enabled") in (0, False)) if require_disabled else controls.step_reached(want, fsm, state.get("enabled"))
            if link.frame_seq > after_seq and reached:
                return
            await asyncio.sleep(min(self.READY_POLL_S, .05))
        raise RuntimeError(f"已收到命令回执，但未收到确认 FSM={want} 的新状态帧")

    async def _power_advance(self, action, link, connection):
        name, params, want, label = self.POWER_SEQ[action]
        rep = None
        try:
            self._ready_check(link, connection)
            why = self._power_precondition(action, link.snap)
            if why:
                return {"ok": False, "why": why, "label": label, "outcome": "not_sent"}
            observation = None
            if action in ('electrify', 'start_master'):
                observation = (link, connection, link.command_session, time.monotonic() + 25)
                self._power_observation = observation
            rep = await link.ask(name, *params)
            self._ready_check(link, connection, observe_power=bool(observation))
            if not rep.ok:
                return {"ok": False, "reply": rep.as_dict(), "label": label,
                        "why": self._fail_text(rep), "outcome": rep.outcome}
            # 即使 ask 内恰好收到了状态帧，也还要等 ACK 之后的周期回读。
            await self._wait_power_state(link, connection, want, link.frame_seq)
            return {"ok": True, "reply": rep.as_dict(), "label": label, "outcome": "confirmed"}
        except RuntimeError as exc:
            return {"ok": False, "why": str(exc), "label": label,
                    "reply": rep.as_dict() if rep else None,
                    "outcome": "unknown" if rep else "not_sent"}
        finally:
            if 'observation' in locals() and observation is not None and getattr(self, '_power_observation', None) is observation:
                self._power_observation = None

    async def power(self, action):
        if action not in self.POWER_SEQ:
            return {"ok": False, "why": "未知电源动作 " + action}
        name, params, want, label = self.POWER_SEQ[action]
        if action == "stop":
            return await self.stop_execution("人工停止")
        if (self.settings.get("mode") == "real" and action not in ("disable", "blackout")
                and self._stop_interlocked()):
            return {"ok": False, "why": "停止尚未确认，不能上电、使能或清错"}
        if action not in ("disable", "blackout") and self._real_jog_conflict("电源操作 " + action):
            return {"ok": False, "why": self.last_deny}
        requested_link, requested_mode = self.real, self.settings["mode"]
        if action in ("disable", "blackout"):
            await self.stop_execution(label)
            if self.real is not requested_link or self.settings["mode"] != requested_mode:
                return {"ok": False, "why": "连接或模式已变化，本次电源操作已撤销"}
        # 上电/下电/使能/清错/停止不设许可闸：这是用户明确要的口径，
        # 而且"让它停下来/把电断掉"这类动作加锁只会更危险。
        if self.settings["mode"] == "sim":
            if action == "disable" and self.sim_state.get("enabled") == 0:
                return {"ok": True, "sim": True, "label": label, "why": "已经去使能，保持原电源状态"}
            return self._power_sim(action, want, label)
        if not self.real or not self.real.cmd_up:
            return {"ok": False, "why": "命令通道未连接"}
        link, connection = self.real, self.real.epoch
        if action == "disable" and link.state_valid() and link.snap.get("enabled") == 0:
            return {"ok": True, "label": label, "outcome": "confirmed", "why": "已经去使能，电源保持不变"}
        if action in self.POWER_FROM:
            result = await self._power_advance(action, link, connection)
            if action == "enable" and result.get("ok"):
                try:
                    await self._apply_ready_speed(link, connection)
                except RuntimeError as exc:
                    return {**result, "ok": False, "why": "使能已完成，但" + str(exc), "outcome": "unknown"}
            return result
        if action == "blackout" and self.real.snap.get("enabled"):
            return {"ok": False, "why": "真机实测：已使能时不能直接下电（20018），请先去使能"}
        rep = await link.ask(name, *params)
        if not rep.ok:
            return {"ok": False, "reply": rep.as_dict(), "label": label,
                    "why": self._fail_text(rep), "outcome": rep.outcome}
        if self.settings["mode"] != "real" or self.real is not link or link.epoch != connection:
            return {"ok": False, "why": "连接或模式已变化，执行结果未知",
                    "reply": rep.as_dict(), "outcome": "unknown"}
        if action == "blackout" and rep.ok:
            self._just_blackout = True
        if action == "disable" and rep.ok:
            try:
                await self._wait_power_state(link, connection, 24, link.frame_seq, timeout=8.0, require_disabled=True)
            except RuntimeError as exc:
                return {"ok": False, "reply": rep.as_dict(), "label": label,
                        "why": str(exc), "outcome": "unknown"}
            return {"ok": True, "reply": rep.as_dict(), "label": label, "outcome": "confirmed"}
        return {"ok": rep.ok, "reply": rep.as_dict(), "label": label,
                "why": "" if rep.ok else self._fail_text(rep)}

    def _power_sim(self, action, want, label):
        st = self.sim_state
        cur = st["fsm"]
        if action == "electrify" and cur != 7:
            return {"ok": False, "why": "仿真：当前 FSM=" + str(cur) + " 已过上电阶段"}
        if action == "start_master" and cur != 14:
            return {"ok": False, "why": "仿真：要先上电"}
        if action == "enable" and cur != 24:
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
    SIM_ACTION = {"Electrify": "electrify", "StartMaster": "start_master", "ConnectToBox": "init",
                  "GrpEnable": "enable"}

    def live_fsm(self):
        if self.settings["mode"] == "real":
            return self.real.snap.get("fsm") if self._real_fresh() else None
        return self.sim_state["fsm"]

    async def ready(self, then_enable=True):
        if self.settings.get('mode')=='real' and self._stop_interlocked():
            await self._recover_powered_off_stop()
        if self.settings.get("mode") == "real" and self._stop_interlocked():
            return {"ok": False, "why": "停止尚未确认，不能启动就绪流程"}
        """按 FSM 目标值逐步推进（上电 → 启动主控 → 使能）；返回 {ok, steps, why}。

        已经在跑的流程不重复起。FSM 未知时不盲发，先等 Data Sheet 来帧。
        """
        if self._real_jog_conflict("一键就绪"):
            return {"ok": False, "why": self.last_deny}
        if getattr(self, "_ready_task", None) and not self._ready_task.done():
            return {"ok": False, "why": "就绪流程已在进行中"}
        if self.settings["mode"] == "real" and (not self.real or not self.real.cmd_up):
            return {"ok": False, "why": "命令通道未连接：现在连指令都发不出去"}
        fsm = self.live_fsm()
        if fsm is None:
            return {"ok": False, "why": "还没收到控制器状态上报（FSM 未知），不盲发"}
        if type(fsm) is not int or fsm not in controls.KNOWN_FSMS:
            return {"ok": False, "why": f"FSM={fsm} 不在已验证的就绪流程中"}
        if fsm == controls.READY_FSM and not controls.motion_granted(fsm, self.live_state().get("enabled")):
            return {"ok": False, "why": "FSM=33 但使能回读未确认，尚未就绪"}
        link = self.real if self.settings["mode"] == "real" else None
        connection = link.epoch if link else None
        self._ready_task = asyncio.create_task(self._ready_pump(then_enable, link, connection))
        return {"ok": True, "started": True, "from_fsm": fsm}

    async def power_down(self, reason="下电"):
        """去使能 → 下电。顺序不能跳：已使能时直接 BlackOut，真机实测回 20018。"""
        steps = []
        requested_link, requested_mode = self.real, self.settings["mode"]
        await self.stop_execution(reason)
        if self.real is not requested_link or self.settings["mode"] != requested_mode:
            return {"ok": False, "why": "连接或模式已变化，收回流程已撤销", "steps": steps}
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
        link, connection = self.real, self.real.epoch
        if link.snap.get("enabled"):
            rep = await link.ask("GrpDisable", 0)
            steps.append(["去使能", rep.ok, "OK" if rep.ok else self._fail_text(rep)])
            if not rep.ok:
                return {"ok": False, "steps": steps}
            # 去使能之后 FSM 要从 33 往下走，这段过渡期里 BlackOut 实测回 20018
            # （2026-10-12 就卡在这：取消就绪报"下电=20018"）。所以先等它落定再断 48V。
            try:
                await self._wait_power_state(link, connection, 24, link.frame_seq, timeout=8.0, require_disabled=True)
            except RuntimeError as exc:
                return {"ok": False, "why": str(exc), "steps": steps}
        else:
            steps.append(["去使能", True, "本来未使能，跳过"])
        if self.settings["mode"] != "real" or self.real is not link or link.epoch != connection:
            return {"ok": False, "why": "连接或模式已变化，收回流程已撤销", "steps": steps}
        rep = await link.ask("BlackOut")
        if self.settings["mode"] != "real" or self.real is not link or link.epoch != connection:
            return {"ok": False, "why": "连接或模式已变化，下电结果未知", "steps": steps}
        steps.append(["下电", rep.ok, "OK" if rep.ok else self._fail_text(rep)])
        self._just_blackout = rep.ok
        return {"ok": all(x[1] for x in steps), "steps": steps}

    @staticmethod
    def _fail_text(rep):
        """回执失败的简短说明。Reply 对象没有 hint 属性（hint 是 as_dict 里现算的），
        以前这里直接写 rep.hint，一走到失败分支就 AttributeError 把接口炸掉。"""
        d = rep.as_dict()
        parts = [str(v) for v in (d.get("fields") or []) if v not in (None, "")]
        code = d.get("err_code")
        if code is not None and str(code) not in parts:
            parts.insert(0, str(code))
        if parts:
            return ", ".join(parts)
        if d.get("timeout"):
            return "回执超时"
        return d.get("hint") or d.get("sent") or "失败"

    async def cancel_ready(self):
        """取消就绪仅停任务并去使能，保留 48V 与主控；下电为独立操作。"""
        had = bool(self._ready_task) and not self._ready_task.done()
        if had:
            self._ready_task.cancel()
        prefix = "已停止就绪流程；" if had else "就绪流程本来没在跑；"
        self.ready_state.update(running=False, done=True, why=prefix + "正在去使能，保持电源与主控…")
        r = await self.power("disable")
        steps = [["去使能", bool(r.get("ok")), r.get("why") or "已确认"]]
        if r.get("ok"):
            msg = prefix + "已去使能，48V 和主控保持原状态；再次就绪无需重复冷启动。"
        else:
            msg = prefix + "去使能未确认：" + (r.get("why") or "未知") + "；未发送下电指令。"
        self.ready_state.update(why=msg)
        self.log.add("ready", msg)
        return {"ok": bool(r.get("ok")), "why": msg, "steps": steps}

    async def _wait_for_fsm(self, want, timeout=8.0):
        """只认可已验证的枚举状态；超时返回未知，不用最后读数冒充到位。"""
        link = self.real if self.settings["mode"] == "real" else None
        connection = link.epoch if link else None
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self._ready_check(link, connection)
            cur = self.live_fsm()
            if controls.step_reached(want, cur, self.live_state().get("enabled")):
                return cur
            await asyncio.sleep(0.2)
        return None

    async def _wait_fsm_settle(self, timeout=10.0, *, link=None, connection=None):
        """等 FSM 连续 SETTLE_S 秒不再变化。刚下电/刚上电时它要跳几档，
        这时候发指令会撞 20018，所以先等它自己走完。"""
        if link is None and self.settings["mode"] == "real":
            link = self.real
            if link is None:
                raise RuntimeError("控制器未连接")
            connection = link.epoch
        end = time.monotonic() + timeout
        last, stable_since = self.live_fsm(), time.monotonic()
        while time.monotonic() < end:
            self._ready_check(link, connection)
            await asyncio.sleep(0.2)
            self._ready_check(link, connection)
            cur = self.live_fsm()
            if cur != last:
                last, stable_since = cur, time.monotonic()
            elif cur is not None and time.monotonic() - stable_since >= self.SETTLE_S:
                return cur
        return None

    async def _ready_pump(self, then_enable, link=None, connection=None):
        try:
            await self._ready_pump_body(then_enable, link, connection)
        except asyncio.CancelledError:
            self.ready_state.update(running=False, done=True,
                                    why=self.ready_state.get("why") or "就绪流程已取消")
            raise
        except RuntimeError as exc:
            self.ready_state.update(running=False, done=True, why=str(exc))

    def _power_observation_valid(self, link, connection):
        observation = getattr(self, '_power_observation', None)
        if not observation or not link:return False
        return bool(observation[0] is link and observation[1] == connection == link.epoch
            and observation[2] == link.command_session and time.monotonic() < observation[3]
            and self.real is link and self.settings.get('mode') == 'real' and link.cmd_up
            and link.snap.get('enabled') == 0 and link.snap.get('moving') == 0
            and link.snap.get('err_code') == 0 and not self.executor.busy
            and self.runner.state not in ('running', 'waiting', 'paused')
            and not self.real_jog_follow.busy and not link.jogging
            and not getattr(link, '_jog_intent', None))

    def _ready_check(self, link, connection, observe_power=False):
        if link:
            if (self.settings["mode"] != "real" or self.real is not link or link.epoch != connection
                    or (not link.state_valid() and not (observe_power and self._power_observation_valid(link, connection)))
                    or not link.cmd_up):
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
        self._just_blackout = False
        for i, (name, want, params, label, perm) in enumerate(steps):
            st = self.ready_state["steps"][i]
            t0 = time.monotonic()
            while time.monotonic() - t0 < self.READY_TIMEOUT_S:
                self._ready_check(link, connection)
                fsm = self.live_fsm()
                state = self.live_state()
                if type(fsm) is not int or fsm not in controls.KNOWN_FSMS:
                    raise RuntimeError(f"FSM={fsm} 不在已验证的就绪流程中")
                if fsm == controls.READY_FSM and not controls.motion_granted(fsm, state.get("enabled")):
                    raise RuntimeError("FSM=33 但使能回读未确认，尚未就绪")
                if controls.step_reached(want, fsm, state.get("enabled")):
                    st["at"] = f"FSM={fsm} 回读已确认"
                    break
                if fsm in (6, 15):
                    st["at"] = f"等待过渡状态 FSM={fsm} 结束"
                    await asyncio.sleep(self.READY_POLL_S)
                    continue
                if self.settings["mode"] == "sim":
                    r = self._power_sim(self.SIM_ACTION[name], want, label)
                else:
                    action = {"Electrify": "electrify", "StartMaster": "start_master",
                              "GrpEnable": "enable"}[name]
                    r = await self._power_advance(action, link, connection)
                if not r.get("ok"):
                    st["at"] = "失败"
                    raise RuntimeError(label + " 失败：" + str(r.get("why") or "未知"))
                st["at"] = "回读已确认"
            else:
                raise RuntimeError(f"{name} 等待状态回读超时，FSM={self.live_fsm()}")
        self._ready_check(link, connection)
        if not controls.step_reached(33 if then_enable else 24, self.live_fsm(),
                                     self.live_state().get("enabled")):
            raise RuntimeError("就绪流程结束，但最终状态尚未确认")
        if link and then_enable:
            await self._apply_ready_speed(link, connection)
        self.ready_state.update(running=False, done=True, why="")
        self.log.add("ready", "就绪流程完成：FSM=" + str(self.live_fsm()))

    async def _apply_ready_speed(self, link, connection):
        """用户主动使能/就绪后同步保存的倍率（默认 5%），不发运动。"""
        self._ready_check(link, connection)
        pct = self.settings["real_speed_pct"]
        self.speed_ack = {"target": "real", "req": pct, "ok": None, "why": "等待控制器倍率确认"}
        actual = link.snap.get("override")
        if actual is not None and abs(actual * 100 - pct) <= .05:
            self.speed_ack.update(ok=True, outcome="confirmed", actual_pct=actual * 100, why="")
            return
        await self._real_set_override(pct, link, connection, ack=self.speed_ack)
        self._ready_check(link, connection)
        if self.speed_ack.get("ok") is not True:
            raise RuntimeError("速度倍率未确认：" + (self.speed_ack.get("why") or "未知"))

    def set_speed(self, pct, target=None, schedule=True):
        """改速度。target=None 时按当前驱动模式落到 sim_speed_pct 或 real_speed_pct。

        真机那一路会顺带下发 SetOverride（协议 p72，0.01~1.0）；下发结果原样记在
        speed_ack 里 —— 成功与否以控制器回执为准，本地记录不代表控制器已经生效。
        """
        key = config.speed_key(target if target in ("sim", "real") else self.settings["mode"])
        if key == "real_speed_pct" and self.settings.get("mode") == "real" and self._stop_interlocked():
            self.speed_ack = {"target": "real", "req": pct, "ok": False,
                              "outcome": "not_sent", "why": "停止尚未确认，倍率未修改或下发"}
            return self.settings[key]
        if self._real_jog_conflict("修改速度"):
            self.speed_ack = {"target": "real", "req": pct, "ok": False, "why": self.last_deny}
            return self.settings[key]
        pct = round(max(1.0, min(100.0, float(pct))), 1)
        saved = self._save_preferences({key: pct})
        # Simulation override changes elapsed time, not the checked path or
        # hand-follow coordinate frame. Real speed remains a bound setting.
        if key == "real_speed_pct":
            self._cfg_rev += 1
        self.settings = saved
        self.sim.speed_pct = self.settings["sim_speed_pct"]
        self.log.add("speed", f"{key} -> {pct}%")
        if key == "real_speed_pct" and self.settings["mode"] == "real":
            self.speed_ack = {"target": "real", "req": pct, "ok": None, "why": "等待下发"}
            if schedule:
                if self._speed_task and not self._speed_task.done():
                    self._speed_task.cancel()
                self._speed_task = asyncio.create_task(
                    self._real_set_override(pct, self.real, self.real.epoch if self.real else None,
                                            ack=self.speed_ack))
        elif key == "real_speed_pct":
            self.speed_ack = {"target": "real", "req": pct, "ok": None,
                              "why": "当前是仿真模式：只存值，没往控制器发 SetOverride"}
        else:
            self.speed_ack = {"target": "sim", "req": pct, "ok": True, "why": "仿真插值速度，不涉及控制器"}
        return pct

    async def _real_set_override(self, pct, requested_link=None, requested_epoch=None, ack=None):
        # speed_ack 每次改速度都会被整体替换成新字典。若这里继续写 self.speed_ack，
        # 上一次还没结束的倍率下发会把结果写进新请求的回执里（req 对不上也照写）。
        # 用字典身份绑定本次请求：只有仍是当前那一份时才允许写。
        ack = self.speed_ack if ack is None else ack
        def update(**fields):
            if self.speed_ack is ack:
                ack.update(**fields)
        if self._stop_interlocked():
            update(ok=False, outcome="not_sent", why="停止尚未确认，倍率未下发")
            return
        if self._real_jog_conflict("下发速度倍率"):
            update(ok=False, why=self.last_deny)
            return
        if requested_link is not None and (self.real is not requested_link or self.real.epoch != requested_epoch):
            if ack.get("req") == pct:
                update(ok=False, outcome="not_sent", why="连接已变化，倍率未下发")
            return
        if not self.real or not self.real.cmd_up:
            update(ok=False, why="命令通道未连接，只存了本地值，控制器速度未改动")
            self.log.add("speed", ack["why"])
            return
        if not self.motion_ok():
            update(ok=False, why="只读态（未就绪+使能）：不下发 SetOverride，"
                                 "控制器速度未改动")
            self.log.add("speed", ack["why"])
            return
        link = self.real
        connection, stamp = link.epoch, link.snap.get("stamp", 0)
        after_seq = link.frame_seq
        rep = await link.ask("SetOverride", 0, f"{pct / 100.0:.4f}")
        update(
            ok=None if rep.ok else False,
            outcome=rep.outcome,
            why="" if rep.ok else ("回执超时" if rep.timeout
                                   else self._fail_text(rep)))
        if rep.ok:
            after_seq = link.frame_seq
            end = time.monotonic() + 2.0
            while time.monotonic() < end:
                if self.real is not link or link.epoch != connection or not link.state_valid():
                    update(ok=False, outcome="unknown", why="连接已变化，实际倍率未确认")
                    return
                actual = link.snap.get("override")
                if link.frame_seq > after_seq and actual is not None and abs(actual - pct / 100.) <= 0.0005:
                    update(ok=True, outcome="confirmed", actual_pct=actual * 100.)
                    return
                await asyncio.sleep(0.02)
            update(ok=False, outcome="unknown", why="已收到回执，但实际倍率未确认")
            return
        if not rep.ok:
            self.log.add("speed", "SetOverride 未确认：" + ack["why"]
                         + "（控制器当前 override 见状态读数）")

    def _save_preferences(self, patch):
        # A runtime sim fallback is not a permission to restore a saved real
        # mode when an unrelated display/speed preference is edited.
        return self._settings_store.save_settings({'mode':self.settings['mode'],**patch})

    def update_settings(self, patch):
        if 'fence' in patch and patch['fence']!=self.settings['fence']:
            from backend.domain.scene_bounds import validate_placement
            candidate_fence={**patch['fence'],'z':[0,patch['fence'].get('z',self.settings['fence']['z'])[1]]}
            for item in self.objects.list():validate_placement(item,candidate_fence)
        display_keys = {'ghost_opacity', 'ghost_mode', 'tts', 'tts_rate',
                        'log_keep', 'play_len_s', 'trail_len', 'interaction_range_default'}
        changed = {key for key,value in patch.items() if self.settings.get(key) != value}
        control_changed = bool(changed-display_keys)
        if control_changed:
            self._request_real_jog_stop("工程设置已变化")
        if isinstance(patch.get('fence'),dict):
            patch={**patch,'fence':{**patch['fence'],'z':[0,patch['fence'].get('z',self.settings['fence']['z'])[1]]}}
        if control_changed:
            self._cfg_rev += 1
        self.settings = self._save_preferences(patch)
        if control_changed:
            self.sim.set_vmax(self._vmax(), self.settings["vel_source"])
            self.sim.speed_pct = self.settings["sim_speed_pct"]
            self._apply_limits()
        self.log.resize(self.settings['log_keep'])
        self.playback.set_duration(self.settings['play_len_s'])
        self.log.add("sys", f"设置更新：{','.join(sorted(patch))}")
        return self.settings

    def tool_snapshot(self):
        if self.settings.get("mode") != "sim":
            snapshot=self.tools.snapshot()
            snapshot['mounted']['suction']=self.suction_snapshot()
            try:
                actual,channels=self._suction_binding()
                binding=self._suction_confirmation(actual)
                confirmed=binding is not None and snapshot['mounted'].get('io_confirmation')==binding
            except (ValueError,AttributeError):
                confirmed=False
            snapshot['mounted']['suction_io_confirmed']=confirmed
            snapshot['mounted']['suction_io_reason']='' if confirmed else '请核对当前工具、输出映射和连接后确认'
            return snapshot
        descriptor = sim_tool.normalize(self.settings.get("sim_tool",sim_tool.DEFAULT_TOOL))
        none = self.tools.get("none") or {"id":"none","name":"无工具","type":"none","actions":{},"tcp":{"name":"法兰","translation_mm":[0,0,0]}}
        vacuum = {"id":"vacuum-reference","name":"长条双吸盘（参考）","type":"suction","revision":"reference-v1",
            "tcp":{"name":"安装板中心","translation_mm":[0,0,5],"rpy_deg":[0,0,90],"status":"unvalidated"},
            "payload":{"mass_kg":None,"status":"unknown"},"actions":{"vacuum_on":"unmapped","vacuum_off":"unmapped","blow_on":"unmapped","blow_off":"unmapped"},
            "visual":sim_tool.DEFAULT_TOOL,"verification":"reference"}
        pen = {'id':'pen','name':'书写笔','type':'pen','revision':'pen-v1',
            'tcp':{'name':'笔尖','translation_mm':[0,0,sim_tool.PEN_LENGTH_MM],'rpy_deg':[0,0,0]},
            'actions':{},'visual':{'kind':'pen','rotation':[0,0,0]},'verification':'simulation_reference'}
        items = [none,vacuum,pen] + [tool_library.profile(item) for item in tool_library.entries()]
        tool_id = (descriptor["id"] if descriptor.get("kind")=="library" else 'pen' if descriptor.get('kind')=='pen' else "vacuum-reference") if descriptor else "none"
        tool = copy.deepcopy(next(item for item in items if item["id"]==tool_id))
        model = self.motion_model()
        tool["tcp"] = {"name":tool["tcp"]["name"],"translation_mm":[model.tcp_translation_mm[i]+(20 if i==2 else 0) for i in range(3)],
                       "rpy_deg":model.tcp_rpy_deg,"status":"simulation_reference"}
        return {"items":items,"tool":tool,"visual":descriptor,
            "mounted":{"state":"simulated","scope":"simulation","tool_id":tool_id,"source":"simulation",
                "revision":self._cfg_rev,**self._sim_tool_action,"vacuum":dict(self._sim_vacuum),
                "suction":self.suction_snapshot(),"reason":"仅仿真，未确认或修改实际安装工具"}}

    @property
    def _sim_vacuum(self):
        """Read-only left-cup projection for historical single-cup robotPlay."""
        return MappingProxyType(self._sim_suction['left'])

    def _suction_binding(self):
        tool=self.tools.get(self.tools.mounted.get('tool_id'))
        if not tool or tool.get('type')!='suction':
            raise ValueError('请先确认实际安装的吸盘档案')
        channels=suction.resolve_channels(tool.get('suction_io'),self.calibrations.items['io'])
        return tool,channels

    def _suction_confirmation(self, tool):
        if not self.real or self.tools.mounted.get('state') not in ('manual_confirmed','detected_verified'):
            return None
        return {'profile_revision':tool.get('revision'),'profile_digest':self.tools.profile_digest(tool),
                'io_version':self.calibrations.items['version'], 'session':self.session_id,
                'command_session':self.real.command_session,'connection_epoch':self.real.epoch}

    def suction_snapshot(self):
        unknown={side:{'vacuum':None,'blow':None,'holding':None} for side in suction.SIDES}
        if self.settings.get('mode')=='sim':
            return {side:{**state,'holding':None} for side,state in self._sim_suction.items()}
        try:
            tool,channels=self._suction_binding()
            return suction.read_state(channels,self.real.snap if self.real else {},self._real_fresh())
        except (ValueError,AttributeError):
            return unknown

    def _tool_edit_conflict(self):
        return (self._real_jog_conflict('修改工具档案') or self.executor.busy
                or self.runner.state in ('running','waiting','paused')
                or self._stop_interlocked()
                or (getattr(self,'_tool_action_lock',None) is not None and self._tool_action_lock.locked()))

    def update_tool_profile(self, value):
        if self._tool_edit_conflict():
            return {'ok':False,'why':'请先停止作业并确认停止，再修改工具档案'}
        try:
            saved=self.tools.add_or_update(value)
        except (ValueError,TypeError,OSError) as exc:
            return {'ok':False,'why':str(exc)}
        self._cfg_rev+=1
        self.preview_q=None
        self.plans.clear()
        snapshot=self.tool_snapshot()
        snapshot['profile_history']=self.tools.revisions(saved['id'])
        return {'ok':True,'profile':saved,**snapshot}

    def confirm_tool_io(self, tool_id, revision):
        if self._tool_edit_conflict():
            return {'ok':False,'why':'作业或停止确认尚未结束，不能确认IO映射'}
        try:
            if self.settings.get('mode')!='real' or not self._real_fresh() or not self.real.cmd_up:
                raise ValueError('请等待当前真机连接的新状态；确认不会测试输出')
            tool,channels=self._suction_binding()
            if tool['id']!=tool_id or tool.get('revision')!=revision:
                raise ValueError('工具档案修订已变化，请重新核对')
            confirmation=self._suction_confirmation(tool)
            if confirmation is None:
                raise ValueError('请先人工确认实际安装工具')
            self.tools.confirm_io(confirmation)
            return {'ok':True,**self.tool_snapshot()}
        except (ValueError,OSError) as exc:
            return {'ok':False,'why':str(exc)}

    def select_tool(self, tool_id, source="manual", hardware_id=None, reason=""):
        if self._real_jog_conflict("切换工具"):
            return {"ok": False, "why": self.last_deny}
        if self.settings.get("mode") == "sim":
            if tool_id == "none":
                descriptor = None
            elif tool_id in ("suction-template","vacuum-reference"):
                descriptor = sim_tool.DEFAULT_TOOL
            else:
                item = tool_library.get(tool_id)
                if tool_id == "gripper-template":
                    item = next((t for t in tool_library.entries() if t.get("type")=="parallel_gripper"),None)
                if not item:
                    return {"ok":False,"why":"此档案未关联已安装的仿真末端模型"}
                descriptor = {"kind":"library","id":item["id"],"rotation":item.get("default_rotation_deg",[0,0,0])}
            result = self.set_sim_tool(descriptor)
            return {**result,**self.tool_snapshot()} if result.get("ok") else result
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
        if self._real_jog_conflict("确认无工具"):
            return {"ok": False, "why": self.last_deny}
        if self.settings.get("mode") == "sim":
            return self.select_tool("none")
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

    def _tool_action_current(self):
        """当前任务是否是正在执行的吸盘工具动作（吸盘专属输出的豁免依据）。"""
        try:
            return getattr(self,'_tool_action_task',None) is asyncio.current_task()
        except RuntimeError:
            return False

    def _tool_lock_current(self):
        """当前任务是否持有工具动作锁（运动闸门对持有者本身放行）。"""
        try:
            return getattr(self,'_tool_lock_task',None) is asyncio.current_task()
        except RuntimeError:
            return False

    @asynccontextmanager
    async def _hold_tool_action(self, lock, *, suction=False):
        async with lock:
            self._tool_lock_task=asyncio.current_task()
            if suction:
                self._tool_action_task=asyncio.current_task()
            try:
                yield
            finally:
                if suction:
                    self._tool_action_task=None
                self._tool_lock_task=None

    def _suction_reserved(self, group, bit):
        if self.settings.get('mode')!='real': return False
        tool=self.tools.get(self.tools.mounted.get('tool_id')) or {}
        sides=(tool.get('suction_io') or {}).get('sides',{})
        for item in sides.values():
            for channel in item.values():
                address=self.calibrations.items['io'].get(channel.get('alias'))
                if address and address.get('direction')=='output' and address.get('group')==group and address.get('bit')==bit:
                    return True
        return False

    async def _suction_action(self, action, side):
        lock=getattr(self,'_tool_action_lock',None)
        if lock is None:
            lock=self._tool_action_lock=asyncio.Lock()
        if lock.locked():
            return {'ok':False,'why':'已有工具动作等待确认，请稍后重试'}
        async with self._hold_tool_action(lock, suction=True):
            if self.settings.get('mode')=='sim':
                tool=self.tool_snapshot()['tool']
                if tool.get('type')!='suction':
                    return {'ok':False,'why':'当前仿真工具不支持吸盘动作'}
                try:
                    kind,enabled=suction.transition(action,self._sim_suction[side])
                except ValueError as exc:
                    return {'ok':False,'why':str(exc)}
                self._sim_suction[side][kind]=enabled
                if side=='left' and action=='vacuum_off' and self.robot_play and self.robot_play.held and not self.robot_play.active:
                    self.robot_play.release_manual()
                self._sim_tool_action={'action_state':'active' if enabled else 'idle',
                    'last_action':{'action':action,'side':side,'at':round(time.time(),3)}}
                self.log.add('tool',f'仿真吸盘动作：{side} {action}')
                return {'ok':True,'sim':True,**self.tool_snapshot()}
            sent=False
            try:
                tool,channels=self._suction_binding()
                binding=self._suction_confirmation(tool)
                if binding is None or self.tools.mounted.get('io_confirmation')!=binding:
                    raise ValueError('真实吸盘IO映射尚未针对当前工具、标定与连接确认')
                if not self._real_fresh() or not self.real.cmd_up or self._stop_interlocked():
                    raise ValueError('当前反馈无效或停止尚未确认，未发送工具动作')
                state=suction.read_state(channels,self.real.snap,True)
                kind,enabled=suction.transition(action,state[side])
                channel=channels[side][kind]
                value=channel['on'] if enabled else 1-channel['on']
                link=self.real
                before_seq=link.frame_seq
                sent=True
                reply=await self.write_alias(channel['alias'],value)
                if not reply.get('ok'):
                    raise RuntimeError(reply.get('why','工具IO请求失败，当前输出待核对'))
                deadline=time.monotonic()+.5
                while time.monotonic()<deadline:
                    if (self.real is not link or self._suction_confirmation(tool)!=binding
                            or self.tools.mounted.get('io_confirmation')!=binding or not self._real_fresh()
                            or self._stop_interlocked()):
                        raise RuntimeError('连接、工具或映射已变化，输出结果未知')
                    observed=suction.read_state(channels,link.snap,True)[side]
                    opposite='blow' if kind=='vacuum' else 'vacuum'
                    if link.frame_seq>before_seq and observed[kind] is enabled and (not enabled or observed[opposite] is False):
                        snap=self.tools.record_action(action,'output_confirmed',{'side':side,'value':value,'holding':None})
                        self.log.add('tool',f'吸盘输出已回读：{side} {action}；持件状态未知')
                        return {'ok':True,'sim':False,**self.tool_snapshot()}
                    await asyncio.sleep(.01)
                raise RuntimeError('未收到新的目标输出回读，结果未知，请核对现场')
            except asyncio.CancelledError:
                # Cancellation revokes the request, never invents a safe output
                # or releases a held workpiece by sending a guessed reverse IO.
                self.tools.record_action(action,'unknown',{'side':side,'why':'请求取消，输出待核对'})
                raise
            except (ValueError,RuntimeError,OSError,AttributeError) as exc:
                if sent:
                    try:
                        self.tools.record_action(action,'unknown',{'side':side,'why':str(exc)})
                    except OSError:
                        pass  # Request remains failed; never replace it with success.
                return {'ok':False,'why':str(exc),'side':side,'action':action,'outcome':'unknown'}

    async def tool_action(self, action, duration_ms=0, internal=False, *, side=None):
        if duration_ms != 0:
            return {'ok':False,'why':'暂不支持定时工具动作，请分别确认开启与关闭'}
        if self._real_jog_conflict("工具动作"):
            return {"ok": False, "why": self.last_deny}
        if not internal and (self.executor.busy or self.runner.state in ("running", "waiting")):
            return {"ok": False, "why": "作业执行中，不能手动切换末端工具"}
        if action in suction.SUCTION_ACTIONS:
            try:
                target_side=suction.side_for(side)
            except ValueError as exc:
                return {'ok':False,'why':str(exc)}
            return await self._suction_action(action,target_side)
        if side is not None:
            return {'ok':False,'why':'此工具动作不支持吸盘侧别'}
        # 非吸盘工具动作同样要占住工具锁：否则 IO 写入期间运动闸门失效，
        # 且两个工具动作可以交叉写同一路输出。
        lock=getattr(self,'_tool_action_lock',None)
        if lock is None:
            lock=self._tool_action_lock=asyncio.Lock()
        if lock.locked():
            return {'ok':False,'why':'已有工具动作等待确认，请稍后重试'}
        async with self._hold_tool_action(lock):
            return await self._tool_action_body(action,duration_ms)

    async def _tool_action_body(self, action, duration_ms=0):
        if self.settings.get("mode") == "sim":
            tool = self.tool_snapshot()["tool"]
            if action not in tool.get("actions",{}):
                return {"ok":False,"why":"当前仿真工具不支持此动作"}
            state = "active" if action in {"close","vacuum_on","attach"} else "idle"
            self._sim_tool_action = {"action_state":state,"last_action":{"action":action,"state":state,"at":round(time.time(),3)}}
            self.log.add("tool",f"仿真工具动作：{action}（未向硬件发送）")
            return {"ok":True,"sim":True,**self.tool_snapshot()}
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
        self._request_real_jog_stop("模式已变化")
        if self.real_jog_follow.busy:
            await self.real_jog_follow.stop("模式已变化")
        self.update_settings({"mode": mode})
        if mode == "real":
            await self.connect_real()
        else:
            await self.disconnect_real()
        return True

    async def connect_real(self, ip=None):
        self._stop_hand_follow("已退出仿真跟随")
        if config.no_real():
            self.log.add("interlock", "HUYAN_NO_REAL=1：本进程不允许连控制器（自检脚本隔离闸），"
                                      "连接请求已拒")
            return False
        if self.real:
            await self.stop_execution("重新连接")
            await self.real.stop()
        host = ip or self.settings["robot_ip"]
        self.update_settings({"mode": "real", "robot_ip": host})
        self.real = RealLink(host, log=lambda k, m: self.log.add(k, m))
        await self.real.start()
        self.log.add("link", f"正在连接真机 {host}，三维姿态改由 10004 取流驱动")
        return True

    async def disconnect_real(self):
        self._stop_hand_follow("连接状态已变化，请重新开始")
        await self.stop_execution("断开控制器")
        if self.real:
            await self.real.stop()
            self.real = None
        self.update_settings({"mode": "sim"})
        self.log.add("link", "已断开真机，回到仿真模式")

    async def jog(self, axis, direction, on):
        """点动：仿真直接改速度状态；真机走 LongJogJ + 心跳。"""
        if not on:
            if self.real_jog_follow.busy:
                return await self.stop_execution("点动停止")
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

    async def move_joints(self, joints, allowed_axes=None):
        if allowed_axes is not None:
            if (not isinstance(allowed_axes, (list, tuple)) or not allowed_axes
                    or any(type(i) is not int or not 0 <= i <= 5 for i in allowed_axes)
                    or len(set(allowed_axes)) != len(allowed_axes)):
                return {"ok": False, "accepted": False,
                        "why": "allowed_axes 必须为非空、唯一的 0..5 整数轴列表"}
            allowed_axes = tuple(allowed_axes)
            current = self.current_joints()
            if any(not abs(joints[i] - current[i]) <= .05 for i in range(6) if i not in allowed_axes):
                return {"ok": False, "accepted": False, "why": "排除轴目标差异超过 0.05°，已拒绝 MOVEJ"}
        if self.executor.busy or self.runner.state in ("running", "waiting"):
            return {"ok": False, "why": "已有任务执行中，请先停止"}
        if not self._need(self.MOTION, "手动 MOVEJ"):
            return {"ok": False, "why": self.last_deny}
        warn = self.precheck(list(joints))
        if warn and "红色安全预检" in warn:
            self.last_deny = warn
            self.log.add("interlock", warn)
            return {"ok": False, "accepted": False, "why": warn, "warn": warn}
        accepted = (self._move_to(list(joints), src="手动 MOVEJ") if allowed_axes is None else
                    self._move_to(list(joints), src="手动 MOVEJ", allowed_axes=allowed_axes))
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

    def point_items(self):
        """Joint positions stay stored; simulated TCP readouts use the active TCP."""
        model = self.motion_model()
        return [{**p, "tcp": model.flange_pose(p["joints"]), "tcp_source": "fk"}
                if self.settings["mode"] == "sim" else p
                for p in self.points.list()]

    def resolve_point(self, reference):
        return next((p for p in self.point_items()
                     if str(p["id"]) == str(reference) or reference in (p.get("code"), p["name"])), None)

    async def load_program(self, src, params=None):
        if self.robot_play and (self.robot_play.active or self.robot_play.held):
            return {'ok':False,'why':'请先停止取放程序，并释放或整理当前吸附工件后再载入其他程序'}
        if self.executor.busy or self.runner.state in ("running", "waiting"):
            return {"ok": False, "why": "请先停止当前任务再载入程序"}
        try:
            steps = program.parse(src, self.resolve_point, params=params, point_items=self.point_items())
        except program.ProgramError as e:
            self.log.add("program", f"解析失败：{e}")
            return {"ok": False, "error": str(e)}
        self.runner.load(steps)
        self.log.add('program', '脚本语法检查通过，步骤由运行时生成' if self.runner.script_spec else f'解析成功，共 {len(steps)} 步')
        return {"ok": True, "steps": steps}

    async def write_end_do(self, bit, val):
        if self._suction_reserved('end',bit) and not self._tool_action_current():
            return {'ok':False,'why':'此输出属于吸盘，请使用对应侧的工具动作'}
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
        if self._suction_reserved(channel['group'],channel['bit']) and not self._tool_action_current():
            return {'ok':False,'why':'此输出属于吸盘，请使用对应侧的工具动作'}
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
        if self.settings.get("mode") == "sim":
            motion_settings["sim_tool"] = sim_tool.normalize(self.settings.get("sim_tool",sim_tool.DEFAULT_TOOL))
        tool = self.tools.mounted if getattr(self, "tools", None) else {}
        if self.settings.get("mode") == "real":
            motion_settings["model"] = self.model.variant
            motion_settings["real_speed_pct"] = self.settings.get("real_speed_pct")
            motion_settings["tool_profile"] = self.tools.get(tool.get("tool_id"))
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
                "run_records":self.run_journal.list(),
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
            self._exit_free_drive("急停")
        return await self.stop_execution("急停")

    async def stop_execution(self, reason="人工停止"):
        """停止任务独立于请求存活；并发停止合并，未知结果保持运动闭锁。"""
        stopping = getattr(self, "_stop_task", None)
        if stopping is None or stopping.done():
            if self.settings.get("mode") == "real":
                self._stop_unknown = True
            binding = (self.real, getattr(self.real, "command_session", None))
            stopping = asyncio.create_task(self._stop_execution_body(reason, binding))
            self._stop_task = stopping
        try:
            return await asyncio.shield(stopping)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._stop_unknown = True
            self.stop_result = {"ok": False, "settled": False, "acknowledged": False,
                                "why": "停止异常，运动保持闭锁：" + str(exc)}
            self.log.add("alarm", self.stop_result["why"])
            return self.stop_result

    def _stop_interlocked(self):
        stopping = getattr(self, "_stop_task", None)
        return (getattr(self, "_stop_unknown", False)
                or bool(stopping is not None and not stopping.done()))

    def _passive_real_read(self):
        """Connecting for observation must not inject a stop into an idle cabinet."""
        link=self.real
        if not link:return True
        if self._power_observation_valid(link, link.epoch):return True
        ready=getattr(self,'_ready_task',None)
        if (self.executor.busy or self.runner.state in ('running','waiting','paused')
                or self.real_jog_follow.busy or (ready is not None and not ready.done())
                or getattr(link,'jogging',None) or getattr(link,'_jog_intent',None)):
            return False
        s=link.snap
        if getattr(link,'frame_seq',0)==0 and all(s.get(key) is None for key in ('fsm','enabled','moving')):return True
        return s.get('fsm') in (7,14,15,24) and s.get('enabled') in (0,False) and s.get('moving') in (0,False)

    async def _recover_powered_off_stop(self):
        """Two fresh powered-off frames reconcile an old stop failure, not a new stop command."""
        link=self.real;stopping=getattr(self,'_stop_task',None)
        if (not getattr(self,'_stop_unknown',False) or not link or not link.cmd_up
                or (stopping is not None and not stopping.done()) or self.executor.busy
                or self.real_jog_follow.busy):return False
        def off():
            return (self.real is link and link.state_valid() and link.snap.get('fsm')==7
                    and link.snap.get('enabled') in (0,False) and link.snap.get('moving') in (0,False)
                    and link.snap.get('inpos') in (1,True))
        if not off():return False
        epoch=link.epoch;seq=link.frame_seq;q=list(link.snap['joints']);end=time.monotonic()+.3
        while time.monotonic()<end:
            await asyncio.sleep(.02)
            if link.epoch!=epoch or not off():return False
            if link.frame_seq>seq:
                if max(abs(a-b) for a,b in zip(q,link.snap['joints']))>.05:return False
                self._stop_unknown=False
                motion=getattr(self,'motion_session',None)
                if motion and not motion._work():motion.abandon_powered_off()
                self.stop_result={'ok':True,'settled':True,'acknowledged':False,
                                  'proof':'fresh_powered_off_frames','why':'控制器已确认下电并静止，旧停止未确认状态已解除；未发送急停'}
                self.log.add('event',self.stop_result['why'])
                return True
        return False

    async def _stop_execution_body(self, reason, binding):
        link = self.real
        ready_task = self._ready_task
        # A disabled cabinet may reject GrpStop. Only consider a no-command
        # confirmation when there was no active work or in-flight transaction.
        idle_disabled = bool(link and link.state_valid()
            and link.snap.get('fsm') in (7, 14, 24) and link.snap.get('enabled') == 0
            and link.snap.get('moving') == 0 and link.snap.get('inpos') == 1
            and link.snap.get('err_code') == 0 and not self.executor.busy
            and self.runner.state not in ('running', 'waiting', 'paused')
            and not (ready_task and (not ready_task.done() or ready_task.cancelled()))
            and not self.real_jog_follow.busy and not link.jogging
            and not getattr(link, '_jog_intent', None)
            and getattr(link, '_inflight', None) is None and not link._futures
            and link.q.empty())
        real_follow_busy = self.real_jog_follow.busy
        self._request_real_jog_stop(reason)
        self._stop_hand_follow(reason)
        task = self.executor.revoke(reason)
        if self._speed_task:
            self._speed_task.cancel()
        self.runner.stop(reason)
        self.sim.stop()
        if self.robot_play:self.robot_play.stop()
        self.queue_state = "aborted"
        if self.real and not real_follow_busy:
            self.real.invalidate_pending(reason)
        for ready_task in (self._ready_task,):
            if ready_task and not ready_task.done():
                ready_task.cancel()
        if real_follow_busy:
            await self.real_jog_follow.stop(reason)
            confirmed = bool(self._real_jog_stop_confirmed and not self.real_jog_follow.busy)
            self._stop_unknown = not confirmed
            self.stop_result = {"ok": confirmed,
                                "reply": self._real_jog_stop_reply,
                                "settled": confirmed, "why": reason}
            self.log.add("alarm", reason, self.stop_result)
            return self.stop_result
        link = self.real
        if (link is not binding[0]
                or getattr(link, "command_session", None) != binding[1]):
            self._stop_unknown = True
            self.stop_result = {"ok": False, "settled": False, "acknowledged": False,
                                "why": "停止请求绑定的命令连接已变化，未向新连接发送停止"}
            return self.stop_result
        if idle_disabled and await self._confirm_disabled_idle(link, binding[1]):
            self._stop_unknown = False
            self.stop_result = {'ok': True, 'settled': True, 'acknowledged': False,
                                'proof': 'fresh_disabled_stationary_frames',
                                'why': reason + '：连续新帧确认未使能且静止，无需发送运动停止'}
            self.log.add('event', self.stop_result['why'])
            return self.stop_result
        expected_epoch = link.epoch + 1 if link else None
        reply = await link.estop() if link else None
        # 正确：回执前的空闲帧不能确认这次停止。
        ack_at = time.monotonic()
        barrier = link.frame_seq if link else 0
        settled = not link and self.settings.get("mode") == "sim"
        if link and reply.ok and link.epoch == expected_epoch:
            connection = expected_epoch
            end = ack_at + .5
            while time.monotonic() < end:
                if self.real is not link or link.epoch != connection:
                    break
                stamp = link.snap.get("stamp")
                if (link.state_valid() and type(stamp) in (int, float) and math.isfinite(stamp)
                        and ack_at < stamp <= time.monotonic() and time.monotonic()-stamp <= .15
                        and link.frame_seq > barrier
                        and link.snap.get("moving") == 0 and link.snap.get("inpos") == 1):
                    settled = True
                    break
                await asyncio.sleep(.02)
        # 停机先于取消等待，拒绝取消的旧工作任务也不能阻塞停机下发。
        pending = set()
        if task and task is not asyncio.current_task() and not task.done():
            _, pending = await asyncio.wait({task}, timeout=.1)
        self._stop_unknown = not settled or bool(pending)
        self.stop_result = {"ok": bool(settled and not pending and (reply.ok if reply else True)),
                            "acknowledged": bool(reply and reply.ok),
                            "reply": reply.as_dict() if reply else None,
                            "settled": settled,
                            "why": reason if settled and not pending else reason + "：停止尚未确认，运动保持闭锁"}
        self.log.add("alarm", reason, self.stop_result)
        return self.stop_result

    async def _confirm_disabled_idle(self, link, command_session):
        """No ACK claim: prove an already-disabled idle cabinet on new frames."""
        epoch, first_seq = link.epoch, link.frame_seq
        previous_seq, first_q = first_seq, list(link.snap['joints'])
        confirmations = 0
        end = time.monotonic() + .5
        while time.monotonic() < end:
            if (self.real is not link or link.epoch != epoch or not link.cmd_up
                    or link.command_session != command_session
                    or getattr(link, '_inflight', None) is not None or link._futures
                    or not link.q.empty() or link.jogging or getattr(link, '_jog_intent', None)):
                return False
            if link.frame_seq > previous_seq and link.state_valid():
                s = link.snap
                if (s.get('fsm') not in (7, 14, 24) or s.get('enabled') != 0
                        or s.get('moving') != 0 or s.get('inpos') != 1 or s.get('err_code') != 0):
                    return False
                q = list(s['joints'])
                if max(abs(a-b) for a,b in zip(first_q,q)) > .05:
                    return False
                confirmations += 1
                if confirmations >= 2:return True
                previous_seq = link.frame_seq
            await asyncio.sleep(.02)
        return False

    def start_program(self):
        if any(step.get('kind') in ('sim_pick','sim_place') for step in self.runner.steps):
            if not self.robot_play or not self.robot_play.active or self.runner.steps is not self.robot_play.active['steps']:
                return {'ok':False,'why':'取放程序绑定已失效，请从功能页重新预检并确认，不能直接重启旧程序'}
        if self.executor.busy or not self._need(self.MOTION, "程序启动"):
            return {"ok": False, "why": self.last_deny or "已有任务执行中"}
        started = self.runner.start()
        if started and self.settings['mode'] == 'sim':
            self.set_preview(None)
        return {"ok": started, **self.runner.snapshot()}

    async def pause_program(self):
        if getattr(self,'motion_session',None):return await self.motion_session.pause()
        if self.runner.state not in ("running", "waiting"):
            return {"ok": False, "why": "程序未运行"}
        self.runner.pause()
        if self.settings["mode"] == "sim":
            self.sim.paused = True
        else:
            if not self.real or not self.real.state_valid() or not self.real.cmd_up:
                return {"ok": False, "why": "真机状态不可用，程序队列已暂停"}
            reply = await self.real.ask("GrpInterrupt", 0)
            if not reply.ok:
                return {"ok": False, "why": "控制器暂停被拒绝，程序队列已暂停", "reply": reply.as_dict()}
        self.log.add("program", "程序已暂停")
        return {"ok": True, "state": self.runner.state}

    async def resume_program(self):
        if getattr(self,'motion_session',None):return await self.motion_session.resume()
        if not self._need(self.MOTION, "继续程序"):
            return {"ok": False, "why": self.last_deny}
        if self.runner.state != "paused":
            return {"ok": False, "why": "程序未暂停"}
        if self.safety["level"] in (RED, ALARM):
            return {"ok": False, "why": "当前安全状态为红色"}
        if self.settings["mode"] == "real":
            if not self.real or not self.real.state_valid() or not self.real.cmd_up:
                return {"ok": False, "why": "真机状态不可用"}
            reply = await self.real.ask("GrpContinue", 0)
            if not reply.ok:
                return {"ok": False, "why": "控制器继续被拒绝", "reply": reply.as_dict()}
        else:
            self.sim.paused = False
        self.runner.resume()
        self.log.add("program", "程序继续执行")
        return {"ok": True, "state": self.runner.state}

    async def ask_real(self, name, *params):
        if self.settings["mode"] != "real" or not self.real:
            return {"ok": False, "why": "当前不是真机模式"}
        if name == "GrpStop":
            return await self.stop_execution("原始命令停止")
        read_only = isinstance(name, str) and (name.startswith(("Read", "Get", "Is")) or name == "PackageVersion")
        if not read_only and self._real_jog_conflict("原始写命令 " + str(name)):
            return {"ok": False, "why": self.last_deny}
        # 正确：诊断通道不能成为运动、使能或 IO 的旁路。
        from backend.core import protocol as P
        if (not isinstance(name, str) or name not in P.CMDS
                or not (name.startswith(("Read", "Get", "Is")) or name == "PackageVersion")):
            return {"ok": False, "why": "诊断通道只允许只读指令；控制操作请使用专用入口"}
        link, epoch = self.real, self.real.epoch
        context = self.plan_context()
        rep = await link.ask(name, *params)
        if self.real is not link or link.epoch != epoch or self.plan_context() != context:
            return {"ok": False, "why": "连接或上下文已变化，回执不能确认当前状态", "reply": rep.as_dict()}
        return rep.as_dict()

    def ik_pose(self, target6, seed=None, allow_partial=False, *, position_only=False,
                budget_s=None, cancelled=None):
        """笛卡尔目标 → 关节角。优先沿初始位形求解，返回解及静态风险。

        不收敛时如实返回 ok=False 带上真实残差 —— 绝不能拿一组差 300mm 的解去动机器。
        """
        started = time.monotonic()
        deadline = started + budget_s if budget_s is not None else None
        def result(data):
            return {"solve_ms": round((time.monotonic()-started)*1000, 3), **data}
        def context():
            link = getattr(self, "real", None)
            return (getattr(self, "session_id", None), self.settings.get("mode"),
                    getattr(self, "_cfg_rev", None), id(link), getattr(link, "epoch", None))
        request_context = context()
        def interrupted():
            if cancelled is not None and cancelled():
                return "cancelled", "目标求解已取消，未载入"
            if context() != request_context:
                return "context_changed", "模式、工具或连接已变化，求解结果失效"
            if deadline is not None and time.monotonic() >= deadline:
                return "solve_timeout", "本次逆解计算预算已用尽，未载入；不代表目标不可达"
            return None
        if len(target6) != 6:
            return result({"ok": False, "reason_code": "invalid_target", "why": "目标需要 X,Y,Z,A,B,C 六个数"})
        # 正确：自动姿态仅用于明确选择的仿真示教，不能改变真机位姿命令语义。
        if position_only and (self.settings.get("mode") != "sim" or getattr(self, "real", None)):
            return result({"ok": False, "reason_code": "mode_not_supported", "why": "自动姿态仅用于未连接真机的仿真示教"})
        if position_only and allow_partial:
            return result({"ok": False, "reason_code": "invalid_target", "why": "自动姿态不能与中间目标裁剪同时启用"})
        if not self.joints_known():
            return result({"ok": False, "reason_code": "state_unknown", "why": "当前姿态未知，不能选择运动学初值"})
        cur = self.current_joints()
        lo, up = self.limits()
        # 正确：只分离每次查询的 FK 缓存，共享只读几何；避免与50Hz状态循环互相改写缓存。
        model = copy.copy(self.motion_model())
        model._fk_key, model._fk_value = None, None
        goal = [float(v) for v in target6]

        initial = list(seed) if seed is not None else list(cur)
        if len(initial) != 6 or not all(math.isfinite(float(v)) for v in initial + goal):
            return result({"ok": False, "reason_code": "invalid_target", "why": "目标和初始关节角必须是六个有限数值"})
        stopped = interrupted()
        if stopped:
            return result({"ok": False, "reason_code": stopped[0], "why": stopped[1]})
        # 正确：只有可验证的几何必要条件能直接断言不可达；不支持的链返回None继续数值搜索。
        # 滑块裁剪要寻找中间目标，不能因原目标不可达而提前取消整次查询。
        bound = reachability_bound(model, goal, position_only=position_only) if not allow_partial else None
        if bound:
            label = "目标位置不可达" if bound['reason_code'] == 'unreachable_position' else "指定完整位姿不可达"
            hint = "；可选择自动姿态仅约束位置" if bound['reason_code'] == 'unreachable_pose' else ""
            return result({"ok": False, "converged": False, **bound,
                           "bound_reason": bound['why'], "why": f"{label}；未载入{hint}"})
        solver_options = {"deadline": deadline, "cancelled": lambda: interrupted() is not None}
        q, emm, edeg, conv = model.ik(initial, goal, lo, up,
                                     iters=60 if allow_partial else 100,
                                     max_restarts=4 if allow_partial else 8,
                                     **solver_options,
                                     **({"position_only": True} if position_only else {}))
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
                if interrupted():
                    break
                t = (low + high) / 2
                # 正确：平移线性、姿态沿SO(3)短弧；欧拉角跨±180度不能直接取平均。
                trial = interpolate_pose(start, requested, t)
                cq, cp, cr, cc = model.ik(valid_q, trial, lo, up, iters=35, restarts=False, **solver_options)
                if cc:
                    low, valid_q, valid_goal = t, cq, trial
                else:
                    high = t
            if low > .002:
                q, goal, fraction, conv = valid_q, valid_goal, low, True
                e = model._err(q, goal)
                emm, edeg = max(abs(v) for v in e[:3]), max(abs(v) for v in e[3:])
        stopped = interrupted()
        if stopped:
            return result({"ok": False, "converged": False, "reason_code": stopped[0], "why": stopped[1],
                           "pos_err_mm": round(emm, 3), "rot_err_deg": round(edeg, 3)})
        if not conv:
            # 正确：查询的错误与残差就近返回，不为每个拖拽中间点同步写审计日志。
            return result({"ok": False, "converged": False, "reason_code": "not_converged", "pos_err_mm": round(emm, 3),
                    "rot_err_deg": round(edeg, 3),
                    "why": ("未找到满足位置与关节范围的解；未载入。" if position_only else
                            "本次搜索未找到满足完整位姿的解；未载入，不等于证明不可达。可调整初值或选择自动姿态。")})
        # 正确：逆解只计算与标注静态风险；执行还必须经过完整的安全路径检查。
        warn = self.precheck(q, model=model)
        stopped = interrupted()
        if stopped:
            return result({"ok": False, "reason_code": stopped[0], "why": stopped[1]})
        actual_error = model._err(q, requested)
        return result({"ok": True, "converged": True, "approx": False,
                "reason_code": "clipped" if fraction < 1 else "solved", "joints": [round(v, 6) for v in q],
                "orientation_constrained": not position_only,
                "achieved": model.pose_vec(q), "requested": requested, "fraction": fraction,
                "clipped": fraction < 1.0,
                "requested_pos_err_mm": round(residual[0], 4),
                "requested_rot_err_deg": round(max(abs(v) for v in actual_error[3:]), 4) if position_only else round(residual[1], 4),
                "pos_err_mm": round(emm, 4), "rot_err_deg": round(edeg, 4), "warn": warn,
                "note": ("仅约束位置，姿态已自动求解" if position_only else "已沿当前位形求解；多解情况下不保证全局最近")
                        + ("；滑块已退回可求解的中间目标，尚未到达原请求" if fraction < 1 else "")
                        + ("；静态风险见 warn，执行仍需通过安全路径检查" if warn else "")})

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
        code = st.get("err_code")
        stamp = st.get('stamp')
        age_s = max(0.,time.monotonic()-stamp) if type(stamp) in (int,float) and math.isfinite(stamp) else None
        if (type(code) is not int or (self.settings['mode']=='real' and not self._real_fresh())):
            return {'ok':False, 'code':code, 'ctx':dict(st), 'age_s':age_s,
                    'likely':[], 'why':'故障读数未知或已过期，请等待当前连接的新状态'}
        raw = None
        if code != 0 and self.settings["mode"] == "real" and self.real and self.real.cmd_up:
            rep = await self.real.ask("GetErrorCodeStr", code)
            raw = rep.as_dict()
        ctx = {"code": code, "axis": st.get("err_axis"), "fsm": st.get("fsm"),
               "enabled": st.get("enabled"), "controller_str":
                   (raw or {}).get("fields") if raw else None,
               "axis_err": st.get("axis_err"), "temps": st.get("temps"),
               "note": note}
        if not code:
            return {"ok": True, "code": 0, "advice": "", "ctx": ctx, "age_s":age_s,
                    "why": "当前没有故障码（Error_Code=0）。这条只是把控制器状态读给你看。"}
        return await ai_plan.explain(ctx, config.ai_api_key(),
                                     log=lambda k, m: self.log.add(k, m))

    async def io_probe(self, group="end", bit=0, val=1):
        """IO 标定向导：单路点亮 → 采样前后 DI/AI 变化 → 把差异如实列出来。

        只写用户点的那一路，不盲扫全部通道（盲扫会把吸盘、气阀一起激活）。
        测不出差异就写"没测出"，不编映射表。
        """
        if self.settings["mode"] != "real" or not self.real:
            return {"ok": False, "why": "仿真模式没有真实 IO：这条只能在真机上做"}
        if self._suction_reserved(group,bit):
            return {'ok':False,'why':'此输出属于吸盘，不能用标定试输出绕过气路策略'}
        if not self._need(self.MOTION,'IO标定试输出'):
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

        脱敏：SN/MAC/授权码始终打码，导出不提供绕过脱敏的参数。
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

    def _ai_resources(self):
        snapshot = self.tool_snapshot()
        mapped = []
        if self.settings.get('mode') == 'real':
            for action in ai_plan.TOOL_ACTIONS:
                _, mapping = self.tools.action_mapping(action)
                channel = self.calibrations.items.get('io', {}).get((mapping or {}).get('alias'))
                if (channel and channel.get('direction') == 'output' and type(channel.get('bit')) is int
                        and ((channel.get('group') == 'end' and 0 <= channel['bit'] <= 2)
                             or (channel.get('group') == 'box' and 0 <= channel['bit'] <= 7))):
                    mapped.append(action)
        mounted = snapshot.get('mounted') or {}
        return {'mode': self.settings['mode'], 'tool': snapshot.get('tool'),
                'mounted': {key: mounted.get(key) for key in ('tool_id', 'state', 'revision')},
                'mapped_actions': mapped}

    def _ai_owner_reason(self):
        # Read-only mutual exclusion. In particular do NOT invoke the usual
        # real-jog conflict helper: it requests a follow stop as a side effect.
        if self.hand_follow.active or self.hand_follow.returning or self._hand_centering or self._hand_moving:
            return '手掌/鼠标跟随仍占用机器人，请先主动停止跟随。'
        if self.real_jog_follow.busy:
            return '真机跟随或其停止确认尚未结束，请先主动停止并确认。'
        if self.executor.busy or self.runner.state in ('running', 'waiting', 'paused'):
            return '程序、调试暂停、工具动作或等待仍占用机器人，请先停止原作业。'
        if self.queue_state in ('running', 'paused') or self.sim.traj or self.sim.jog:
            return '点位队列或其他运动仍占用机器人，请先停止原作业。'
        if self.free.get('on'):
            return '手拖示教仍占用机器人，请先退出。'
        if any(task and not task.done() for task in (self._stop_task, self._ready_task, self._speed_task)):
            return '停止、就绪或速度确认尚未结束，请等待完成。'
        if self.settings.get('mode') == 'real':
            if self._stop_interlocked():
                return '停止尚未确认，不能启动 AI 计划。'
            if not self._real_fresh() or self.live_state().get('moving') not in (0, False):
                return '尚无新鲜且静止的真机反馈。'
        return ''

    def ai_revoke(self, request_id=None, plan_id=None):
        # Revoke exactly this request: a late discard must not kill a newer turn.
        if isinstance(request_id, str) and 0 < len(request_id) <= 100:
            cancelled = getattr(self, '_ai_cancelled_requests', [])
            self._ai_cancelled_requests = (cancelled + [request_id])[-64:]
            if getattr(self, '_ai_active_request', None) == request_id:
                self._ai_generation = getattr(self, '_ai_generation', 0) + 1
        for key, proposal in list(self.plans.items()):
            if 'plan_hash' in proposal and ((request_id and proposal.get('request_id') == request_id) or key == plan_id):
                self.plans.pop(key, None)
        pending=getattr(self,'_ai_clarifications',None)
        if pending:pending.revoke(request_id)
        return {'ok': True}

    async def ai(self, text, history, request_id=None, *, clarification=None, allow_model=False):
        # Every new turn revokes AI proposals, including greetings and failures.
        generation = getattr(self, '_ai_generation', 0) + 1
        self._ai_generation = generation
        for key in list(self.plans):
            if 'plan_hash' in self.plans[key]:
                self.plans.pop(key, None)
        if getattr(self, '_ai_busy', False):
            return {"ok": False, "error": "上一条回答尚未结束；旧计划已撤销，请稍后重试。"}
        request_id = request_id if isinstance(request_id, str) and 0 < len(request_id) <= 100 else secrets.token_urlsafe(18)
        if request_id in getattr(self, '_ai_cancelled_requests', []):
            return {'ok': False, 'error': '本次请求已取消，未生成执行许可。'}
        self._ai_active_request = request_id
        self._ai_busy = True
        context, start, epoch = self.plan_context(), self.current_joints(), self.executor.epoch
        config_revision = self._cfg_rev
        session = self.session_id
        resources = self._ai_resources()
        from backend.application.ai_clarification import Clarifications
        if not hasattr(self,'_ai_clarifications'):self._ai_clarifications=Clarifications()
        pending_context=digest([context,resources,config_revision,epoch,session,start,getattr(self.objects,'persisted_generation',0)])
        previous=None
        try:
            if clarification is not None:
                try:text,previous=self._ai_clarifications.resolve(text,clarification,context=pending_context,session=session)
                except ValueError as exc:return {'ok':False,'kind':'clarify','error':str(exc),'plan':[]}
            else:self._ai_clarifications.pending=None
            # 发给模型的设置副本要带上面板配置的 base_url / model，否则两家配置会打架。
            ai_settings = copy.deepcopy(self.settings)
            ai_settings["ai_base_url"], ai_settings["ai_model"] = config.ai_endpoint()
            res = await ai_plan.make_plan(text, history, copy.deepcopy(self.points.list()), self.model,
                                          ai_settings, config.ai_api_key(),
                                          log=lambda k, m: self.log.add(k, m), resources=resources, allow_model=allow_model is True)
        finally:
            self._ai_busy = False
        if generation != self._ai_generation or request_id in getattr(self, '_ai_cancelled_requests', []):
            return {"ok": False, "error": "回答已被较新的请求替代；未发布执行许可。"}
        if res.get('ok') and res.get('kind')=='clarify' and not res.get('unhandled_skills'):
            if pending_context!=digest([self.plan_context(),self._ai_resources(),self._cfg_rev,self.executor.epoch,self.session_id,self.current_joints(),getattr(self.objects,'persisted_generation',0)]):
                return {'ok':False,'kind':'clarify','error':'澄清期间现场已变化，请重新提出完整要求','plan':[]}
            token=self._ai_clarifications.publish(text,context=pending_context,session=session,request_id=request_id,previous=previous)
            if token:
                res['clarification']=token;res['clarification_ttl_s']=max(0,self._ai_clarifications.pending['expires']-time.monotonic())
                res['questions']=list(res.get('questions',[]))+['可明确补充待定速度、点位或等待；其余情况请以“完整要求：”重述本次全部动作。']
        if res.get("ok") and res.get('kind', 'plan') == 'plan' and res.get('plan'):
            if (context != self.plan_context() or digest(resources) != digest(self._ai_resources()) or config_revision != self._cfg_rev
                    or epoch != self.executor.epoch or session != self.session_id
                    or not self.joints_known()
                    or any(abs(a-b) > .05 for a,b in zip(start,self.current_joints()))):
                return {"ok": False, "error": "生成期间起点或场景变化，请重新生成"}
            plan_id = secrets.token_urlsafe(18)
            self.plans[plan_id] = {"context": context, "plan_hash": digest(res["plan"]),
                                   "expires": time.monotonic() + 120, "start": start,
                                   "ai_generation": generation, "epoch": epoch, "session": session,
                                   "config_revision": config_revision}
            self.plans[plan_id].update(request_id=request_id, resources_hash=digest(resources),skill_plan=copy.deepcopy(res.get('skill_plan')))
            while len(self.plans) > 32:
                self.plans.pop(next(iter(self.plans)))
            res["plan_id"] = plan_id
            res["ttl_s"] = 120
        res['request_id'] = request_id
        if res.get('model_request'):res['model_config_rev']=config_revision
        return res

    async def ai_run(self, plan, plan_id=None, *, drawing=None):
        """AI 计划必须人工确认后才走这里；执行前逐条复查白名单与限位。"""
        # An attempted confirmation is one-shot even when busy or unauthorized.
        proposed = self.plans.pop(plan_id, None) if isinstance(plan_id, str) else None
        owner_reason = self._ai_owner_reason()
        if getattr(self, '_ai_run_pending', False) or owner_reason:
            return {"ok": False, "why": owner_reason or "AI 路径检查进行中，请勿重复确认。"}
        if not self._need(self.MOTION, "AI 计划执行"):
            return {"ok": False, "why": self.last_deny}
        if self.safety.get('level') not in (GREEN, 'yellow'):
            return {'ok': False, 'why': '当前安全状态异常或未知，计划已撤销，请处理后重新生成。'}
        if not proposed:
            return {"ok": False, "why": "找不到这份 AI 计划，请重新生成并确认"}
        if time.monotonic() > proposed["expires"]:
            self.plans.pop(plan_id, None)
            return {"ok": False, "why": "AI计划已过期（有效期 120 秒），请重新生成并确认"}
        try:
            unchanged = proposed.get('plan_hash') == digest(plan)
        except (TypeError, ValueError, OverflowError):
            unchanged = False
        if not unchanged:
            return {"ok": False, "why": "确认内容与生成的 AI 计划不一致，请重新生成并确认"}
        if (proposed["context"] != self.plan_context() or proposed.get('session') != self.session_id
                or proposed.get('config_revision') != self._cfg_rev
                or proposed.get('resources_hash') != digest(self._ai_resources())
                or proposed.get('ai_generation') != getattr(self, '_ai_generation', None)
                or proposed.get('epoch') != self.executor.epoch):
            self.log.add("interlock", "AI计划上下文变化：工程运动状态已改变")
            return {"ok": False, "why": "AI计划对应的工程状态已变化（点位、场景、工具、模式或限位），请重新生成并确认"}
        if not self.joints_known() or any(abs(a-b) > .05 for a,b in zip(proposed["start"], self.current_joints())):
            return {"ok": False, "why": "AI计划起点已变化，请重新生成"}
        if proposed.get('skill_plan'):
            self._ai_run_pending=True
            try:
                from backend.application.ai_skills import run
                return await run(self,drawing,plan,proposed)
            except (ValueError,RuntimeError) as exc:return {'ok':False,'why':str(exc)}
            finally:self._ai_run_pending=False
        try:
            plan = ai_plan.validate({"plan": plan, "summary": "人工确认后的计划", "warnings": []},
                                    self.points.list(), self.model, self._ai_resources())["plan"]
            if not plan:
                raise ValueError('空计划不可执行')
        except ValueError as e:
            return {"ok": False, "why": f"计划复查未通过：{e}"}
        if self.objects.any_active():
            return {"ok": False, "why": "场景物体正在运动，请静止后重新生成计划"}
        settings, objects = copy.deepcopy(self.settings), copy.deepcopy(self.objects.list())
        lower, upper = self.limits()
        # 后台路径采样拥有独立 FK 缓存，不与状态循环争用同一查询缓存。
        model = copy.copy(self.motion_model())
        model._fk_key, model._fk_value = None, None
        def preflight():
            cur, issues = list(proposed['start']), []
            for n, op in enumerate(plan):
                if op['op'] == 'speed':
                    settings[config.speed_key(settings['mode'])] = op['pct']
                elif op['op'] == 'movej':
                    report = inspect_path(model, cur, op['joints'], settings, lower, upper, objects)
                    if report.get('risk_detected') is not False:
                        issues.append({'step': n, 'why': '路径采样发现风险', 'report': report})
                    cur = list(op['joints'])
            return issues
        self._ai_run_pending = True
        try:
            issues = await asyncio.to_thread(preflight)
            if issues:
                return {'ok': False, 'why': '路径采样未通过；整份计划未执行，请调整后重新生成。', 'issues': issues}
            if (proposed['context'] != self.plan_context() or proposed['epoch'] != self.executor.epoch
                    or proposed['config_revision'] != self._cfg_rev
                    or proposed['resources_hash'] != digest(self._ai_resources())
                    or proposed['session'] != self.session_id
                    or proposed['ai_generation'] != self._ai_generation
                    or self.objects.any_active() or not self.joints_known()
                    or any(abs(a-b) > .05 for a,b in zip(proposed['start'],self.current_joints()))
                    or self.executor.busy or self.runner.state in ('running', 'waiting', 'paused')
                    or self._ai_owner_reason()
                    or self.safety.get('level') not in (GREEN, 'yellow')
                    or not self._need(self.MOTION, 'AI 计划执行')):
                return {'ok': False, 'why': '路径检查期间权限、起点或现场变化，计划已撤销，请重新生成。'}
            return self._ai_start_checked(plan)
        except Exception as exc:
            self.log.add('ai', f'AI路径检查失败：{type(exc).__name__}')
            return {'ok': False, 'why': '路径检查失败，整份计划未执行，请重新生成。'}
        finally:
            self._ai_run_pending = False

    def _ai_start_checked(self, plan):
        # No await between the final context check and handing steps to runner.
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
            elif o['op'] == 'tool':
                steps.append({'kind': 'tool_action', 'action': o['action'], 'line': 0, 'src': 'TOOL ' + o['action'].upper()})
            elif o['op'] == 'require_tool':
                steps.append({'kind': 'require_tool', 'tool_id': o['tool_id'], 'line': 0, 'src': 'REQUIRE_TOOL ' + o['tool_id']})
            else:
                steps.append({"kind": "wait", "sec": o["sec"], "line": 0, "src": f"WAIT {o['sec']}"})
        self.runner.load(steps)
        if self.runner.start() is False:
            return {'ok': False, 'why': '执行器未接收计划；未自动重试，请核对原作业状态。'}
        self.log.add("ai", f"人工确认，开始执行 AI 计划 {len(steps)} 步")
        return {"ok": True, "count": len(steps), "issues": [], "replan_hint": ""}

    # ----------------------------------------------------------- 真机有限微步跟随
    def _request_real_jog_stop(self, reason):
        self._real_jog_generation += 1
        self._real_jog_checked = None
        return self.real_jog_follow.request_stop(reason)

    def _real_jog_conflict(self, what):
        if not self.real_jog_follow.busy:
            return False
        self.last_deny = "真机跟随或停止确认尚未结束，请先停止跟随：" + what
        self._request_real_jog_stop(self.last_deny)
        self.log.add("interlock", self.last_deny)
        return True

    def _real_jog_state(self):
        """Raw capture times only; no snapshot/driver calls (snapshot reads us)."""
        link = self.real
        if self.settings.get("mode") != "real" or link is None:
            return {"mode": self.settings.get("mode"), "connection": None}
        state = dict(link.snap) if link else {}
        valid = bool(link and link.cmd_up and link.stream_up and link.state_valid())
        q = state.get("joints")
        finite_q = (isinstance(q, (list, tuple)) and len(q) == 6
                    and all(type(v) in (int, float) and math.isfinite(v) for v in q))
        model = self.motion_model()
        pose = model.pose_vec(q) if valid and finite_q else None
        mounted = self.tools.mounted
        tool = self.tools.get(mounted.get("tool_id")) or {}
        confirmed_none = (mounted.get("tool_id") == "none" and tool.get("type") == "none"
                          and mounted.get("state") in (
                              "none_confirmed", "confirmed", "manual_confirmed", "detected_verified")
                          and (tool.get("tcp") or {}).get("status") == "verified"
                          and (tool.get("payload") or {}).get("status") == "verified")
        # Actual tool bodies are not modeled here. Even a verified non-none TCP
        # must not be presented as a verified collision envelope.
        camera = self._hand_camera_reader() if callable(self._hand_camera_reader) else {}
        camera_key = self._hand_camera_identity(camera)
        external = bool(self._stop_interlocked() or self.executor.busy or self.runner.state in ("running", "waiting", "paused")
                        or self.queue_state in ("running", "paused") or self.sim.traj or self.sim.jog
                        or self.hand_follow.active or self.free["on"]
                        or state.get("free_drive") not in (0, False)
                        or (link and (link.jogging is not None or getattr(link, "_jog_intent", None) is not None))
                        or any(task and not task.done() for task in (self._ready_task, self._speed_task)))
        return {"mode": self.settings["mode"],
                "connection": (id(link), link.epoch) if valid else None,
                "context": self.plan_context(), "stamp": state.get("stamp"),
                "frame_seq": link.frame_seq if link else None,
                "q": list(q) if finite_q else None, "pose": pose,
                "enabled": bool(valid and state.get("enabled") == 1),
                "motion_allowed": bool(valid and self.settings["mode"] == "real"
                                       and controls.motion_granted(state.get("fsm"), state.get("enabled"))
                                       and not self.free["on"] and state.get("free_drive") == 0),
                "safety_ok": bool(valid and state.get("err_code") == 0
                                  and state.get("paused") == 0 and state.get("free_drive") == 0
                                  and self.safety.get("level") in (GREEN, "yellow")),
                "idle": bool(valid and state.get("moving") == 0 and state.get("inpos") == 1),
                "override": state.get("override"), "confirmed_tool": bool(confirmed_none),
                "camera_session": camera_key[0] if camera_key else None,
                "camera_aspect": camera_key[1] / camera_key[2] if camera_key else None,
                "camera_ok": camera_key is not None, "busy_external": external}

    def _real_jog_capture(self):
        return (self.real, self.real.epoch if self.real else None, self.plan_context(),
                self._cfg_rev, self._real_jog_generation, self.model.variant)

    def _real_jog_same(self, capture):
        return self.real is capture[0] and self._real_jog_capture() == capture

    @staticmethod
    def _real_jog_gate(state):
        stamp, override = state.get("stamp"), state.get("override")
        return (state.get("mode") == "real" and state.get("connection") is not None
                and type(stamp) in (int, float) and math.isfinite(stamp)
                and 0 <= time.monotonic() - stamp <= .15
                and all(state.get(k) is True for k in (
                    "enabled", "motion_allowed", "safety_ok", "idle", "confirmed_tool", "camera_ok"))
                and state.get("busy_external") is False
                and type(override) in (int, float) and math.isfinite(override)
                and 0 < override <= MAX_OVERRIDE)

    async def _real_jog_solve(self, seed, pose):
        capture = self._real_jog_capture()
        if not self._real_jog_gate(self._real_jog_state()):
            return {"ok": False}
        pending = getattr(self, '_real_jog_ik_task', None)
        if pending is not None and not pending.done():
            return {"ok": False, "reason_code": "ik_busy"}
        model, (lower, upper) = copy.copy(self.motion_model()), self.limits()
        model._fk_key, model._fk_value = None, None
        # Fixed, offline model/limits; no fallback to World.ik_pose, which reads
        # live state and may select a different tool/model while a worker runs.
        cancelled = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(
            model.ik, list(seed), list(pose), list(lower), list(upper),
            iters=45, restarts=False, deadline=time.monotonic()+.1,
            cancelled=cancelled.is_set))
        self._real_jog_ik_task = task
        def retire(done):
            if self._real_jog_ik_task is done:
                self._real_jog_ik_task = None
            if not done.cancelled():
                done.exception()
        task.add_done_callback(retire)
        try:
            # 取消等待不能终止线程；占用槽持续到真正退出，防止重复启动叠加CPU。
            q, _, _, ok = await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        if not self._real_jog_same(capture) or not self._real_jog_gate(self._real_jog_state()):
            return {"ok": False}
        return {"ok": bool(ok), "q": list(q)}

    async def _real_jog_check_step(self, start, end, box):
        self._real_jog_checked = None
        capture = self._real_jog_capture()
        state = self._real_jog_state()
        if not self._real_jog_gate(state):
            return False
        def six(q):
            return (isinstance(q, (list, tuple)) and len(q) == 6
                    and all(type(v) in (int, float) and math.isfinite(v) for v in q))
        if not six(start) or not six(end) or not isinstance(box, dict):
            return False
        if not all(isinstance(box.get(key), (list, tuple)) and len(box[key]) == 3
                   and all(type(v) in (int, float) and math.isfinite(v) for v in box[key])
                   for key in ("min", "max")):
            return False
        if any(a >= b for a, b in zip(box["min"], box["max"])):
            return False
        start, end, box = list(start), list(end), copy.deepcopy(box)
        delta = [b-a for a, b in zip(start, end)]
        if sum(abs(v) > 1e-9 for v in delta) != 1 or max(map(abs, delta)) > MAX_STEP_DEG:
            return False
        if max(abs(a-b) for a, b in zip(start, state["q"])) > .01:
            return False
        model = copy.copy(self.motion_model())
        settings, objects = copy.deepcopy(self.settings), copy.deepcopy(self.objects.list())
        lower, upper = self.limits()
        def check():
            count = max(8, math.ceil(max(map(abs, delta)) / .025))
            for i in range(count + 1):
                q = [a + d*i/count for a, d in zip(start, delta)]
                if any(not lo <= v <= hi for v, lo, hi in zip(q, lower, upper)):
                    return False
                pose = model.pose_vec(q)
                if (len(pose) != 6 or not all(math.isfinite(v) for v in pose)
                        or any(not lo <= v <= hi for v, lo, hi in zip(pose[:3], box["min"], box["max"]))):
                    return False
                caps = model.capsules(q, conservative=True)
                hits = self._object_hits(tool_capsules(model, caps), objects)
                risk = evaluate(model, q, settings, lower, upper, caps=caps, obj_hits=hits)
                if risk["level"] in (RED, ALARM):
                    return False
            return True
        safe = await asyncio.to_thread(check)
        if not safe or not self._real_jog_same(capture):
            return False
        latest = self._real_jog_state()
        if (not self._real_jog_gate(latest)
                or max(abs(a-b) for a, b in zip(start, latest["q"])) > .01):
            return False
        self._real_jog_checked = (capture, start, end)
        return True

    async def _real_jog_move(self, axis, delta, expected):
        # One-use proof of this actual single-axis chord, not an IK chord or a
        # TCP endpoint. Consume before the first await; an ACK is not arrival.
        checked, self._real_jog_checked = self._real_jog_checked, None
        if (checked is None or not self.real_jog_follow.active
                or type(axis) is not int or not 0 <= axis < 6
                or type(delta) not in (int, float) or not math.isfinite(delta)
                or not 0 < abs(delta) <= MAX_STEP_DEG):
            return False
        capture, start, end = checked
        if (not self._real_jog_same(capture) or expected != end
                or abs((end[axis]-start[axis])-delta) > 1e-9
                or any(abs(end[i]-start[i]) > 1e-9 for i in range(6) if i != axis)):
            return False
        state = self._real_jog_state()
        if (not self._real_jog_gate(state)
                or max(abs(a-b) for a, b in zip(start, state["q"])) > .01):
            return False
        link = capture[0]
        if link is not self._real_jog_link or link.epoch != self._real_jog_connection:
            return False
        rep = await link.ask("MoveRelJ", 0, axis, 1 if delta > 0 else 0, abs(delta), timeout=.25)
        return bool(rep.ok and self.real_jog_follow.active and self._real_jog_same(capture)
                    and self._real_jog_gate_after_ack(self._real_jog_state()))

    @staticmethod
    def _real_jog_gate_after_ack(state):
        # Moving=1 is expected after an ACK. Arrival is the driver's separate
        # fresh-stamp/sequence/expected-angle barrier, not a World-side ACK claim.
        return World._real_jog_gate({**state, "idle": True})

    async def _real_jog_stop_motion(self, reason):
        """Stop the owned connection directly; never recurse into stop_execution."""
        self._real_jog_stop_confirmed = False
        self._real_jog_stop_reply = None
        link, epoch = self._real_jog_link, self._real_jog_connection
        command_session = getattr(self, "_real_jog_command_session", None)
        def same_command_session():
            return (self.real is link and link is not None and link.cmd_up
                    and (getattr(link, "command_session", None) == command_session
                         if command_session is not None else link.epoch == epoch))
        if not same_command_session():
            return False
        def environment():
            return digest({"settings": self.settings, "model": self.model.variant,
                           "tool": self.tools.snapshot(), "objects": self.objects.list(),
                           "calibration": self.calibrations.items.get("version", 0)})
        context = environment()
        # 命令会话绑定不同于状态 epoch；丢状态不能阻止向同一原命令通道停机。
        try:
            stop = link.estop(command_session=command_session) if command_session is not None else link.estop()
            rep = await asyncio.wait_for(stop, .6)
            self._real_jog_stop_reply = rep.as_dict()
            stop_epoch = link.epoch if command_session is not None else epoch + 1
            if (self.real is not link or link.epoch != stop_epoch
                    or (command_session is not None and link.command_session != command_session)):
                return False
            # This advance belongs to our own stop, even if its ACK rejects.
            # Keep the same link bound so a manual retry can still stop it.
            self._real_jog_connection = stop_epoch
            if (not rep.ok
                    or environment() != context):
                return False
            ack_at, barrier = time.monotonic(), link.frame_seq
            deadline = ack_at + .6
            while time.monotonic() < deadline:
                if (self.real is not link or link.epoch != stop_epoch or environment() != context
                        or (command_session is not None and link.command_session != command_session)):
                    return False
                state = dict(link.snap)
                stamp = state.get("stamp")
                if (link.state_valid() and type(stamp) in (int, float) and math.isfinite(stamp)
                        and ack_at < stamp <= time.monotonic() and time.monotonic()-stamp <= .15
                        and link.frame_seq > barrier and state.get("moving") == 0 and state.get("inpos") == 1):
                    self._real_jog_stop_confirmed = True
                    return True
                await asyncio.sleep(.01)
        except (asyncio.TimeoutError, ConnectionError):
            return False
        finally:
            if (self.real is link and (link.epoch == epoch + 1 if command_session is None
                                      else link.command_session == command_session)):
                self._real_jog_connection = link.epoch
        return False

    async def real_hand_follow_action(self, action, payload, camera_state=None, *, captured_at=None):
        if action not in ("configure", "start", "sample", "stop", "status"):
            return {"ok": False, "why": "未知真机跟随动作", "state": self.real_jog_follow.snapshot()}
        if not isinstance(payload, dict):
            return {"ok": False, "why": "真机跟随输入无效", "state": self.real_jog_follow.snapshot()}
        if action == "status":
            return {"ok": True, "state": self.real_jog_follow.snapshot()}
        if action == "stop":
            return await self.real_jog_follow.stop(token=payload.get("token"))
        # The installed reader checks color-frame freshness on every driver
        # iteration. camera_state is only a fallback for offline callers.
        if self._hand_camera_reader is None and camera_state is not None:
            self._hand_camera_reader = lambda: camera_state
        if action in ("configure", "start") and not self.real_jog_follow.busy:
            self._real_jog_link = self.real
            self._real_jog_connection = self.real.epoch if self.real else None
            self._real_jog_command_session = getattr(self.real, "command_session", None)
            self._real_jog_checked = None
            self._real_jog_stop_confirmed = False
        if action == "configure":
            enclosure = payload.get("enclosure")
            direction = payload.get("direction", 0)
            if isinstance(enclosure, dict) and direction in (0, 90, 180, 270):
                low, high = enclosure.get("min"), enclosure.get("max")
                if (isinstance(low, (list, tuple)) and isinstance(high, (list, tuple))
                        and len(low) == len(high) == 3
                        and all(type(v) in (int, float) and math.isfinite(v) for v in [*low, *high])):
                    center = [(a+b)/2 for a, b in zip(low, high)]
                    span = [b-a for a, b in zip(low, high)]
                    if direction in (90, 270):
                        span[0], span[1] = span[1], span[0]
                    box_low = [v-d/2 for v, d in zip(center, span)]
                    box_high = [v+d/2 for v, d in zip(center, span)]
                    fence = self.settings["fence"]
                    if (not fence.get("enabled", True) or not self.settings.get("ground_enabled", True)
                            or box_low[2] < 0
                            or any(box_low[i] < fence[axis][0] or box_high[i] > fence[axis][1]
                                   for i, axis in enumerate(("x", "y", "z")))):
                        self._request_real_jog_stop("跟随框超出围栏或地面/围栏检查未开启")
                        return {"ok": False, "why": "跟随框必须在围栏及Z=0以上，且围栏与地面检查须开启",
                                "state": self.real_jog_follow.snapshot()}
            return self.real_jog_follow.configure(payload)
        if action == "start":
            return await self.real_jog_follow.start(payload)
        return self.real_jog_follow.sample(payload, captured_at=captured_at)

    # ----------------------------------------------------------- 共用仿真关节的手掌跟随
    def _hand_model(self):
        return self.motion_model()

    def _stop_hand_follow(self, reason="已停止跟随"):
        self._hand_generation += 1
        startup=getattr(self,'_hand_startup',None)
        if startup and startup.get('phase') not in ('cancelled','failed'):
            startup.update(phase='cancelled',why=reason)
            self._hand_startup_cancelled.append(startup['id'])
        if self._hand_centering:
            self._hand_centering = False
            self.executor.revoke(reason)
            if self.settings.get("mode") == "sim":
                self.sim.stop()
        return self.hand_follow.stop(reason)

    def _hand_startup_state(self,startup_id):
        current=getattr(self,'_hand_startup',None)
        if not current or current['id']!=startup_id:
            cancelled=startup_id in getattr(self,'_hand_startup_cancelled',())
            return {'ok':cancelled,'startup_id':startup_id,'phase':'cancelled' if cancelled else 'missing','why':'' if cancelled else '启动任务已变化'}
        return {'ok':True,'startup_id':startup_id,'phase':current['phase'],'why':current.get('why','')}

    async def hand_center_action(self,action,payload):
        """Camera-independent sim positioning, owned by a client startup ID."""
        if not hasattr(self,'_hand_startup_cancelled'):self._hand_startup_cancelled=deque(maxlen=128)
        startup_id=payload.get('startup_id') if isinstance(payload,dict) else None
        if (not isinstance(startup_id,str) or not 8<=len(startup_id)<=128
                or any(not (c.isascii() and (c.isalnum() or c=='-')) for c in startup_id)):
            return {'ok':False,'why':'启动任务标识无效'}
        record=getattr(self,'_hand_startup',None)
        if action=='center-status':return self._hand_startup_state(startup_id)
        if action=='center-cancel':
            self._hand_startup_cancelled.append(startup_id)
            if not record or record['id']!=startup_id:return self._hand_startup_state(startup_id)
            if record.get('follow') is self.hand_follow:
                self._stop_hand_follow('已取消本次跟随启动')
            else:
                owned=(record.get('task') is self.executor.task and record.get('epoch')==self.executor.epoch)
                record.update(phase='cancelled',why='已取消本次跟随启动')
                self._hand_generation+=1
                if owned and self.executor.busy:
                    curve=self.sim.traj;self._hand_centering=False
                    await self.executor.cancel('已取消本次到中心动作')
                    if self.sim.traj is curve:self.sim.stop()
            return self._hand_startup_state(startup_id)
        if not self._hand_available():return {'ok':False,'why':'到中心等待仅用于仿真'}
        if startup_id in self._hand_startup_cancelled:return self._hand_startup_state(startup_id)
        if record and record['id']==startup_id:return self._hand_startup_state(startup_id)
        if self._hand_busy() or self.hand_follow.active or self._hand_centering:
            return {'ok':False,'why':'请先停止当前作业'}
        allowed={'startup_id','aspect','depth','rotation','direction','enclosure','depth_mode','depth_range'}
        if set(payload)-allowed or not isinstance(payload.get('enclosure'),dict):return {'ok':False,'why':'到中心参数无效'}
        self._stop_hand_follow('正在准备到中心')
        geometry={key:copy.deepcopy(value) for key,value in payload.items() if key!='startup_id'}
        geometry.setdefault('direction',0);geometry.setdefault('depth_mode','scale');geometry.setdefault('depth_range',None)
        model=copy.copy(self._hand_model());model._fk_key=None;model._fk_value=None
        candidate=HandFollow(model)
        record={'id':startup_id,'phase':'preparing','why':'','geometry':geometry,'candidate':candidate,
                'generation':self._hand_generation,'revision':self._cfg_rev,'context':self.plan_context(),'start':self.current_joints()[:],'task':None}
        self._hand_startup=record
        lower,upper=self.limits()
        result=await asyncio.to_thread(candidate.configure,{**geometry,'session':'startup-'+startup_id[:100]},
                                     lower=lower,upper=upper,current_q=record['start'])
        if (self._hand_startup is not record or record['phase']!='preparing'
                or record['generation']!=self._hand_generation or record['revision']!=self._cfg_rev
                or record['context']!=self.plan_context() or not self._hand_available()
                or self._hand_busy() or self.current_joints()!=record['start']):
            if record['phase']=='preparing':record.update(phase='cancelled',why='状态已变化，未移动到中心')
            return self._hand_startup_state(startup_id)
        if not result.get('ok'):
            record.update(phase='failed',why=result.get('why','到中心配置失败'));return self._hand_startup_state(startup_id)|{'ok':False}
        box=candidate.snapshot()['world_bounds'];fence=self.settings['fence']
        low=[fence['x'][0],fence['y'][0],max(0.,fence['z'][0])]
        high=[fence['x'][1],fence['y'][1],fence['z'][1]]
        if any(a<lo or b>hi for a,b,lo,hi in zip(box['min'],box['max'],low,high)):
            record.update(phase='failed',why='跟随框超出安全围栏范围');return self._hand_startup_state(startup_id)|{'ok':False}
        def guard():
            if (self._hand_startup is not record or record['phase']!='centering'
                    or record['context']!=self.plan_context() or record['revision']!=self._cfg_rev
                    or not self._hand_available()):raise RuntimeError('到中心任务已失效')
        def centered():
            guard();self._hand_centering=False;record['phase']='waiting'
        async def run(epoch):
            try:await self._execute_move(candidate._center_q[:],'手掌跟随中心',epoch,execution_guard=guard)
            except BaseException as exc:
                if record['phase']=='centering':record.update(phase='failed',why=str(exc) or '到中心已取消')
                if self.executor.task is asyncio.current_task():self.sim.stop();self._hand_centering=False
                raise
        record.update(phase='centering',epoch=self.executor.epoch);self._hand_centering=True
        if not self.executor.submit('跟随启动到中心',run,centered):
            self._hand_centering=False;record.update(phase='failed',why='机器人已有运动任务')
            return self._hand_startup_state(startup_id)|{'ok':False}
        record['task']=self.executor.task
        return self._hand_startup_state(startup_id)

    def _hand_available(self):
        return self.settings.get("mode") == "sim" and self.real is None

    def _hand_busy(self):
        return ((self.executor.busy and not self._hand_centering) or self.runner.state in ("running", "waiting", "paused")
                or self.queue_state in ("running", "paused")
                or (self.sim.traj is not None and not self._hand_centering)
                or self.sim.jog is not None or self.sim.paused)

    @staticmethod
    def _hand_camera_identity(camera_state):
        active = (camera_state or {}).get("active") or {}
        image = (active.get("images") or {}).get("color") or {}
        session = active.get("session")
        stamp = active.get("last_frame_at")
        width, height, seq = (image.get(key) for key in ("width", "height", "seq"))
        if (active.get("state") != "streaming" or not isinstance(session, str)
                or not 1 <= len(session) <= 128
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in (width, height, seq, stamp))
                or not (1 <= width <= 8192 and 1 <= height <= 8192 and seq >= 1)
                or not 0 <= time.time() - stamp <= 1.5):
            return None
        return session, width, height

    @staticmethod
    def _hand_input_identity(camera_state, input_kind, session):
        # 鼠标有独立来源标识，绝不伪造摄像头采集帧。
        if input_kind == "mouse":
            if (isinstance(session, str) and session.startswith("mouse-")
                    and 12 <= len(session) <= 128
                    and all(c.isascii() and (c.isalnum() or c == "-") for c in session)):
                return session, 640, 480
            return None
        if input_kind != "camera":
            return None
        return World._hand_camera_identity(camera_state)

    def _track_state(self):
        return self.hand_track.snapshot() if self.hand_track is not None else {'active': False, 'reason': ''}

    def hand_track_action(self, action, payload):
        """仿真云台追踪：末端原地转向，让摄像头光轴对准手掌。仅仿真。

        与跟随是两条独立通道：跟随改末端位置，追踪只改朝向，所以两者不共用
        状态机，也不互相停止。
        """
        if not self._hand_available():
            return {"ok": False, "why": "仅限仿真，真机连接时不可追踪", "state": self._track_state()}
        if action == "stop":
            if self.hand_track is not None:
                self.hand_track.stop("已停止追踪")
            if self.sim.traj is not None:
                self.sim.stop()
            return {"ok": True, "state": self._track_state()}
        if action not in ('configure', 'sample') or not isinstance(payload, dict):
            return {"ok": False, "why": "不支持的追踪操作", "state": self._track_state()}
        if self._hand_busy() and action == 'configure':
            return {"ok": False, "why": "请先停止其他作业", "state": self._track_state()}
        model = self.motion_model()
        q = self.current_joints()
        lower, upper = self.limits()
        if action == 'configure':
            try:
                track = HandTrack(model)
                info = track.configure(payload, current_q=q)
            except ValueError as exc:
                self.hand_track = None
                return {"ok": False, "why": str(exc), "state": self._track_state()}
            self.hand_track = track
            return {"ok": True, **info, "state": self._track_state()}
        track = self.hand_track
        if track is None or not track.active:
            return {"ok": False, "why": "请先开始追踪", "state": self._track_state()}
        # 上一次转动还没走完就跳过这一帧：追踪是连续小步，不是每帧重建轨迹。
        if self.sim.traj is not None:
            return {"ok": True, "busy": True, "state": self._track_state()}
        ok, goal, why = track.step(u=payload.get('u'), v=payload.get('v'),
                                   depth=payload.get('depth'),
                                   speed_pct=self.settings.get('sim_speed_pct', 10),
                                   current_q=q, lower=lower, upper=upper)
        if not ok:
            return {"ok": False, "why": why or '追踪失败', "state": self._track_state()}
        moved = goal is not None and max(abs(a - b) for a, b in zip(goal, q)) > .05
        if moved:
            self.sim.move_j(list(goal))
        return {"ok": True, "moved": moved, "state": self._track_state()}

    async def hand_follow_action(self, action, payload, camera_state, *, captured_at=None, input_kind="camera"):
        """A closed sim-only API; center motion uses the normal sim trajectory."""
        if action in ('center','center-status','center-cancel'):
            return await self.hand_center_action(action,payload)
        if not self._hand_available():
            self._stop_hand_follow("仅限仿真，真机连接时不可跟随")
            return {"ok": False, "why": "仅限仿真，请先断开真机", "state": self.hand_follow.snapshot()}
        if action == "stop":
            if not isinstance(payload, dict) or ("token" in payload and payload["token"] != self.hand_follow.token):
                return {"ok": False, "why": "旧跟随已失效", "state": self.hand_follow.snapshot()}
            return self._stop_hand_follow("已停止跟随")
        if action not in ("configure", "start", "sample") or not isinstance(payload, dict):
            return {"ok": False, "why": "不支持的跟随操作"}
        if self._hand_busy():
            self._stop_hand_follow("其他作业正在执行")
            return {"ok": False, "why": "请先停止其他作业", "state": self.hand_follow.snapshot()}
        if action != "configure" and input_kind != getattr(self, "_hand_input_kind", "camera"):
            return {"ok": False, "why": "输入源已变化，请重新开始", "state": self.hand_follow.snapshot()}
        key = self._hand_input_identity(camera_state, input_kind, payload.get("session"))
        if key is None or payload.get("session") != key[0]:
            if action == "sample" and self.hand_follow.active and self.hand_follow._credentials(payload):
                if input_kind == "camera":
                    return self.hand_follow.camera_missing("等待摄像头恢复，保持当前位置")
                return self.hand_follow.return_to_center("等待摄像头恢复，平滑返回中心")
            self._stop_hand_follow("相机画面不可用或已变化")
            return {"ok": False, "why": "请开启有效彩色画面", "state": self.hand_follow.snapshot()}
        if action == "configure":
            startup_id=payload.get('startup_id')
            if startup_id is not None:
                record=getattr(self,'_hand_startup',None)
                try:current_key=self._hand_camera_identity(self._hand_camera_reader()) if self._hand_camera_reader else key
                except Exception:current_key=None
                supplied={k:copy.deepcopy(v) for k,v in payload.items() if k not in ('startup_id','session','aspect')}
                supplied.setdefault('direction',0);supplied.setdefault('depth_mode','scale');supplied.setdefault('depth_range',None)
                expected={k:v for k,v in (record or {}).get('geometry',{}).items() if k!='aspect'}
                if (not record or record['id']!=startup_id or record['phase']!='waiting' or supplied!=expected or current_key!=key
                        or record['context']!=self.plan_context() or record['revision']!=self._cfg_rev
                        or max(abs(a-b) for a,b in zip(self.current_joints(),record['candidate']._center_q))>.05
                        or type(payload.get('aspect')) not in (int,float) or abs(payload['aspect']-key[1]/key[2])>.01):
                    return {'ok':False,'why':'到中心任务或画面已变化，请重新开始'}
                candidate=record['candidate'];candidate.session=key[0];candidate.aspect=float(payload['aspect']);candidate._camera_input=input_kind=='camera'
                candidate.q=self.current_joints()[:];candidate.actual=candidate.model.pose_vec(candidate.q)
                self.hand_follow=candidate;self._hand_generation+=1
                self._hand_camera_key,self._hand_cfg_rev=key,self._cfg_rev;self._hand_input_kind=input_kind
                record.update(phase='configured',follow=candidate)
                return {'ok':True,'state':candidate.snapshot()}
            self._stop_hand_follow("正在建立工作区")
            enclosure = payload.get("enclosure")
            fence = self.settings.get("fence") or {}
            lows = [fence.get("x", [-1200, 1200])[0], fence.get("y", [-1200, 1200])[0],
                    max(0.0, fence.get("z", [-600, 1400])[0])]
            highs = [fence.get("x", [-1200, 1200])[1], fence.get("y", [-1200, 1200])[1],
                     fence.get("z", [-600, 1400])[1]]
            if isinstance(enclosure, dict) and isinstance(enclosure.get("min"), list) and isinstance(enclosure.get("max"), list):
                if (len(enclosure["min"]) != 3 or len(enclosure["max"]) != 3
                        or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                               for v in enclosure["min"] + enclosure["max"])
                        ):
                    return {"ok": False, "why": "跟随框超出安全围栏范围", "state": self.hand_follow.snapshot()}
                center = [(a+b)/2 for a,b in zip(enclosure["min"],enclosure["max"])]
                half = [(b-a)/2 for a,b in zip(enclosure["min"],enclosure["max"])]
                side = payload.get("direction",0) in (90,270)
                span = [half[1],half[0],half[2]] if side else half
                if any(center[i]-span[i] < lows[i] or center[i]+span[i] > highs[i] for i in range(3)):
                    return {"ok": False, "why": "跟随框超出安全围栏范围", "state": self.hand_follow.snapshot()}
            generation, revision = self._hand_generation, self._cfg_rev
            aspect = payload.get("aspect")
            if (type(aspect) not in (int, float) or not math.isfinite(aspect)
                    or abs(aspect - key[1] / key[2]) > .01):
                return {"ok": False, "why": "画面比例已变化，请重新建立工作区"}
            # 隔离 FK 单条记忆化缓存：configure 在工作线程里跑 fk/ik，与事件循环线程
            # 共享同一 RobotModel 会把 (_fk_key,_fk_value) 撕裂成 (keyA,valueB)。
            model = copy.copy(self._hand_model()); model._fk_key, model._fk_value = None, None
            candidate = HandFollow(model)
            candidate._camera_input = input_kind == "camera"
            lower, upper = self.limits()
            result = await asyncio.to_thread(candidate.configure, dict(payload), lower=lower, upper=upper,
                                              current_q=list(self.sim.q))
            # Threaded IK may finish after mode/camera/configuration changes.
            try:
                current_key = (key if input_kind == "mouse" else
                               self._hand_camera_identity(self._hand_camera_reader()) if self._hand_camera_reader else key)
            except Exception:
                current_key = None
            if (generation != self._hand_generation or revision != self._cfg_rev
                    or not self._hand_available() or self._hand_busy() or current_key != key):
                return {"ok": False, "why": "状态已变化，工作区未启用"}
            if result.get("ok"):
                if not isinstance(enclosure, dict):
                    bounds = candidate.snapshot().get("bounds") or {}
                    lower_box, upper_box = bounds.get("min"), bounds.get("max")
                    if (not isinstance(lower_box, list) or not isinstance(upper_box, list)
                            or len(lower_box) != 3 or len(upper_box) != 3
                            or any(lower_box[i] < lows[i] or upper_box[i] > highs[i] for i in range(3))):
                        return {"ok": False, "why": "自动跟随框超出安全围栏范围", "state": self.hand_follow.snapshot()}
                self.hand_follow = candidate
                candidate.q = list(self.sim.q)
                candidate.actual = candidate.model.pose_vec(self.sim.q)
                self._hand_camera_key, self._hand_cfg_rev = key, revision
                self._hand_input_kind = input_kind
                result["state"] = candidate.snapshot()
            return result
        if key != self._hand_camera_key or self._cfg_rev != self._hand_cfg_rev:
            if (action == "sample" and self._cfg_rev == self._hand_cfg_rev
                    and self.hand_follow.active and self.hand_follow._credentials(payload)):
                if input_kind == "camera":
                    return self.hand_follow.camera_missing("摄像头已变化，保持当前位置；停止后可重新配置")
                return self.hand_follow.return_to_center("摄像头已变化，平滑返回中心；停止后可重新配置")
            self._stop_hand_follow("工作区已失效，请重新建立")
            return {"ok": False, "why": "请重新建立工作区", "state": self.hand_follow.snapshot()}
        if action == "start":
            result = self.hand_follow.start(dict(payload), current_q=self.current_joints())
            if not result.get("ok"):
                return result
            startup=getattr(self,'_hand_startup',None)
            if startup and startup.get('follow') is self.hand_follow and startup['phase']=='configured':
                startup['phase']='following';self.hand_follow.status='等待手掌'
                return {'ok':True,'state':self.hand_follow.snapshot()}
            center_q = list(self.hand_follow._center_q)
            self._hand_centering = True

            def centered():
                self._hand_centering = False
                self.hand_follow.q = list(self.sim.q)
                self.hand_follow.actual = self.hand_follow._world_pose(
                    self.sim.q, [0., 0., self.hand_follow.BASE_Z])

            if not self.executor.submit("手掌跟随移动到中心",
                    lambda epoch: self._execute_move(center_q, "手掌跟随中心", epoch), centered):
                self._hand_centering = False
                return self._stop_hand_follow("仿真运动正在执行") | {"ok": False, "why": "仿真运动正在执行"}
            self.log.add("execution", "手掌跟随启动：末端正平滑移动到工作区中心")
            return result
        if action == "sample":
            observation = dict(payload)
            # HTTP verifies the original camera sequence for the real Servo
            # mailbox too; the pure simulation contract accepts observations.
            observation.pop('_verified_frame_seq', None)
            return self.hand_follow.sample(observation, captured_at=captured_at,
                                           camera_gap=input_kind == "camera")
        return getattr(self.hand_follow, action)(dict(payload))

    def _poll_hand_solver(self, now):
        """单槽后台逆解：不在处理请求的事件循环里反复跑牛顿迭代。"""
        self.hand_follow.update_input_state(now)
        if (self.hand_follow.active and self._hand_camera_reader
                and getattr(self, "_hand_input_kind", "camera") == "camera"):
            try:
                key = self._hand_camera_identity(self._hand_camera_reader())
            except Exception:
                key = None
            if key != self._hand_camera_key:
                self.hand_follow.camera_missing("等待摄像头恢复，保持当前位置", now=now)
        pending = getattr(self, '_hand_solver_task', None)
        if pending is not None:
            if not pending.done():
                return
            self._hand_solver_task = None
            try:
                source, token, revision, returning, started, result = pending.result()
            except Exception as exc:
                self.log.add('sys', f'仿真逆解任务失败：{type(exc).__name__}')
            else:
                current = self.hand_follow
                same_request = (current is source and current.active and current.token == token
                    and self._cfg_rev == revision and self._hand_available()
                    and current.returning == returning and not self._hand_centering and not self._hand_busy())
                if same_request and current.accepts_solver_failure(result):
                    # Count late/queued work too. A non-cooperative/overdue worker
                    # cannot create an unlimited discard-and-retry compute loop.
                    current.record_solver_cost(result._request_key(),now-started,now)
                    if now-started>.5:
                        current._solver_cause='budget_exhausted'
                        current.status,current.why='保持位置','局部求解结果已超时，保持位置并有界重试'
                if (current is source and current.active and current.token == token
                        and self._cfg_rev == revision and self._hand_available()
                        and current.returning == returning and now-started <= .5
                        and not self._hand_centering and not self._hand_busy()):
                    goal = result._goal
                    if goal is not None and current.accepts_solver_goal(result):
                        # 只交付目标，不覆盖当前关节、鼠标/相机目标或新样本。
                        # 实际运动仍逐小步做 FK、空间/关节和路径检查。
                        current._goal = list(goal)
                        current._local_target = result._local_target
                        current._solver_cause = result._solver_cause
                        current._wrist_recovery_target = result._wrist_recovery_target
                        current._wrist_recovery_reference = result._wrist_recovery_reference
                        current._reachability_cache = result._reachability_cache
                        current._solver_detail = dict(result._solver_detail)
                        current._subdivision_resume = None
                        current._rejected_target = None
                        if not current.blocked_reason:
                            current.status, current.why = result.status, result.why
                    elif goal is None and current.accepts_solver_failure(result):
                        current._goal = None
                        current._reachability_cache = result._reachability_cache
                        current._rejected_target = result._rejected_target
                        current._rejected_until = result._rejected_until
                        current._solver_cause = result._solver_cause
                        current._solver_detail = dict(result._solver_detail)
                        current._subdivision_resume = result._subdivision_resume
                        current.status, current.why = result.status, result.why
        follow = self.hand_follow
        if (not follow.active or not self._hand_available() or self._hand_centering
                or (follow.waiting and not follow.returning)
                or self._hand_busy() or self._cfg_rev != self._hand_cfg_rev
                or (follow._last_ik is not None and now-follow._last_ik < follow.IK_INTERVAL)):
            return
        if not follow.solver_ready(now):
            return
        # FK 有单条缓存；模型浅拷贝隔离缓存，其余几何只读共享，不复制网格。
        model = copy.copy(follow.model)
        model._fk_key, model._fk_value = None, None
        shadow = copy.deepcopy(follow, {id(follow.model): model})
        shadow._solver_deadline = time.monotonic()+follow.SOLVE_BUDGET_S
        token, revision, returning = follow.token, self._cfg_rev, follow.returning
        follow._last_ik = now
        def solve():
            shadow.solve_goal(now)
            return follow, token, revision, returning, now, shadow
        self._hand_solver_task = asyncio.create_task(asyncio.to_thread(solve))

    def _hand_step_interlock(self, before, candidate, model, objects, lower, upper, settings=None):
        """手掌跟随的逐内部小步互锁；返回 (是否放行, 触发时的安全结果)。

        仿真中若当前已相交，只放行几何上"不加深、不新增相交面"的小步，
        这样被上次碰撞挡住后仍能沿脱离方向退出（SIM05），而不是锁在起点采样上。
        真机分支保持原样：任何 red/alarm 都拦。
        摆放物按全连杆判据（含手腕/小臂），否则手掌本体或连杆穿入物体、
        末端却还在安全余量里时不会被拦（SIM04）。
        """
        settings = settings or self.settings
        # 手掌跟随每步只有零点几度到两三度。原先 0.25° 一个采样会把单次
        # 检查放大到 6~12 个 evaluate（每个约 1ms），60Hz 下直接把运动循环
        # 吃光（表现为"机器人一顿一顿"）。1° 一个采样对这里的微动足够，
        # 真正的粗大位移仍由真实机预检和回放路径逐步检查。
        samples = max(2, math.ceil(max(abs(a-b) for a, b in zip(before, candidate)) / 1.0))
        # 原地不动（只做姿态微调）没有扫掠风险，直接放行。
        if max(abs(a-b) for a, b in zip(before, candidate)) <= 1e-4:
            return True, None
        start_profile = None
        for index in range(1, samples + 1):
            q = [a + (b-a)*index/samples for a, b in zip(before, candidate)]
            caps = model.capsules(q, conservative=True)
            contact_links = object_capsules(model, caps)
            hits = self._object_hits(contact_links, objects)
            risk = evaluate(model, q, settings, lower, upper, caps=caps, obj_hits=hits)
            if risk["level"] in (RED, ALARM):
                if settings.get("mode") == "sim" and (hits or risk.get("collisions")):
                    if start_profile is None:
                        start_caps = model.capsules(list(before), conservative=True)
                        start_profile = contact_profile(
                            model, list(before), settings, start_caps, objects,
                            object_monitored=object_capsules(model, start_caps))
                    verdict = recede_verdict(
                        start_profile,
                        contact_profile(model, q, settings, caps, objects,
                                        monitored=tool_capsules(model, caps),
                                        object_monitored=contact_links),
                        require_progress=False)
                    if verdict["ok"]:
                        continue
                return False, risk
        return True, None

    def _hand_follow_speed(self):
        """跟随专用的速度倍率。

        顶栏的 sim_speed_pct 是给"执行程序"用的：设 10% 是希望示教动作慢一点
        看清楚。但跟随是实时跟着手走的，直接吃这个倍率会变成"手早停了，
        机械臂还在挪"——实测 10% 下跟 64mm 要 1.92 秒，观感就是卡顿、跟不上手。
        这里给跟随一个下限：手再快也跟得上，同时不超过用户设的更高倍率。
        """
        try:
            value = float(self.settings.get("sim_speed_pct", 10))
        except (TypeError, ValueError):
            value = 10.0
        if not math.isfinite(value):
            value = 10.0
        return max(self.HAND_FOLLOW_MIN_SPEED_PCT, min(100.0, value))

    def _tick_hand_follow(self, dt, now):
        self._hand_moving = False
        state = self.hand_follow.snapshot()
        if not state.get("active"):
            return
        if not self._hand_available() or self._hand_busy() or self._cfg_rev != self._hand_cfg_rev:
            self._stop_hand_follow("状态已变化，跟随已停止")
            return
        if self._hand_camera_reader and getattr(self, "_hand_input_kind", "camera") == "camera":
            try:
                key = self._hand_camera_identity(self._hand_camera_reader())
            except Exception:
                key = None
            if key != self._hand_camera_key:
                self.hand_follow.camera_missing("等待摄像头恢复，保持当前位置", now=now)
        if self._hand_centering:
            self.hand_follow.q = list(self.sim.q)
            self.hand_follow.actual = self.hand_follow._world_pose(
                self.sim.q, [0., 0., self.hand_follow.BASE_Z])
            if self.executor.state in ("failed", "cancelled"):
                self._stop_hand_follow(self.executor.error or "移动到中心失败")
            return
        model, objects = self.motion_model(), self.objects.list()
        lower, upper = self.limits()
        blocked = None
        def validate_step(before, candidate):
            nonlocal blocked
            # 正确：逐内部小步检查关节弦线，而不是推进整批后只看终点。
            # 有限离散检查不等于经过认证的连续扫掠碰撞证明。
            ok, risk = self._hand_step_interlock(before, candidate, model, objects, lower, upper)
            if not ok:
                blocked = risk
            return ok
        options = {'solve': False} if getattr(self, '_hand_async_solver', False) else {}
        changed = self.hand_follow.tick(dt, now=now, speed_pct=self._hand_follow_speed(),
                                        validate_step=validate_step, **options)
        if changed:
            # 已接受的中间小步保持有效，绝不发布被拒绝的候选终点。
            self.sim.q = list(self.hand_follow.q)
            self._hand_moving = True
        if blocked is not None:
            reason = '；'.join(blocked.get('reasons') or []) or '安全余量不足'
            category = ('碰撞／接触' if blocked.get('collisions') else
                        '关节限位' if blocked.get('joint_level') in (RED, ALARM) else '碰撞预警')
            self.hand_follow.blocked_reason = f"{category}：{reason}；已暂停"
            self.hand_follow.wait(self.hand_follow.blocked_reason)
            key = (category, tuple((hit.get('type'), hit.get('link')) for hit in blocked.get('collisions', [])))
            if key != getattr(self, '_hand_risk_key', None):
                self._hand_risk_key = key
                self.log.add("interlock", f"跟随已拦截（{category}）：{reason}")
        elif not self.hand_follow.blocked_reason:
            self._hand_risk_key = None

    # ------------------------------------------------------------------ 快照
    def snapshot(self, force_tele=False):
        q = self.current_joints()
        active_model = self.motion_model()
        pose = active_model.flange_pose(q) if self.joints_known() else None
        if self.settings["mode"] == "sim":
            self.hand_follow.q = list(q)
            self.hand_follow.actual = active_model.pose_vec(q)
        real = None
        if self.settings["mode"] == "real" and not self.real:
            # 模式是真机但链路对象不存在：明确报断开，别让界面一片 -- 让人以为在读数
            real = {k: None for k in FAST_KEYS}
            real.update(stream_up=False, cmd_up=False, host=None, update_time="", joints=None)
        elif self.real:
            snapd = self.real.snap
            real = {"stream_up": self.real.stream_up, "cmd_up": self.real.cmd_up,
                    "connection_epoch": self.real.epoch, "frame_seq": self.real.frame_seq,
                    "quality": "valid" if self._real_fresh() else "stale",
                    "last_command": self.real.last_command,
                    "controller_info": self.real.controller_info() if callable(getattr(self.real,"controller_info",None))
                        else {"status":"unknown","version":None,"motion_qualified":False},
                    "joints": snapd.get("joints"),
                    "lag_ms": self.real.lag_ms(), "host": self.real.host,
                    **{k: snapd.get(k) for k in FAST_KEYS}}
        out = {
            "robot_play": ({"planning": self.robot_play.busy,
                            "progress": dict(self.robot_play.progress),
                            "message": self.robot_play.message,
                            "attachment": self.robot_play.display_attachment()} if self.robot_play else None),
            "hand_follow": self.hand_follow.snapshot(),
            "real_hand_follow": self.real_jog_follow.snapshot(),
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
            "tcp": pose, "tcp_frame": {"translation_mm": [active_model.tcp_translation_mm[i] + (20 if i == 2 else 0) for i in range(3)],
                "rpy_deg": active_model.tcp_rpy_deg}, "safety": self.safety,
            "moving": bool(self.sim.traj or self.sim.jog or self.executor.busy or self._hand_moving) if self.settings["mode"] == "sim"
                      else bool(self.real and self.real.snap.get("moving")),
            "paused": bool(self.sim.paused) if self.settings['mode']=='sim' else bool(real and real.get('paused') in (1,True)),
            "exec": {**self.runner.snapshot(), "queue_state": self.queue_state,
                     "execution": self.executor.snapshot(), "stop_result": self.stop_result},
            "real": real, "perm": self.perm, "perm_name": self.PERM_NAMES[self.perm],
            "motion_granted": self.perm >= self.MOTION, "power_granted": True,   # 电源类不设闸
            "ctrl": self.ctrl(), "free": dict(self.free),
            "preview": self.preview_q, "last_deny": self.last_deny,
            "target_evidence": copy.deepcopy(self._target_evidence),
            "target_plan_id": self._preview_target.plan_id if self._preview_target else None,
            "preview_safety": self.preview_safety(),
            "resume": self.resume,
            "sim_state": dict(self.sim_state, is_sim=True),
            "live_fsm": self.live_fsm(), "ready": self.ready_state,
            "ack_left_s": int(max(0.0, self._ack_until - time.monotonic())) if self._armed else 0,
            "objects": self.objects.with_vel(), "obj_hits": self.obj_hits,
            "tools": self.tool_snapshot(),
            "settings": {k: self.settings.get(k) for k in
                         ("fence", "ghost_opacity", "ghost_mode", "ghost_align_tol_deg",
                          "ghost_align_hold_s", "tts", "tts_rate", "vel_source", "limit_source",
                          "safety_yellow_pct", "safety_red_pct", "safety_alarm_pct",
                          "self_collision", "ground_enabled", "real_move_cmd", "custom_limits",
                          "sim_speed_pct", "real_speed_pct", "auto_connect",
                          "robot_ip",
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
        link = {"stream_up": self._real_fresh(),
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



