"""FastAPI 入口：REST 下命令、WebSocket 推状态、顺带托管前端构建产物。

跑法（开发）：
    .venv/Scripts/python.exe -m uvicorn sim.backend.main:app --port 8710
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import os
import secrets
import time
import urllib.parse
from pathlib import Path

from fastapi import Body, FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from .core import config
from .core.program_store import ProgramStore
from .core.cameras import CameraManager
from .core.scenes import SceneStore
from .core.world import World
from .schemas import Move, IK, Preview, Jog, EndDO, Speed, FrameCalibration, TCPCalibration, IOCalibration, SimInput, FrameTarget, ToolSelect, ToolClear, ToolAction
from .core.calibration import workcell, tcp_fit
from .schemas import validate_settings
from .core.kinematics import rpy_to_mat, _mat_mul, _mat_trans, mat_to_xyzrpy

config.load_dotenv()
app = FastAPI(title="华沿 Elfin 点位仿真与监控系统", docs_url="/api/docs", openapi_url="/api/openapi.json")
# 三维库压不动，但可以先压再发：636KB 的 JS 走 gzip 只有 ~180KB。
# 只对 >1KB 的响应动手（minimum_size），免得给 20Hz 的小快照白白加压缩开销。
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.exception_handler(ValueError)
async def invalid_value(request, exc):
    return JSONResponse({"ok": False, "why": str(exc)}, status_code=422)

# ------------------------------------------------------------------ 访问边界
# 这套东西能真的让机械臂动，所以默认只认本机：
#   1) 非回环地址来的请求必须带 HUYAN_TOKEN（放 .env），否则 403，绝不静默开放；
#   2) 浏览器发出的写请求要校验 Origin —— 挡住"随手打开的网页往本机控制口投递报文"。
LOOPBACK = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}


def _is_local(request: Request) -> bool:
    """判断这次请求是不是本机来的。

    对端地址解析不出 IP 时（TestClient、ASGI 内部调用、进程内直连）说明根本没有
    网络对端，按本机处理 —— 否则会把自家内部调用和反向代理一起挡在门外。
    """
    peer = (request.client.host if request.client else "") or ""
    if peer in LOOPBACK:
        return True
    try:
        return ipaddress.ip_address(peer).is_loopback
    except ValueError:
        # 对端地址根本不是 IP：TestClient、进程内 ASGI 直连这类没有真实网络对端，
        # 按本机处理。拿 Host 头去判断会误伤（TestClient 的 Host 是 testserver），
        # 而反向代理后面更是全被挡死。
        return True


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
    if not _is_local(request):
        peer_note = (request.client.host if request.client else "?")
        want = _expected_token()
        got = request.headers.get("x-huayan-token") or request.query_params.get("token") or ""
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
    if request.url.path in ("/", "/index.html"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response
world = World()
programs = ProgramStore()
cameras = CameraManager()
scenes = SceneStore()
WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"


@app.on_event("startup")
async def _startup():
    if not _expected_token():
        print("[华沿监控台] 未设置 HUYAN_TOKEN：只允许本机访问（127.0.0.1）。"
              "要用局域网访问请在 .env 里加 HUYAN_TOKEN=<长随机串> 再重启。")
    await world.start()


@app.on_event("shutdown")
async def _shutdown():
    cameras.close()
    await world.stop()


# ---------------------------------------------------------------------- 状态
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
               "free_hold_max_s", "free_force_max_n", "play_len_s", "trail_len"}
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
    return {"ok": True, "speed_pct": pct, "target": config.speed_key(world.settings["mode"]),
            "sim_speed_pct": world.settings["sim_speed_pct"],
            "real_speed_pct": world.settings["real_speed_pct"]}


@app.get("/api/logs")
async def logs(n: int = Query(200, le=2000), kind: str | None = None):
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
    world.log.clear_file()
    world.log.add("sys", "日志已清空")
    return {"ok": True}


# ---------------------------------------------------------------------- 模型与元信息
@app.get("/api/model")
async def model():
    return JSONResponse(world.model.data)


@app.get("/api/meta")
async def meta():
    from huayan_panel.core import protocol as P
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
            "MOVEL / 直线移动 x,y,z,a,b,c": "笛卡尔直线；当前仅仿真执行，真机 MoveLTo 尚未完成资格验证",
            "DOUT / 输出 <0..2>,<0|1>": "末端 DO（协议 p51 只有 3 路可写）",
            "WAIT / 等待 <0..60>": "等待秒",
            "LOOP / 循环 <1..200> ... END / 结束": "循环，不支持嵌套",
            "PAUSE / 暂停": "等人工继续",
            "REQUIRE_TOOL / 需要工具 <id>": "要求当前已确认的工具档案，否则程序在该步停止",
            "TOOL / 工具 OPEN|CLOSE|VACUUM_ON|VACUUM_OFF": "末端工具动作；仿真只改虚拟状态，真机需要已标定 IO",
        },
        "ai": {"model": world.settings["ai_model"], "key_present": bool(config.zhipu_key()),
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
                   "note": "按占用比 = |当前角|/同向限位，六轴取最大；碰撞/越限一律按告警级处理"},
    }


@app.get("/api/capabilities")
async def capabilities():
    """按证据等级公开当前能力目录，避免把协议文档误报成已验证能力。"""
    from .core import robot_real
    from huayan_panel.core import protocol as P
    verified = {"SetOverride", "ReadOverride", "ReadCurFSM", "ReadActPos", "MoveRelJ",
                "GrpStop", "Electrify", "BlackOut", "StartMaster", "GrpEnable", "GrpDisable"}
    commands = []
    for name, (params, page) in P.CMDS.items():
        commands.append({"name": name, "params": params, "doc_page": page,
                         "level": "hardware_verified" if name in verified else "documented",
                         "write": name not in {"IsSimulation", "ReadControllerState", "PackageVersion",
                                                "ReadRobotModel", "ReadOverride", "ReadCurFSM", "ReadActPos",
                                                "ReadBrakeStatus", "ReadBoxInfo", "ReadBoxCI", "ReadBoxDI",
                                                "ReadBoxCO", "ReadBoxDO", "ReadBoxAI", "ReadBoxAO", "ReadEI",
                                                "ReadEO", "ReadEAI", "GetErrorCodeStr"}})
    fields = [{"section": sec, "source": fld, "key": key, "level": "implemented"}
              for sec, fld, key, _ in robot_real.FIELD_MAP]
    return {"robot": {"model": world.model.variant, "mode": world.settings["mode"],
                       "connection_epoch": world.real.epoch if world.real else None},
            "commands": commands, "fields": fields,
            "levels": ["documented", "implemented", "offline_verified", "hardware_verified", "qualified"],
            "note": "documented 不表示本机固件支持；hardware_verified 只代表已记录的特定硬件条件。"}


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
        streams = [str(x).lower() for x in streams if str(x).lower() in allowed]
        if not streams:
            return {"ok": False, "why": "至少选择一条有效视频或 IMU 流"}
    return cameras.open(camera_id, streams)


@app.post("/api/cameras/close")
def cameras_close():
    return cameras.close()


@app.get('/api/cameras/frame.jpg')
def camera_frame(stream: str, session: str):
    data = cameras.image(stream, session)
    if not data:
        return JSONResponse({'ok': False, 'why': '没有有效图像帧'}, status_code=503)
    return Response(data, media_type='image/jpeg', headers={'Cache-Control': 'no-store', 'Content-Encoding': 'identity'})


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
    return await world.tool_action(payload.action, payload.duration_ms)


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
async def free_drive(payload: dict = Body(default={})):
    """手拖示教：hold=True 表示"正按住"，松手必须再发一次 on=False。"""
    return await world.free_drive(bool(payload.get("on")), bool(payload.get("hold")),
                                  str(payload.get("source") or "软件按钮"))


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
async def ready(payload: dict = Body(default={})):
    return await world.ready(payload.get("then_enable", True))


@app.post("/api/ready/cancel")
async def ready_cancel():
    return await world.cancel_ready()


# ---------------------------------------------------------------------- 场景摆放物
@app.get("/api/objects")
async def objects_list():
    return {"items": world.objects.list()}


@app.post("/api/objects")
async def objects_add(payload: dict = Body(...)):
    try:
        o = world.objects.add(payload)
    except ValueError as e:
        return {"ok": False, "why": str(e)}
    world.log.add("event", f"摆放物新增 {o['name']} {o['kind']} @ {o['xyz']}（仿真道具，不发真机）")
    return {"ok": True, "object": o}


@app.post("/api/objects/update")
async def objects_update(payload: dict = Body(...)):
    o = world.objects.update(payload.get("id"), payload.get("patch") or {})
    if not o:
        return {"ok": False, "why": "找不到该摆放物"}
    return {"ok": True, "object": o}


@app.post("/api/objects/remove")
async def objects_remove(payload: dict = Body(...)):
    return {"ok": world.objects.remove(payload.get("id"))}


@app.post("/api/objects/clear")
async def objects_clear():
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
    try:
        item = scenes.get(payload.get("name"))
    except ValueError as exc:
        return {"ok": False, "why": str(exc)}
    if not item:
        return {"ok": False, "why": "找不到现场文件"}
    objects = world.objects.replace(item["objects"])
    world.log.add("event", f"已载入摆放现场「{item['name']}」：{len(objects)} 个物体")
    return {"ok": True, "name": item["name"], "objects": objects, "items": scenes.list()}


@app.post("/api/scenes/import")
async def scenes_import(payload: dict = Body(default={} )):
    """导入前端解析后的 JSON；保存后仍需用户点击载入，避免误替换当前现场。"""
    try:
        name = payload.get("name") or (payload.get("scene") or {}).get("name")
        source = payload.get("scene") if isinstance(payload.get("scene"), dict) else payload
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
    return await world.move_joints(payload.joints)


@app.post("/api/ik")
async def ik(payload: IK):
    """笛卡尔目标 → 关节角（只求解，不动机器；解出来还要人点执行）。"""
    return await asyncio.to_thread(world.ik_pose, payload.target, payload.seed, payload.allow_partial)


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
    return {"ok": True, "preview": world.set_preview(q)}


@app.post("/api/estop")
async def estop():
    return await world.estop()


@app.post("/api/io/enddo")
async def enddo(payload: EndDO):
    return await world.write_end_do(payload.bit, payload.val)


@app.post("/api/io/probe")
async def io_probe(payload: dict = Body(default={})):
    """标定向导：只点亮用户指定的那一路，回读前后差异。不盲扫。"""
    return await world.io_probe(str(payload.get("group") or "end"),
                                int(payload.get("bit", 0)), int(payload.get("val", 1)))


@app.get("/api/diag")
async def diag(full: int = 0):
    """一键诊断包（JSON 下载）。默认对 SN/MAC/授权码打码，full=1 才给完整值。"""
    data = world.diag_bundle()
    if full:
        data["note"] = "含设备标识完整值：外发前请自行确认接收方"
    return JSONResponse(json.dumps(data, ensure_ascii=False, indent=1),
                        headers={"Content-Disposition":
                                 'attachment; filename="huayan-diag.json"'})


# ---------------------------------------------------------------------- 真机
@app.post("/api/real/connect")
async def real_connect(payload: dict = Body(default={})):
    await world.connect_real(payload.get("ip"))
    return {"ok": True}


@app.post("/api/real/disconnect")
async def real_disconnect():
    await world.disconnect_real()
    return {"ok": True}


@app.post("/api/real/ask")
async def real_ask(payload: dict = Body(...)):
    return await world.ask_real(payload["name"], *payload.get("params", []))


# ---------------------------------------------------------------------- 点位
@app.get("/api/points")
async def points():
    return {"items": world.points.list()}


@app.post("/api/points")
async def point_add(payload: dict = Body(...)):
    if "joints" not in payload:
        p = {"ok": False, "why": "没有关节角数据，拒绝保存空点位"}
        return p
    src = payload.get("source") or ("real" if world.settings["mode"] == "real" else "sim")
    try:
        q = [float(v) for v in payload['joints']]
        if len(q) != 6 or not all(math.isfinite(v) for v in q):
            raise ValueError('点位需要六个有限的关节角')
        p = world.points.add(payload.get("name", ""), q, tcp=world.model.flange_pose(q),
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
        p = world.points.add(payload.get("name", ""), q, tcp=world.model.flange_pose(q),
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
    if not world._need(world.MOTION, "继续程序") or world.executor.busy:
        return {"ok": False, "why": world.last_deny or "当前任务尚未完成"}
    world.runner.resume()
    if world.settings["mode"] == "sim":
        world.sim.paused = False
    return {"ok": True, "state": world.runner.state}


# ---------------------------------------------------------------------- 程序
@app.get("/api/program")
async def program_get():
    return {"runner": world.runner.snapshot()}


@app.post("/api/program/load")
async def program_load(payload: dict = Body(...)):
    return await world.load_program(payload.get("src", ""), payload.get("params", {}))


@app.post("/api/program/start")
async def program_start():
    return world.start_program()


@app.post("/api/program/stop")
async def program_stop():
    return await world.stop_execution("程序停止")


@app.post("/api/program/resume")
async def program_resume():
    if not world._need(world.MOTION, "继续程序") or world.executor.busy:
        return {"ok": False, "why": world.last_deny or "当前任务尚未完成"}
    world.runner.resume()
    if world.settings["mode"] == "sim":
        world.sim.paused = False
    return {"ok": True, "state": world.runner.state}


@app.post("/api/program/resume_last")
async def program_resume_last():
    """从上次落盘的步数接着跑；没有进度或许可不足都会如实回绝。"""
    return world.resume_last()


@app.post("/api/program/forget_resume")
async def program_forget():
    """丢弃上次进度（换了工件、重新对刀之后必须能清掉，否则下次误从旧步数起跑）。"""
    return world.forget_resume()


# ---------------------------------------------------------------------- AI
@app.post("/api/ai/chat")
async def ai_chat(payload: dict = Body(...)):
    return await world.ai(payload.get("text", ""), payload.get("history", []))


@app.post("/api/ai/run")
async def ai_run(payload: dict = Body(...)):
    return await world.ai_run(payload.get("plan", []), payload.get("plan_id"))


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
        value = world.calibrations.save("frames", str(payload.get("name", "工位")), value)
    return {"ok": True, **value}


@app.post("/api/calibrations/tcp")
async def calibrate_tcp(payload: TCPCalibration):
    payload = payload.model_dump()
    value = tcp_fit(payload.get("poses", []))
    if payload.get("save"):
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
    local_ws = peer in LOOPBACK
    if not local_ws:
        try:
            local_ws = ipaddress.ip_address(peer).is_loopback
        except ValueError:
            local_ws = True     # 进程内/无网络对端
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
        got = socket.query_params.get("token") or socket.headers.get("x-huayan-token") or ""
        if not secrets.compare_digest(got, _expected_token()):
            await socket.close(code=4403)
            return
    await socket.accept()
    q = world.subscribe()
    # 首帧强制带遥测：否则新开的页面要空等一个 1Hz 窗口才能画出体检/健康/力这些卡，
    # 看起来就像"遥测页是空的"。
    await socket.send_json({"type": "state", "data": world.snapshot(force_tele=True)})
    config_rev, log_seq = -1, -1
    try:
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


# ---------------------------------------------------------------------- 前端静态资源
if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
else:
    @app.get("/")
    async def no_frontend():
        return PlainTextResponse(
            "前端还没构建。先在 sim/web 里执行 npm install && npm run build，"
            "或用开发模式：cd sim/web && npm run dev（默认 http://localhost:5173，后端 http://127.0.0.1:8710）")
