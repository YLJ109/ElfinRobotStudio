"""FastAPI 入口：REST 下命令、WebSocket 推状态。只提供 API，不托管网页。

网页入口只有 Vite：http://localhost:5173/（热更新）。
8710 的 "/" 返回 404 + JSON 提示，不再呈现第二份界面。

跑法（开发）：
    backend/.venv/Scripts/python.exe -m uvicorn backend.main:app --port 8710
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import secrets
import time
import urllib.parse
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.middleware.gzip import GZipMiddleware

from .core import config
from .paths import WEB_DIST, DATA_DIR
from .build_identity import IDENTITY as BUILD_IDENTITY
from .core.service_lease import ServiceLease
from .bootstrap import ApplicationResources
from .application.vision_pipeline import VisionPipeline
from .infrastructure.vision.process_detector import ProcessHandDetector
from .vision_pipeline_routes import create_pipeline_router
from .application.remote_camera import RemoteCamera
from .remote_camera_routes import create_remote_camera_router, VISITOR_PATHS
from .access import local_peer, trusted_host, remote_readonly_request
from .request_limits import RequestSizeLimit
from .core.program_store import ProgramStore
from .core.cameras import CameraManager
from .core.scenes import SceneStore
from .core.world import World
from .task_routes import create_task_router
from .drawing_routes import create_drawing_router
from .teaching_routes import create_teaching_router
from .vision_routes import create_vision_router
from .core.vision_workspace import VisionWorkspace
from .vision_workspace_routes import create_workspace_vision_router
from .workspace_routes import create_workspace_router
from .data_backup_routes import create_data_backup_router
from .run_journal_routes import create_run_journal_router
from .scene_asset_routes import create_scene_asset_router
from .handling_challenge_routes import create_handling_challenge_router
from .assembly_routes import create_assembly_router
from .routes.vision_tasks import create_vision_tasks_router
from .play_routes import create_play_router
from .robot_play_routes import create_robot_play_router
from .motion_session_routes import create_motion_session_router
from .debug_routes import create_debug_router
from .core.real_servo import real_follow_readiness
from .core.ik_preview import PreviewIKService
from .schemas import Move, IK, Preview, Jog, EndDO, Speed, FrameCalibration, TCPCalibration, IOCalibration, SimInput, FrameTarget, ToolSelect, ToolClear, ToolAction
from .core.calibration import workcell, tcp_fit
from .schemas import validate_settings, RealCommand, Ready, FreeDrive, IOProbe, ToolIOConfirmation
from .core.kinematics import rpy_to_mat, _mat_mul, _mat_trans, mat_to_xyzrpy

config.load_dotenv()


def build_dependencies(*, data_dir=None, world=None, cameras=None):
    """Construct one explicit bundle; no worker or device is started here."""
    from pathlib import Path
    data_dir = Path(data_dir).resolve() if data_dir is not None else DATA_DIR
    camera = cameras if cameras is not None else CameraManager()
    owned_world = world if world is not None else World(data_dir=data_dir,settings_store=config.SettingsStore(data_dir))
    return ApplicationResources(world=owned_world, cameras=camera,
        vision=VisionWorkspace(camera, data_dir / 'vision_workspace.json'),
        pipeline=VisionPipeline(camera, ProcessHandDetector),
        lease=ServiceLease(data_dir / 'control.lock'),programs=ProgramStore(data_dir/'programs.json'),
        scenes=SceneStore(data_dir/'scenes.json',object_sanitizer=owned_world.objects.sanitize),
        ik_previews=PreviewIKService(),data_dir=data_dir)


def create_app(dependencies=None):
    resources = dependencies if dependencies is not None else build_dependencies()
    world, cameras = resources.world, resources.cameras
    programs, scenes = resources.programs, resources.scenes
    vision_workspace, vision_pipeline = resources.vision, resources.pipeline
    ik_previews, service_lease = resources.ik_previews, resources.lease
    @asynccontextmanager
    async def _lifespan(application):
        await _startup()
        try:
            yield
        finally:
            await _shutdown()


    app = FastAPI(title="华沿 Elfin 点位仿真与监控系统", docs_url="/api/docs",
                  openapi_url="/api/openapi.json", lifespan=_lifespan)
    # 三维库压不动，但可以先压再发：636KB 的 JS 走 gzip 只有 ~180KB。
    # 只对 >1KB 的响应动手（minimum_size），免得给 20Hz 的小快照白白加压缩开销。
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(RequestSizeLimit)
    app.state.resources = resources


    @app.exception_handler(ValueError)
    async def invalid_value(request, exc):
        return JSONResponse({"ok": False, "why": str(exc)}, status_code=422)

    # ------------------------------------------------------------------ 访问边界
    # 这套东西能真的让机械臂动，所以默认只认本机：
    #   1) 非回环地址来的请求必须带 HUYAN_TOKEN（放 .env），否则 403，绝不静默开放；
    #   2) 浏览器发出的写请求要校验 Origin —— 挡住"随手打开的网页往本机控制口投递报文"。


    def _is_local(request: Request) -> bool:
        """判断网络对端；未知地址不能自动取得本机权限。"""
        return local_peer((request.client.host if request.client else "") or "")


    def _expected_token():
        return (os.environ.get("HUYAN_TOKEN") or "").strip()


    def _origin_host(origin):
        try:
            net = urllib.parse.urlsplit(origin).netloc
        except ValueError:
            return ""
        return net.lower()


    @app.middleware("http")
    async def access_guard(request: Request, call_next):
        peer = (request.client.host if request.client else "") or ""
        if not trusted_host(request.headers.get("host", ""), peer):
            return JSONResponse({"ok": False, "why": "Host 不在允许列表"}, status_code=400)
        visitor = ((request.method == 'POST' and request.url.path in VISITOR_PATHS)
                   or remote_readonly_request(request))
        if not _is_local(request) and not visitor:
            peer_note = (request.client.host if request.client else "?")
            want = _expected_token()
            # 正确：凭据不能放进 URL，否则会进入访问日志、历史和代理记录。
            got = request.headers.get("x-huayan-token") or ""
            if not want or not secrets.compare_digest(got, want):
                world.log.add("interlock", f"拒绝来自 {peer_note} 的访问：没有有效 token")
                return JSONResponse({"ok": False, "why": "非本机访问需要 HUYAN_TOKEN（写进项目根 .env）"},
                                    status_code=403)
        if request.method != "GET":
            origin = request.headers.get("origin") or ""
            host = (request.headers.get("host") or "").lower()
            # 同源请求 Origin 形如 http://127.0.0.1:8710；不等就说明是别的页面发的
            if origin and _origin_host(origin) != host:
                return JSONResponse({"ok": False, "why": f"跨源写请求被拒：Origin={origin} 与 Host={host} 不一致"},
                                    status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response
    app.include_router(create_vision_router(cameras=cameras))
    app.include_router(create_workspace_vision_router(vision_workspace))

    async def _revoke_vision_motion():
        world._stop_hand_follow('检测来源已变化，请重新启动跟随')
        world._request_real_jog_stop('检测来源已变化，请重新启动跟随')

    app.include_router(create_pipeline_router(vision_pipeline, on_revoke=_revoke_vision_motion))
    resources.remote = RemoteCamera(vision_pipeline,on_revoke=_revoke_vision_motion,workspace=vision_workspace)
    app.include_router(create_remote_camera_router(resources.remote))

    def _hand_camera_state():
        state = cameras.snapshot()
        active = state.get("active")
        # Check the color stream's own freshness, not an IMU heartbeat.
        if active:
            frame = cameras.stream_frame("color", active.get("session"))
            if frame is None or not 0 <= time.time() - frame[1] <= 1.5:
                active["state"] = "error"
        return state

    world._hand_camera_reader = _hand_camera_state

    task_router = create_task_router(world)
    app.include_router(task_router)
    app.include_router(create_teaching_router(world))
    workspace_router = create_workspace_router(world)
    app.include_router(workspace_router)
    app.include_router(create_run_journal_router(world.run_journal))
    backup_router=create_data_backup_router(resources,require_idle=workspace_router.workspace.require_idle)
    app.include_router(backup_router)
    app.state.backup_service=backup_router.backup
    app.include_router(create_scene_asset_router(world,workspace=workspace_router.workspace))
    app.include_router(create_play_router(world))
    app.include_router(create_robot_play_router(world,vision_workspace))
    app.include_router(create_handling_challenge_router(world))
    app.include_router(create_assembly_router(world))
    drawing_router=create_drawing_router(world)
    app.include_router(drawing_router)
    app.state.drawing=drawing_router.drawing
    app.include_router(create_vision_tasks_router(vision_workspace))
    app.include_router(create_motion_session_router(world))
    app.include_router(create_debug_router(world))


    async def _startup():
        if not _expected_token():
            print("[华沿监控台] 未设置 HUYAN_TOKEN：只允许本机访问（127.0.0.1）。"
                  "要用局域网访问请在 .env 里加 HUYAN_TOKEN=<长随机串> 再重启。")
        print("[华沿监控台] 8710 只提供 API，不再托管网页；入口 http://localhost:5173/。"
              "tools/run_backend.ps1 -Web 的生产页面检查因此不再通过，"
              "该模式请改用 start.bat web（Vite 热更新）。")
        await resources.start()
        # 顺手把检测模型预热掉：MediaPipe 首次加载 8~10 秒，原来全压在用户
        # 点"开始跟随"那一刻上。放到启动后后台做，用户第一次用时就是热启动。
        try:
            resources.pipeline.prewarm()
        except Exception as exc:      # 预热失败只是回到冷启动，不影响功能
            print(f"[华沿监控台] 检测模型预热未启动：{type(exc).__name__}")


    async def _shutdown():
        # 绘图子系统关闭失败不能连带跳过核心资源释放（相机/租约/世界线程）。
        try:
            await drawing_router.drawing.close()
        finally:
            await resources.close()


    # ---------------------------------------------------------------------- 状态
    @app.get("/api/build")
    async def build_identity():
        return BUILD_IDENTITY

    @app.get('/api/acceptance')
    async def acceptance():
        from .application.acceptance import read_acceptance
        return read_acceptance(WEB_DIST/'acceptance.json',BUILD_IDENTITY)


    @app.get("/api/state")
    async def state():
        # REST 快照永远带遥测：它是首屏与自检脚本的数据源，不该等下一个 1Hz 窗口。
        return JSONResponse(world.snapshot(force_tele=True))


    @app.get("/api/settings")
    async def get_settings():
        return JSONResponse(world.settings)


    @app.post("/api/settings")
    async def set_settings(patch: dict = Body(...)):
        # mode 不在白名单里：改模式必须走 /api/real/connect|disconnect，
        # 否则会出现"设置说自己是真机、链路对象根本不存在"的两份真相。
        allowed = {"sim_speed_pct", "real_speed_pct", "robot_ip", "vel_source",
                   "limit_source", "fence",
                   "self_collision", "ground_enabled", "ghost_opacity", "ghost_mode", "tts", "tts_rate",
                   "ai_model", "ai_base_url", "real_interpolation", "log_keep",
                   "safety_yellow_pct", "safety_red_pct", "safety_alarm_pct",
                   "real_move_cmd", "custom_limits",
                   "auto_connect", "ghost_align_tol_deg", "ghost_align_hold_s",
                   "free_hold_max_s", "free_force_max_n", "play_len_s", "trail_len", "interaction_range_default"}
        bad = set(patch) - allowed
        if bad:
            return {"ok": False, "why": f"不认识的设置项：{sorted(bad)}"}
        validate_settings(patch)
        # 关闭自动连接是即时控制项：若当前仍有真机链路，先完整断开，
        # 否则 RealLink 的取流重连循环会继续刷“已连接/未连接”日志。
        if patch.get("auto_connect") is False and (world.real or world.settings.get("mode") == "real"):
            await world.disconnect_real()
        out = {}
        for k in ("sim_speed_pct", "real_speed_pct"):
            if k in patch:
                out[k] = world.set_speed(patch.pop(k), target=k.split("_")[0])
        settings = world.update_settings(patch) if patch else world.settings
        return {"ok": True, "settings": {**settings, **out}}


    @app.post("/api/speed")
    async def set_speed(payload: Speed):
        """顶栏滑条专用：只改当前驱动模式那一路的速度，另一路保持原值。"""
        pct = world.set_speed(payload.pct)
        mode = world.settings["mode"]
        task = world._speed_task if mode == "real" else None
        if task:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                return {"ok": False, "why": "倍率请求已被取消或被更新的请求替代"}
        if world.settings["mode"] != mode or world.settings[config.speed_key(mode)] != pct:
            return {"ok": False, "why": "模式或倍率目标已变化，本次结果不能确认"}
        ack = dict(world.speed_ack)
        return {"ok": ack.get("ok") is True, "why": ack.get("why", ""),
                "speed_ack": ack, "speed_pct": pct, "target": config.speed_key(mode),
                "sim_speed_pct": world.settings["sim_speed_pct"],
                "real_speed_pct": world.settings["real_speed_pct"]}


    @app.get("/api/logs")
    async def logs(n: int = Query(200, ge=1, le=2000), kind: str | None = None):
        return {"rows": world.log.tail(n, kind)}


    @app.get("/api/logs/export.csv", response_class=PlainTextResponse)
    async def logs_export(kind: str | None = None):
        return PlainTextResponse(world.log.export_csv(kind), media_type="text/csv")


    @app.get("/api/perm/status")
    async def perm_status():
        """兼容旧调用点：许可现在是**从控制器状态推导**的，没有可设置的档位。"""
        return {"perm": world.perm, "perm_name": world.PERM_NAMES[world.perm],
                "derived": True, "granted": world.motion_ok(),
                "left_s": int(max(0.0, world._ack_until - time.monotonic())) if world._armed else 0}


    @app.post("/api/logs/clear")
    async def logs_clear():
        try:
            world.log.clear_file()
        except OSError:
            return JSONResponse({"ok": False, "why": "日志清空失败，请检查磁盘或文件占用"}, status_code=503)
        world.log.add("sys", "日志已清空")
        return {"ok": True}


    # ---------------------------------------------------------------------- 模型与元信息
    @app.get("/api/model")
    async def model():
        return JSONResponse(world.model.data)


    @app.get("/api/meta")
    async def meta():
        from backend.core import protocol as P
        return {
            "model": {"variant": world.model.variant, "source": world.model.data["source"],
                      "lower": world.model.lower, "upper": world.model.upper,
                      "vmax_urdf": world.model.vmax},
            "conflict": {
                "limits": {"urdf": [[round(a, 1), round(b, 1)] for a, b in
                                    zip(world.model.lower, world.model.upper)],
                           "manual_p54": [list(x) for x in config.MANUAL_LIMITS]},
                "velocity": {"urdf_deg_s": [round(v) for v in world.model.vmax],
                             "manual_E05": config.MANUAL_VEL},
            },
            "commands": {k: {"params": v[0], "doc_page": v[1]} for k, v in P.CMDS.items()},
            "program_syntax": {
                "SPEED / 速度 <1..100>": "设置当前驱动模式的速度（仿真改仿真插值，真机改 SetOverride），另一路不受影响",
                "MOVEJ / 关节移动 <点位名|a,b,c,d,e,f>": "关节运动；真机模式用已验证的 MoveRelJ 逐轴逼近",
                "MOVEL / 直线移动 x,y,z,a,b,c": "笛卡尔直线；仿真按位姿插值走近似直线，真机直接下发控制器 MoveLTo（需先有本机现场标定记录）",
                "DOUT / 输出 <0..2>,<0|1>": "末端 DO（协议 p51 只有 3 路可写）",
                "WAIT / 等待 <0..60>": "等待秒",
                "LOOP / 循环 <1..200> ... END / 结束": "循环，不支持嵌套",
                "PAUSE / 暂停": "等人工继续",
                "REQUIRE_TOOL / 需要工具 <id>": "要求当前已确认的工具档案，否则程序在该步停止",
                "TOOL / 工具 OPEN|CLOSE|VACUUM_ON|VACUUM_OFF": "末端工具动作；仿真只改虚拟状态，真机需要已标定 IO",
            },
            "ai": {"model": config.ai_endpoint()[1], "key_present": bool(config.ai_api_key()),
                   "allowed_ops": sorted(ai_ops())},
            "limit_sources": {k: v[0] for k, v in config.LIMIT_SOURCES.items()},
            "real_move_cmds": {"relj": "MoveRelJ 逐轴逼近（本机已实测）",
                               "movejto": "MoveJTo 绝对点动（协议 p198，本机未验证）"},
            "free_drive": {
                "open": "GrpOpenFreeDriver,nRbtID,;（协议 p26）",
                "close": "GrpCloseFreeDriver,nRbtID,;（协议 p25）",
                "ft_set": "SetFTFreeDriveState / SetFTFreeDriveSpeedMode / SetFTFreeFactor"
                          "（p164/166/167，需机器人在力控状态，否则回 40020）",
                "freedom": "HRSetControlFreedom 逐方向解锁（p151）",
                "readback": "Data Sheet 的 StateAndError.IsFreeDriveMode 与 EndIO.EndButton",
                "modes": "手册 §5.11.5：Basic=只松闸不给重力补偿（臂会坠）；"
                         "Advanced=伺服上电带重力补偿",
                "guard": "本软件：只在就绪+使能态允许开；按住才生效、松手/超时/告警自动关；"
                         "开着手拖时运动指令全部拒发"},
            "power_actions": {"electrify": "上电 Electrify（p21）", "init": "初始化 ConnectToBox（p20）",
                              "enable": "使能 GrpEnable（p30）", "disable": "去使能 GrpDisable（p31）",
                              "blackout": "下电 BlackOut（p22）", "reset": "清错 GrpReset（p32）",
                              "stop": "停止 GrpStop（p33）", "standby": "回 Standby XToStandby（p38）"},
            "safety": {"yellow_pct": world.settings["safety_yellow_pct"],
                       "red_pct": world.settings["safety_red_pct"],
                       "alarm_pct": world.settings["safety_alarm_pct"],
                       "note": "关节限位与围栏/地面余量统一分级：90%黄、95%红并拦截、100%接触告警停止；围栏为四面墙，地面单独检测"},
        }


    @app.get("/api/capabilities")
    async def capabilities():
        """按证据等级公开当前能力目录，避免把协议文档误报成已验证能力。"""
        from .core import robot_real
        from backend.core import protocol as P
        verified = {"SetOverride", "ReadOverride", "ReadCurFSM", "ReadActPos", "MoveRelJ",
                    "GrpStop", "Electrify", "BlackOut", "StartMaster", "GrpEnable", "GrpDisable"}
        commands = []
        for name, (params, page) in P.CMDS.items():
            commands.append({"name": name, "params": params, "doc_page": page,
                             "level": "documented",
                             "historical_hardware_evidence": name in verified,
                             "current_device_qualified": False,
                             "write": name not in {"IsSimulation", "ReadControllerState", "PackageVersion",
                                                    "ReadRobotModel", "ReadOverride", "ReadCurFSM", "ReadActPos", "ReadEmergencyInfo",
                                                    "ReadBrakeStatus", "ReadBoxInfo", "ReadBoxCI", "ReadBoxDI",
                                                    "ReadBoxCO", "ReadBoxDO", "ReadBoxAI", "ReadBoxAO", "ReadEI",
                                                    "ReadEO", "ReadEAI", "GetErrorCodeStr"}})
        fields = [{"section": sec, "source": fld, "key": key, "level": "implemented"}
                  for sec, fld, key, _ in robot_real.FIELD_MAP]
        return {"robot": {"model": world.model.variant, "mode": world.settings["mode"],
                           "connection_epoch": world.real.epoch if world.real else None},
                "commands": commands, "fields": fields,
                "levels": ["documented", "implemented", "offline_verified", "hardware_verified", "qualified"],
                "note": "协议收录不表示已接入或当前设备可用；历史硬件记录未绑定当前设备，不授予运动资格。"}


    @app.get("/api/fields")
    async def fields():
        """官方 Data Sheet 字段 → 本软件快照键 的对照表，带"这一帧到底取到没有"。

        这张表存在的意义：帧里有 60 多个字段，历史上只接了 14 个。
        界面上摊开写清楚，谁漏接了一眼就能看到（配套测试 tools/check_datasheet_fields.py）。
        """
        from .core import robot_real
        live = world.real.snap if (world.real and world.settings["mode"] == "real") else {}
        return {"mode": world.settings["mode"], "linked": bool(live),
                "rows": [{"sec": sec, "field": fld, "key": k, "note": note,
                          "got": live.get(k) is not None if live else None}
                         for sec, fld, k, note in robot_real.FIELD_MAP]}


    @app.get("/api/tools")
    async def tools_list():
        """工具档案与安装状态。读取不触发任何真机写入或动作。"""
        return world.tool_snapshot()


    # ---------------------------------------------------------------------- 摄像头
    def _verified_hand_sample(action, payload):
        if action != "sample" or not isinstance(payload, dict):
            return payload, None
        checked = dict(payload)
        checked.pop('_verified_frame_seq', None)
        frame_seq = checked.pop("frame_seq", None)
        if checked.get("hand_present", True) is False:
            checked["age_ms"] = 0.
            return checked, None
        captured = cameras.observation_stamp(checked.get("session"), frame_seq)
        age = (time.monotonic() - captured) * 1000 if captured is not None else None
        if age is None or not 0 <= age < 300:
            raise ValueError("手掌观测帧不存在或已过期，请等待新帧")
        checked["age_ms"] = age
        checked['_verified_frame_seq'] = frame_seq
        return checked, captured


    @app.post("/api/sim/hand-follow/{action}")
    async def sim_hand_follow(action: str, payload: dict = Body(default={})):
        # 拒绝的帧不能改写另一会话的目标；由已鉴权的缺失采样和看门狗处理丢掌。
        payload, captured = _verified_hand_sample(action, payload)
        return await world.hand_follow_action(action, payload, _hand_camera_state(), captured_at=captured)


    @app.post("/api/sim/tool")
    async def sim_tool_configure(payload: dict = Body(default={})):
        return world.set_sim_tool(payload.get("tool"),expected_revision=payload.get('expected_revision'))

    @app.post('/api/sim/task-tool')
    async def sim_task_tool(payload:dict=Body(...)):
        return world.ensure_sim_task_tool(payload.get('task'))


    @app.post("/api/sim/hand-track/{action}")
    async def sim_hand_track(action: str, payload: dict = Body(default={})):
        # 仿真云台追踪：只改朝向，独立于跟随通道。
        return world.hand_track_action(action, payload)


    @app.post("/api/sim/mouse-follow/{action}")
    async def sim_mouse_follow(action: str, payload: dict = Body(default={})):
        # 独立仿真入口；摄像头/真机入口仍强制验证原始采集时间。
        checked = dict(payload)
        captured = None
        if action == "sample":
            checked.pop("frame_seq", None)
            checked["age_ms"] = 0.
            captured = time.monotonic()
        return await world.hand_follow_action(action, checked, None, captured_at=captured, input_kind="mouse")


    @app.get("/api/cameras")
    async def cameras_state():
        """返回最近一次扫描与当前流状态；读取不会打开或占用设备。"""
        return cameras.snapshot()


    @app.post("/api/cameras/scan")
    def cameras_scan():
        """扫描本机 RealSense 与普通 USB 摄像头。扫描结束会立即释放探测句柄。"""
        return cameras.scan()


    @app.post("/api/cameras/open")
    def cameras_open(payload: dict = Body(default={} )):
        camera_id = str(payload.get("camera_id") or "").strip()
        streams = payload.get("streams")
        if streams is not None and not isinstance(streams, list):
            return {"ok": False, "why": "streams 必须是数组"}
        allowed = {"color", "depth", "infrared", "gyro", "accel"}
        if streams is not None:
            streams = [str(x).lower() for x in streams]
            if any(x not in allowed for x in streams):
                return {"ok": False, "why": "streams 包含不支持的流；未忽略请求流"}
            if not streams:
                return {"ok": False, "why": "至少选择一条有效视频或 IMU 流"}
        # 与仿真来源配置共用互斥锁，两个桌面窗口也不能同时开启两路采集。
        with vision_workspace.lock:
            if vision_workspace.config['input']=='virtual' and vision_workspace.config['enabled']:
                return {'ok':False,'why':'请先关闭末端仿真相机，再开启真实摄像头'}
            return cameras.open(camera_id, streams, payload.get('fps', 60))


    @app.post("/api/cameras/close")
    def cameras_close(payload:dict=Body(default={} )):
        with vision_workspace.lock:
            if set(payload)-{'session'} or ('session' in payload and not isinstance(payload['session'],str)):
                return {'ok':False,'why':'关闭摄像头参数无效'}
            expected=payload.get('session')
            if expected is not None:
                current=cameras.snapshot().get('active')
                if not current or current.get('session')!=expected:
                    return {'ok':False,'why':'摄像头会话已变化，未关闭其他窗口的新会话'}
            return cameras.close()


    @app.get('/api/cameras/frame.jpg')
    def camera_frame(stream: str, session: str):
        data = cameras.image(stream, session)
        if not data:
            return JSONResponse({'ok': False, 'why': '没有有效图像帧'}, status_code=503)
        return Response(data, media_type='image/jpeg', headers={'Cache-Control': 'no-store', 'Content-Encoding': 'identity'})


    @app.get('/api/cameras/stream.mjpg')
    async def camera_stream(request: Request, stream: str, session: str, fps: int = Query(default=0, ge=0, le=1000)):
        if cameras.stream_frame(stream, session) is None:
            return JSONResponse({'ok': False, 'why': '没有有效图像帧、会话已变化或数据已过期'}, status_code=503,
                headers={'Cache-Control': 'no-store'})

        async def frames():
            sequence = None
            next_send = 0.0
            while not await request.is_disconnected():
                packet = cameras.stream_frame(stream, session)
                if packet is None:
                    break
                seq, stamp, data = packet
                now = time.monotonic()
                # 0 follows new captures without an artificial delivery FPS cap.
                # Positive values are an optional transport throttle, independent
                # of detection and robot control. A slow reader still sees latest.
                sent = seq != sequence and (fps == 0 or now >= next_send)
                if sent:
                    sequence, next_send = seq, now + 1 / fps if fps else now
                    yield (f'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(data)}\r\n'
                           f'X-Frame-Seq: {seq}\r\nX-Frame-Timestamp: {stamp:.6f}\r\n\r\n').encode('ascii') + data + b'\r\n'
                # Each consumer fetches the latest cache entry, never a queued old frame.
                # Yield after every send; poll an unchanged cache gently rather
                # than spinning on duplicate images or rebuilding a frame queue.
                await asyncio.sleep((0 if sent else .002) if fps == 0 else
                                    min(.01, max(.002, next_send - time.monotonic())))

        return StreamingResponse(frames(), media_type='multipart/x-mixed-replace; boundary=frame',
            headers={'Cache-Control': 'no-store, no-transform', 'Content-Encoding': 'identity',
                     'X-Accel-Buffering': 'no', 'X-Frame-Policy':'latest-unlimited' if fps==0 else 'latest-throttled'})


    @app.get('/api/cameras/pointcloud')
    def camera_pointcloud(session: str):
        return cameras.pointcloud(session)


    @app.get('/api/cameras/measure')
    def camera_measure(session: str, x: int = Query(ge=0), y: int = Query(ge=0)):
        return cameras.measure(x, y, session)


    @app.post("/api/tools/select")
    async def tools_select(payload: ToolSelect):
        return world.select_tool(payload.tool_id, payload.source, payload.hardware_id, payload.reason)


    @app.post("/api/tools/clear")
    async def tools_clear(payload: ToolClear):
        return world.clear_tool(payload.reason)


    @app.post("/api/tools/action")
    async def tools_action(payload: ToolAction):
        """执行末端工具动作；仿真只改虚拟状态，真机必须有已确认的 IO 映射。"""
        return await world.tool_action(payload.action, payload.duration_ms, side=payload.side)


    @app.post("/api/tools/profile")
    async def tools_profile(payload: dict = Body(...)):
        return world.update_tool_profile(payload)


    @app.post("/api/tools/io-confirm")
    async def tools_io_confirm(payload: ToolIOConfirmation):
        return world.confirm_tool_io(payload.tool_id, payload.revision)


    def ai_ops():
        from .core import ai_plan
        return ai_plan.ALLOWED_OPS


    # ---------------------------------------------------------------------- 运动与许可
    @app.post("/api/perm/ack")
    async def perm_ack(payload: dict = Body(default={})):
        """确认框勾完两项后来换一次性凭据。没勾全不发凭据。"""
        return world.begin_motion_ack(payload.get("acks"))


    @app.post("/api/control")
    async def control(payload: dict = Body(default={})):
        """主按钮唯一入口。前端不再自己挑档位 —— 动作由后端按当前状态推导，
        按错了不猜意图，如实返回"现在该按什么"。"""
        return await world.act(payload.get("key", ""), payload.get("challenge"))


    @app.get("/api/control/state")
    async def control_state():
        return world.ctrl()


    @app.post("/api/free")
    async def free_drive(payload: FreeDrive = Body(default=FreeDrive())):
        """手拖示教：hold=True 表示"正按住"，松手必须再发一次 on=False。"""
        return await world.free_drive(payload.on, payload.hold, payload.source or "软件按钮")


    @app.get("/api/trend")
    async def trend():
        """1Hz 趋势曲线（温度/电流/倍率/模型偏差），前端按需拉，不进每帧快照。"""
        return world.trend_columns()


    @app.get("/api/playback")
    async def playback():
        """回放数据一次取走（抽稀到 ≤1500 帧）：之后拖游标、播放都在浏览器本地算。"""
        return world.playback.slice()


    @app.get("/api/playback/frame")
    async def playback_frame(i: int = Query(0, ge=0)):
        """时间轴游标 → 一帧关节角。回放臂的姿态从这里取。"""
        items = world.playback.items
        if not items:
            return {"ok": False, "why": "还没有回放数据（取流未连或缓冲为空）"}
        k = min(max(0, i), len(items) - 1)
        t, q, lvl = items[k]
        return {"ok": True, "i": k, "t": round(t - world.playback.t0, 2), "q": q, "level": lvl,
                "n": len(items)}


    @app.post("/api/power")
    async def power(payload: dict = Body(...)):
        return await world.power(payload.get("action", ""))


    @app.post("/api/ready")
    async def ready(payload: Ready = Body(default=Ready())):
        return await world.ready(payload.then_enable)


    @app.post("/api/ready/cancel")
    async def ready_cancel():
        return await world.cancel_ready()


    # ---------------------------------------------------------------------- 场景摆放物
    def _play_scene_busy():
        play=world.robot_play
        return bool(play and (play.active or play.held or play.busy))

    @app.get("/api/objects")
    async def objects_list():
        return {"items": world.objects.list()}


    @app.post("/api/objects")
    async def objects_add(payload: dict = Body(...)):
        if _play_scene_busy():return {'ok':False,'why':'取放程序预检/运行或持件中，不能修改场景'}
        world._request_real_jog_stop("摆放物已变化")
        try:
            probe = world.objects.sanitize(payload)
            link = world.robot_overlap(probe)
            if link:
                return {"ok": False, "why": f"该位置与机器人 {link} 重叠，请换个位置"}
            o = world.objects.add(payload)
        except ValueError as e:
            return {"ok": False, "why": str(e)}
        world.log.add("event", f"摆放物新增 {o['name']} {o['kind']} @ {o['xyz']}（仿真道具，不发真机）")
        return {"ok": True, "object": o}


    @app.post("/api/objects/update")
    async def objects_update(payload: dict = Body(...)):
        if _play_scene_busy():return {'ok':False,'why':'取放程序预检/运行或持件中，不能修改场景'}
        world._request_real_jog_stop("摆放物已变化")
        o = world.objects.update(payload.get("id"), payload.get("patch") or {})
        if not o:
            return {"ok": False, "why": "找不到该摆放物"}
        link = world.robot_overlap(o)
        if link:
            return {"ok": True, "object": o, "why": f"已保存，但与机器人 {link} 重叠，请移开"}
        return {"ok": True, "object": o}


    @app.post("/api/objects/remove")
    async def objects_remove(payload: dict = Body(...)):
        if _play_scene_busy():return {'ok':False,'why':'取放程序预检/运行或持件中，不能修改场景'}
        world._request_real_jog_stop("摆放物已变化")
        return {"ok": world.objects.remove(payload.get("id"))}


    @app.post("/api/objects/clear")
    async def objects_clear():
        if _play_scene_busy():return {'ok':False,'why':'取放程序预检/运行或持件中，不能修改场景'}
        world._request_real_jog_stop("摆放物已变化")
        n = world.objects.clear()
        world.log.add("event", f"摆放物已全部清空（{n} 个）")
        return {"ok": True, "removed": n}


    # ---------------------------------------------------------------------- 摆放现场文件
    @app.get("/api/scenes")
    async def scenes_list():
        return {"items": scenes.list()}


    @app.get("/api/scenes/export/{name}")
    async def scenes_export(name: str):
        try:
            item = scenes.get(name)
        except ValueError as exc:
            return {"ok": False, "why": str(exc)}
        if not item:
            return {"ok": False, "why": "找不到现场文件"}
        return {"ok": True, "version": 1, "name": item["name"],
                "objects": item["objects"], "updated_at": item["updated_at"]}


    @app.post("/api/scenes")
    async def scenes_put(payload: dict = Body(default={} )):
        try:
            name = payload.get("name")
            objects = payload["objects"] if "objects" in payload else world.objects.list()
            item = scenes.put(name, objects)
        except (KeyError, ValueError) as exc:
            return {"ok": False, "why": str(exc)}
        return {"ok": True, "item": item, "items": scenes.list()}


    @app.post("/api/scenes/load")
    async def scenes_load(payload: dict = Body(default={} )):
        if _play_scene_busy():return {'ok':False,'why':'取放程序预检/运行或持件中，不能载入场景'}
        try:
            item = scenes.get(payload.get("name"))
        except ValueError as exc:
            return {"ok": False, "why": str(exc)}
        if not item:
            return {"ok": False, "why": "找不到现场文件"}
        world._request_real_jog_stop("工位现场已变化")
        objects = world.objects.replace(item["objects"])
        world.log.add("event", f"已载入摆放现场「{item['name']}」：{len(objects)} 个物体")
        return {"ok": True, "name": item["name"], "objects": objects, "items": scenes.list()}


    @app.post("/api/scenes/import")
    async def scenes_import(payload: dict = Body(default={} )):
        """导入前端解析后的 JSON；保存后仍需用户点击载入，避免误替换当前现场。"""
        try:
            source = payload.get("scene") if isinstance(payload.get("scene"), dict) else payload
            if "scene" in payload and not isinstance(payload["scene"], dict):
                raise ValueError("scene 必须是现场对象")
            name = payload.get("name") or source.get("name")
            objects = source.get("objects", [])
            item = scenes.put(name, objects)
        except (KeyError, ValueError, TypeError) as exc:
            return {"ok": False, "why": str(exc)}
        return {"ok": True, "item": item, "items": scenes.list()}


    @app.delete("/api/scenes/{name}")
    async def scenes_delete(name: str):
        try:
            ok = scenes.remove(name)
        except ValueError as exc:
            return {"ok": False, "why": str(exc)}
        return {"ok": ok, "items": scenes.list(), "why": "" if ok else "找不到现场文件"}


    @app.get("/api/programs")
    async def programs_list():
        return {"items": programs.list()}


    @app.get("/api/programs/{name}")
    async def programs_get(name: str):
        p = programs.get(name)
        return {"ok": bool(p), "src": (p or {}).get("src", "")}


    @app.post("/api/programs")
    async def programs_put(payload: dict = Body(...)):
        try:
            programs.put(payload.get("name", ""), payload.get("src", ""))
        except ValueError as e:
            return {"ok": False, "why": str(e)}
        world.log.add("program", "保存程序 " + payload.get("name", ""))
        return {"ok": True, "items": programs.list()}


    @app.delete("/api/programs/{name}")
    async def programs_del(name: str):
        return {"ok": programs.remove(name), "items": programs.list()}


    @app.post("/api/jog")
    async def jog(payload: Jog):
        return await world.jog(payload.axis, payload.direction, payload.on)


    @app.post("/api/movej")
    async def movej(payload: Move):
        if payload.plan_id is None:
            return {'ok':False,'accepted':False,'why':'请先预览确认目标，再携带计划编号执行'}
        return await world.execute_motion_target(payload.joints,payload.plan_id,allowed_axes=payload.allowed_axes)


    @app.post("/api/ik")
    async def ik(payload: IK):
        """笛卡尔目标 → 关节角（只求解，不动机器；解出来还要人点执行）。"""
        start,context=list(world.current_joints()),world.plan_context()
        solved=await ik_previews.solve(world, payload.target, payload.seed, payload.allow_partial,
                                      position_only=payload.position_only)
        if solved.get('ok'):
            binding=world.register_motion_target(solved,start=start,context=context)
            return {**solved,**binding}
        return solved


    @app.post("/api/fk")
    async def fk(payload: Move):
        """关节角正解，只用于同步点动界面的 TCP 显示，不执行运动。"""
        return await asyncio.to_thread(world.fk_pose, payload.joints)


    @app.post("/api/preview")
    async def preview(payload: Preview):
        """残影（预览目标）：只登记姿态，不下发任何运动指令。

        真机原地不动、假手先摆过去，就是靠这个接口把目标角送到后端的对齐判定里。
        """
        q = payload.joints
        if payload.plan_id is not None:
            return world.confirm_motion_target(q,payload.plan_id,allowed_axes=payload.allowed_axes)
        if q is not None:
            if not world.joints_known():
                return {'ok':True,'preview':world.set_preview(q),'plan_id':None,'executable':False}
            pose=world.motion_model().pose_vec(q)
            binding=world.register_motion_target({'ok':True,'requested':pose,'achieved':pose,'joints':q},
                start=list(world.current_joints()),context=world.plan_context(),allowed_axes=payload.allowed_axes)
            if not binding.get('ok'):return binding
            return world.confirm_motion_target(q,binding['plan_id'],allowed_axes=payload.allowed_axes)
        return {"ok": True, "preview": world.set_preview(q)}


    @app.post("/api/estop")
    async def estop():
        return await world.estop()


    @app.post("/api/io/enddo")
    async def enddo(payload: EndDO):
        return await world.write_end_do(payload.bit, payload.val)


    @app.post("/api/io/probe")
    async def io_probe(payload: IOProbe):
        """标定向导：只点亮用户指定的那一路，回读前后差异。不盲扫。"""
        return await world.io_probe(payload.group, payload.bit, payload.val)


    @app.get("/api/diag")
    async def diag():
        """诊断包始终对 SN/MAC/授权码脱敏；旧 full 查询参数不改变此边界。"""
        data = world.diag_bundle()
        return JSONResponse(data,
                            headers={"Content-Disposition":
                                     'attachment; filename="huayan-diag.json"'})


    # ---------------------------------------------------------------------- 真机
    @app.post("/api/real/connect")
    async def real_connect(payload: dict = Body(default={})):
        connected = await world.connect_real(payload.get("ip"))
        return {"ok": connected, "why": "" if connected else "本进程禁止连接真机（HUYAN_NO_REAL）"}


    @app.post("/api/real/disconnect")
    async def real_disconnect():
        await world.disconnect_real()
        return {"ok": True}


    @app.get("/api/real/hand-follow/status")
    async def real_hand_follow_status():
        """Read-only online follow readiness; never a commissioning unlock."""
        info = world.real.controller_info() if callable(getattr(world.real,"controller_info",None)) else {
            "status":"disconnected","version":None,"motion_qualified":False}
        servo = real_follow_readiness(connected=bool(world.real and world.real.cmd_up and world.real.stream_up))
        state = world.real_jog_follow.snapshot()
        servo = {**servo, 'available': state.get('hardware_qualified', False),
                 'motion_enabled': state.get('phase') == 'following' and state.get('hardware_qualified') is True,
                 'stage': state.get('phase', 'idle'),
                 'missing': [b.get('detail', '') for b in state.get('blockers', [])],
                 'why': state.get('why', '')}
        return {**servo, "ok": True, "driver": state.get('driver', 'StartServo/PushServoJ'),
                "stage": state.get('phase', 'idle'), "blockers": state.get('blockers', []),
                "available": state.get("available", False), "why": state.get("why", ""),
                "motion_enabled": state.get("active", False),
                "state": state, "servo": servo, "controller_info": info}


    @app.post("/api/real/hand-follow/{action}")
    async def real_hand_follow(action: str, payload: dict = Body(default={})):
        try:
            payload, captured = _verified_hand_sample(action, payload)
        except ValueError:
            world._request_real_jog_stop("手掌观测帧已过期，停止跟随")
            raise
        if action == "status":
            return JSONResponse({"ok": False, "why": "状态查询请使用 GET"}, status_code=405,
                                headers={"Allow": "GET"})
        return await world.real_hand_follow_action(action, payload, _hand_camera_state(), captured_at=captured)


    @app.post("/api/real/ask")
    async def real_ask(payload: RealCommand):
        return await world.ask_real(payload.name, *payload.params)


    # ---------------------------------------------------------------------- 点位
    @app.get("/api/points")
    async def points():
        return {"items": world.point_items()}


    @app.post("/api/points")
    async def point_add(payload: dict = Body(...)):
        if "joints" not in payload:
            p = {"ok": False, "why": "没有关节角数据，拒绝保存空点位"}
            return p
        src = payload.get("source") or ("real" if world.settings["mode"] == "real" else "sim")
        try:
            q = Move(joints=payload['joints']).joints
            p = world.points.add(payload.get("name", ""), q, tcp=world.motion_model().flange_pose(q),
                                 source=src, note=payload.get("note", ""))
        except (ValueError, TypeError) as e:
            return {'ok': False, 'why': str(e)}
        world.log.add("point", f"新增点位 {p['name']} 来源={src}")
        return {"ok": True, "point": p}


    @app.post("/api/points/teach")
    async def point_teach(payload: dict = Body(...)):
        q = world.current_joints()
        if not world.joints_known():
            return {"ok": False, "why": "真机取流未连接，当前位置未知，不保存"}
        try:
            p = world.points.add(payload.get("name", ""), q, tcp=world.motion_model().flange_pose(q),
                                 source=world.settings["mode"], note=payload.get("note", "示教录入"))
        except ValueError as e:
            return {'ok': False, 'why': str(e)}
        world.log.add("point", f"示教录入 {p['name']} {[round(v,2) for v in q]}")
        return {"ok": True, "point": p}


    @app.delete("/api/points/{pid}")
    async def point_del(pid: str):
        return {"ok": world.points.remove(pid)}


    @app.patch('/api/points/{pid}')
    async def point_rename(pid: str, payload: dict = Body(...)):
        if world.executor.busy or world.runner.state in ('running', 'waiting', 'paused'):
            return {'ok': False, 'why': '请先停止程序再修改点位名称'}
        try:
            point = world.points.update(pid, payload)
            return {'ok': point is not None, 'point': point, 'why': '' if point else '点位不存在'}
        except ValueError as e:
            return {'ok': False, 'why': str(e)}


    @app.post("/api/points/run")
    async def points_run(payload: dict = Body(...)):
        return await world.run_points(payload.get("ids", []))


    @app.post("/api/points/stop")
    async def points_stop():
        return await world.stop_execution("点位停止")


    @app.post("/api/points/resume")
    async def points_resume():
        return await world.resume_program()


    # ---------------------------------------------------------------------- 程序
    @app.get("/api/program")
    async def program_get():
        return {"runner": world.runner.snapshot()}


    @app.post("/api/program/load")
    async def program_load(payload: dict = Body(...)):
        return await world.load_program(payload.get("src", ""), payload.get("params", {}))


    @app.post("/api/program/validate")
    async def program_validate(payload: dict = Body(...)):
        from .core.program import validate_source
        return validate_source(payload.get("src", ""), world.resolve_point, world.point_items())


    @app.post("/api/program/start")
    async def program_start():
        return world.start_program()


    @app.post("/api/program/stop")
    async def program_stop():
        return await world.stop_execution("程序停止")


    @app.post("/api/program/resume")
    async def program_resume():
        return await world.resume_program()


    @app.post("/api/program/pause")
    async def program_pause():
        return await world.pause_program()


    @app.post("/api/program/resume_last")
    async def program_resume_last():
        """从上次落盘的步数接着跑；没有进度或许可不足都会如实回绝。"""
        return world.resume_last()


    @app.post("/api/program/forget_resume")
    async def program_forget():
        """丢弃上次进度（换了工件、重新对刀之后必须能清掉，否则下次误从旧步数起跑）。"""
        return world.forget_resume()


    # ---------------------------------------------------------------------- AI
    @app.get("/api/ai/config")
    async def ai_config_get():
        runtime = config.ai_runtime()
        return {"ok": True, "configured": bool(config.ai_api_key()),
                "provider": runtime.get("provider", ""), "model": runtime.get("model", ""),
                "base_url": runtime.get("base_url", "")}


    @app.post("/api/ai/config")
    async def ai_config_set(payload: dict = Body(...)):
        """AI 助手面板下发的厂商/模型/Key：只进内存，不落盘、不进日志。"""
        if payload.get("clear") is True:
            config.clear_ai_runtime()
            world.log.add("ai", "AI 模型配置已清空")
            return {"ok": True, "configured": False}
        provider = str(payload.get("provider") or "").strip()[:64]
        model = str(payload.get("model") or "").strip()[:128]
        base_url = str(payload.get("base_url") or "").strip()[:512].rstrip("/")
        api_key = str(payload.get("api_key") or "").strip()[:512]
        if not model:
            return {"ok": False, "why": "缺少模型 id"}
        if not base_url.startswith(("http://", "https://")):
            return {"ok": False, "why": "Base URL 必须是 http(s) 地址"}
        if not api_key:
            return {"ok": False, "why": "缺少 API Key"}
        config.set_ai_runtime(provider, model, base_url, api_key)
        world.log.add("ai", f"AI 模型配置已更新：{provider or '自定义'} / {model}")
        return {"ok": True, "configured": True}


    @app.post("/api/ai/test")
    async def ai_config_test(payload: dict = Body(...)):
        """真的发一条极短对话验证 Key/模型/地址；结果如实回传，不做任何推测。"""
        base_url = str(payload.get("base_url") or "").strip()[:512].rstrip("/")
        model = str(payload.get("model") or "").strip()[:128]
        api_key = str(payload.get("api_key") or "")[:512]
        if not model:
            return {"ok": False, "why": "缺少模型 id"}
        if not base_url.startswith(("http://", "https://")):
            return {"ok": False, "why": "Base URL 必须是 http(s) 地址"}
        if not api_key.strip():
            return {"ok": False, "why": "缺少 API Key"}
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(base_url + "/chat/completions",
                                      headers={"Authorization": f"Bearer {api_key.strip()}"},
                                      json={"model": model, "messages": [{"role": "user", "content": "回复两个字：收到"}],
                                            "temperature": 0, "max_tokens": 16})
                if r.status_code >= 400:
                    return {"ok": False, "why": f"HTTP {r.status_code}：{(r.text or '')[:200]}"}
                choice = (r.json().get("choices") or [{}])[0]
                text = str((choice.get("message") or {}).get("content") or "").strip()
            return {"ok": True, "why": f"连接成功（{model}）：{text[:60] or '无文本回复'}"}
        except Exception as e:
            return {"ok": False, "why": f"{type(e).__name__}：{e}"}


    @app.post("/api/ai/chat")
    async def ai_chat(payload: dict = Body(...)):
        result = await world.ai(payload.get("text", ""), payload.get("history", []), payload.get('request_id'),clarification=payload.get('clarification'),allow_model=payload.get('allow_model') is True)
        result.setdefault('source_kind','not_generated')
        return result


    @app.post('/api/ai/revoke')
    async def ai_revoke(payload: dict = Body(...)):
        return world.ai_revoke(payload.get('request_id'), payload.get('plan_id'))


    @app.post("/api/ai/run")
    async def ai_run(payload: dict = Body(...)):
        return await world.ai_run(payload.get("plan", []), payload.get("plan_id"),drawing=app.state.drawing)


    @app.post("/api/ai/explain_error")
    async def ai_explain(payload: dict = Body(default={})):
        """故障码解说：控制器原始回执与 AI 推测分开返回，界面上不混排。"""
        return await world.explain_error(str(payload.get("note", ""))[:200])


    # ---------------------------------------------------------------------- WebSocket
    @app.post("/api/plans/inspect")
    async def plan_inspect(payload: Move):
        return await world.inspect_plan(payload.joints)


    @app.post("/api/plans/execute")
    async def plan_execute(payload: dict = Body(...)):
        return await world.execute_plan(str(payload.get("plan_id", "")))


    @app.get("/api/blackbox")
    async def blackbox():
        return world.blackbox_bundle()


    @app.get("/api/calibrations")
    async def calibrations():
        return world.calibrations.items


    @app.post("/api/calibrations/frame")
    async def calibrate_frame(payload: FrameCalibration):
        payload = payload.model_dump()
        value = workcell(payload.get("points", []))
        if payload.get("save"):
            world._request_real_jog_stop("工位标定已变化")
            value = world.calibrations.save("frames", str(payload.get("name", "工位")), value)
        return {"ok": True, **value}


    @app.post("/api/calibrations/tcp")
    async def calibrate_tcp(payload: TCPCalibration):
        payload = payload.model_dump()
        value = tcp_fit(payload.get("poses", []))
        if payload.get("save"):
            world._request_real_jog_stop("TCP标定已变化")
            value = world.calibrations.save("tools", str(payload.get("name", "工具")), value)
        return {"ok": True, **value}


    @app.post("/api/calibrations/io")
    async def calibrate_io(payload: IOCalibration):
        payload = payload.model_dump()
        group, direction, bit = payload.get("group"), payload.get("direction"), payload.get("bit")
        limit = 4 if group == "end" and direction == "input" else 3 if group == "end" else 8
        if group not in ("end", "box") or direction not in ("input", "output") or type(bit) is not int or not 0 <= bit < limit:
            raise ValueError("IO方向、分组或通道无效")
        if payload.get("confirmed") is not True:
            raise ValueError("请先核对实际接线并确认；仿真别名也必须明确命名")
        world._request_real_jog_stop("IO标定已变化")
        return {"ok": True, "mapping": world.calibrations.save("io", str(payload.get("name", "")),
                    {"group": group, "direction": direction, "bit": bit, "confirmed_by_user": True})}


    @app.post("/api/sim/input")
    async def simulate_input(payload: SimInput):
        payload = payload.model_dump()
        if world.settings["mode"] != "sim":
            return {"ok": False, "why": "虚拟输入仅在仿真模式可用"}
        key = "end_di" if payload.get("group") == "end" else "box_di"
        bit, value = payload.get("bit"), payload.get("val")
        if type(bit) is not int or not 0 <= bit < len(world.sim_io[key]) or value not in (0, 1):
            raise ValueError("虚拟输入通道或电平无效")
        world.sim_io[key][bit] = value
        return {"ok": True, "sim": True, "inputs": world.sim_io}


    @app.post("/api/calibrations/target")
    async def calibrated_target(payload: FrameTarget):
        payload = payload.model_dump()
        frame = world.calibrations.items["frames"].get(payload.get("frame"))
        tool = world.calibrations.items["tools"].get(payload.get("tool")) if payload.get("tool") else None
        target = IK(target=payload.get("target")).target
        if not frame:
            raise ValueError("工位坐标尚未标定")
        if payload.get("tool") and not tool:
            raise ValueError("指定工具尚未标定")
        # matrices use mm here; the model API also uses mm.
        import math
        local = _mat_mul(_mat_trans(*target[:3]), rpy_to_mat(*[math.radians(v) for v in target[3:]]))
        base = _mat_mul(frame["matrix"], local)
        if tool:
            offset = tool["tcp_offset_mm"]
            base = _mat_mul(base, _mat_trans(*[-v for v in offset]))
        xyz, rpy = mat_to_xyzrpy(base)
        result = await asyncio.to_thread(world.ik_pose, xyz + [math.degrees(v) for v in rpy])
        result["calibration_version"] = world.calibrations.items["version"]
        return result


    @app.websocket("/ws")
    async def ws_endpoint(socket: WebSocket):
        peer = (socket.client.host if socket.client else "") or ""
        if not trusted_host(socket.headers.get("host", ""), peer):
            await socket.close(code=4403)
            return
        local_ws = local_peer(peer)
        if not local_ws and not _expected_token():
            await socket.close(code=4403)
            return
        # HTTP middleware 不会包住 WebSocket 握手；这里单独校验来源，避免外部页面
        # 借浏览器上下文订阅实时状态。允许列表由当前请求 Host 确定，生产代理应先
        # 设置可信 Host，不把任意 Origin 当作授权。
        origin = socket.headers.get("origin") or ""
        host = (socket.headers.get("host") or "").lower()
        if origin and _origin_host(origin) != host:
            await socket.close(code=4403)
            return
        if not local_ws:
            got = socket.headers.get("x-huayan-token") or ""
            if not secrets.compare_digest(got, _expected_token()):
                await socket.close(code=4403)
                return
        await socket.accept()
        q = world.subscribe()
        # 首帧强制带遥测：否则新开的页面要空等一个 1Hz 窗口才能画出体检/健康/力这些卡，
        # 看起来就像"遥测页是空的"。
        config_rev, log_seq = -1, -1
        try:
            await socket.send_json({"type": "state", "data": world.snapshot(force_tele=True)})
            while True:
                try:
                    snap = await asyncio.wait_for(q.get(), timeout=30)
                    packet = dict(snap)
                    if config_rev == snap["config_rev"]:
                        packet.pop("settings", None)
                    config_rev = snap["config_rev"]
                    delta = world.log.since(log_seq)
                    packet.pop("log", None)
                    packet["log_delta"] = delta
                    log_seq = delta["seq"]
                    await socket.send_json({"type": "state", "data": packet})
                except asyncio.TimeoutError:
                    await socket.send_json({"type": "ping"})
                except WebSocketDisconnect:
                    raise
        except WebSocketDisconnect:
            pass
        finally:
            world.unsubscribe(q)


    # ---------------------------------------------------------------------- 入口
    # RUN01：8710 只提供 API。网页入口只有 http://localhost:5173/（Vite 热更新，
    # 由 web/vite.config.* 把 /api 与 /ws 代理到本端口），这里不再托管第二份网页。
    # 旧的生产页面挂载已移除；web/dist 目录与构建产物仍保留，供校验/证据使用。
    @app.get("/")
    async def api_only_root():
        return JSONResponse(
            {"ok": False,
             "why": "本端口只提供 API，不托管网页；请在浏览器打开 http://localhost:5173/"
                    "（start.bat web 会同时拉起 Vite 与后端）。"},
            status_code=404, headers={"Cache-Control": "no-store"})

    app.state.handlers = dict(startup=_startup, shutdown=_shutdown,
        camera_stream=camera_stream,cameras_open=cameras_open,cameras_close=cameras_close,
        hand_camera_state=_hand_camera_state,ws_endpoint=ws_endpoint)
    return app


app = create_app()
world = app.state.resources.world
