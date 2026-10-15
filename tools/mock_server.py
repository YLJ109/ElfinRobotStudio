"""假控制器：在 127.0.0.1 复刻已实测的 V6 行为，用于无真机时自测上位机。

复刻范围（全部来自本仓库实测，不是臆造）：
  * 10004/10005/10006 —— LTBR + 小端总长 + 小端净数据长度 + JSON，周期 50/100/200ms
  * 10014/10015/10016 —— 同帧头，定长 944B 二进制
  * 10003 —— ASCII 指令串行处理，回执 名称,OK,...,; / 名称,Fail,码,说明,;
  * 10001 —— 快速端口，ReadBrakeStatus 之类返回 20099（实测行为）
  * 命令口在有/无使能两种状态下是否推 Data Sheet 不同（实测：未使能时不推）

状态机码沿用 SDK 注释：7 未上电 / 14 已上电 / 24 初始化成功 / 33 已就绪。
"""

from __future__ import annotations

import json
import os
import socket
import socketserver
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from huayan_panel.core import protocol as P  # noqa: E402  用上位机同一张指令表，避免参数位错位

MAGIC = b"LTBR"


def named(name, args):
    """按 protocol.CMDS 的参数签名把逗号字段解析成字典（真机格式：名称,p1,...,;）。"""
    keys = [k for k in P.CMDS.get(name, ("", 0))[0].split(",") if k]
    return dict(zip(keys, args[1:1 + len(keys)]))

sim = {
    "joints": [-3.124, -18.553, -122.896, 1.189, -74.089, -113.853],
    "target": None,
    "fsm": 7,
    "enabled": 0,
    "moving": 0,
    "inpos": 1,
    "err_axis": 0,
    "err_code": 0,
    "override": 0.01,
    "brake": [0, 0, 0, 0, 0, 0],
    "temps": [40.0, 40.0, 43.0, 43.0, 46.0, 46.0],
    "currents": [0.0, -0.75, 2.29, 0.21, 0.08, 0.01],
    "volt48": 48.06,
    "ecat_fps": 1006,
    "lost": 0,
    "safeguard": 0,
    "end_do": [0, 0, 0, 0],
    "end_di": [0, 0, 0, 0],
    "end_btn": [0, 0, 0, 0],
    "end_ai": [0.020, 0.012],
    "box_do": [0] * 8,
    "box_di": [0] * 8,
    "box_ci": [0] * 8,
    "box_co": [0] * 8,
    "jog": None,            # (axis, direction)
    "jog_last_hb": 0.0,
    "model": "MOCK-E05-1195_Pro",
    "version": "HR-mock",
    "lock": threading.RLock(),
}

LIMITS = [(-360, 360), (-135, 135), (-153, 153), (-360, 360), (-180, 180), (-360, 360)]
JOINT_MAX_VEL = 10.0          # 协议 p211/213：点动最大不超过 10°/s
HB_DEADLINE = 0.20            # 协议 p215：LongMoveEvent 周期不得超过 200ms


def pcs_base(j):
    """假的正运动学，只保证数值随关节变化，不做真实 DH。"""
    x = 300 + 90 * (j[1] / 90.0) - 60 * (j[2] / 90.0)
    return [round(x, 3), round(j[0] * 0.5, 3), round(300 + j[2] * 0.4, 3),
            round(180 - abs(j[3]) * 0.1, 3), round(-j[4] * 0.05, 3), round(j[5] * 0.2, 3)]


def frame(payload: bytes) -> bytes:
    return MAGIC + struct.pack("<II", 12 + len(payload), len(payload)) + payload


def datasheet() -> dict:
    with sim["lock"]:
        j = list(sim["joints"])
        return {
            "PosAndVel": {
                "Actual_Position": [f"{v:.3f}" for v in j] + [f"{v:.3f}" for v in pcs_base(j)],
                "Actual_Joint_Current": [f"{v:.3f}" for v in sim["currents"]],
                "Actual_Joint_Velocity": ["0.000"] * 6,
                "Actual_Joint_Acceleration": ["0.000"] * 6,
                "Actual_PCS_TCP": [f"{v:.3f}" for v in pcs_base(j)],
                "Actual_PCS_Base": [f"{v:.3f}" for v in pcs_base(j)],
                "Actual_PCS_Tool": ["0.000"] * 6,
                "Actual_Override": f"{sim['override']:.3f}",
            },
            "EndIO": {"EndDI": list(sim["end_di"]), "EndDO": list(sim["end_do"]),
                      "EndButton": list(sim["end_btn"]), "EnableEndBTN": 0,
                      "EndAI": [f"{v:.3f}" for v in sim["end_ai"]]},
            "ElectricBoxIO": {"BoxCI": list(sim["box_ci"]), "BoxCO": list(sim["box_co"]),
                              "BoxDI": list(sim["box_di"]), "BoxDO": list(sim["box_do"]),
                              "Conveyor": "0.000", "Encode": 0},
            "ElectricBoxAnalogIO": {"BoxAnalogOutMode_1": 0, "BoxAnalogOutMode_2": 0,
                                    "BoxAnalogOut_1": "0.000", "BoxAnalogOut_2": "0.000",
                                    "BoxAnalogIn_1": "0.000", "BoxAnalogIn_2": "0.000"},
            "StateAndError": {"robotState": sim["fsm"], "robotEnabled": sim["enabled"],
                              "robotMoving": sim["moving"], "robotBlendingDone": 1,
                              "InPos": sim["inpos"], "Error_AxisID": sim["err_axis"],
                              "Error_Code": sim["err_code"], "IsReduceMode": 0,
                              "IsFreeDriveMode": 0, "AutoMode": "1",
                              "BrakeState": sim["brake"], "nAxisStatus": [3] * 6,
                              "nAxisErrorCode": [0] * 6},
            "FTData": {"FTControlState": 0},
            "HardLoad": {"EtherCAT_TotalFrame": 1149179, "EtherCAT_FramesPerSecond": sim["ecat_fps"],
                         "EtherCAT_TotalLostFrame": sim["lost"], "EtherCAT_TxErrorFrame": 0,
                         "EtherCAT_RxErrorFrame": 0, "Box48IN_Voltage": f"{sim['volt48']:.3f}",
                         "Box48IN_Current": "0.000", "Box48Out_Voltage": "48.108",
                         "Box48Out_Current": "0.000",
                         "Joint_temperature": [f"{v:.3f}" for v in sim["temps"]],
                         "Joint_Voltage": ["0.000"] * 6},
            "RobotAuthorization": {"DeviceSN": "MOCK0000000", "SystemDate": "20261011",
                                   "AuthorizedTimeLeftMinutes": "Unlimited"},
            "Script": {"errorCode": "0", "ScriptChanged": "1"},
            "MsgTitle": {"Stamp": str(int(time.time() * 1000)),
                         "UpdateTime": time.strftime("%H:%M:%S.") + f"{int(time.time() * 1000 % 1000):03d}"},
        }


def physics():
    """按 10°/s 限速趋近目标；心跳断了就停长点动。"""
    while True:
        time.sleep(0.02)
        with sim["lock"]:
            if sim["target"] is not None:
                axis, tgt = sim["target"]
                cur = sim["joints"][axis]
                d = tgt - cur
                step = JOINT_MAX_VEL * 0.02 * max(1.0, sim["override"] * 10)
                if abs(d) <= step:
                    sim["joints"][axis] = round(tgt, 3)
                    sim["target"] = None
                    sim["moving"] = 0
                    sim["inpos"] = 1
                else:
                    sim["joints"][axis] = round(cur + (step if d > 0 else -step), 3)
                    sim["moving"] = 1
                    sim["inpos"] = 0
            if sim["jog"] and time.monotonic() - sim["jog_last_hb"] > HB_DEADLINE:
                sim["jog"] = None
                sim["moving"] = 0
                sim["inpos"] = 1
            elif sim["jog"]:
                axis, direction = sim["jog"]
                delta = (1 if direction else -1) * JOINT_MAX_VEL * 0.02
                nxt = sim["joints"][axis] + delta
                lo, hi = LIMITS[axis]
                if lo <= nxt <= hi:
                    sim["joints"][axis] = round(nxt, 3)
                    sim["moving"] = 1
                    sim["inpos"] = 0
                else:
                    sim["jog"] = None
                    sim["err_code"] = 1
                    sim["err_axis"] = axis + 1


def handle_cmd(name, args, port):
    """返回回执文本（不含结尾 ,;）。"""
    FAST_ONLY_OK = {"IsSimulation", "ReadControllerState", "PackageVersion", "ReadRobotModel",
                    "ReadCurFSM", "ReadOverride", "ReadActPos", "GetErrorCodeStr"}
    if port == 10001 and name not in FAST_ONLY_OK:
        return f"{name},Fail,20099,This command is not allowed to run through this socket port,"
    if name not in KNOWN:
        return f"{name},Fail,20007,command format error,"

    with sim["lock"]:
        if name in ("Electrify",):
            if sim["fsm"] >= 14:
                return "Electrify,Fail,20018,command forbidden in this state,"
            sim["fsm"] = 14
            return "Electrify,OK,"
        if name in ("BlackOut",):
            sim["fsm"], sim["enabled"], sim["jog"], sim["target"] = 7, 0, None, None
            sim["moving"], sim["inpos"] = 0, 1
            sim["brake"] = [0] * 6
            return "BlackOut,OK,"
        if name in ("StartMaster",):
            sim["fsm"] = max(sim["fsm"], 14)
            return "StartMaster,OK,"
        if name in ("ConnectToBox",):
            if sim["fsm"] < 14:
                return "ConnectToBox,Fail,20018,command forbidden in this state,"
            sim["fsm"] = 24
            return "ConnectToBox,OK,"
        if name in ("GrpEnable",):
            if sim["fsm"] < 24 or sim["safeguard"]:
                return "GrpEnable,Fail,20018,command forbidden in this state,"
            sim["fsm"], sim["enabled"] = 33, 1
            sim["brake"] = [1] * 6
            return "GrpEnable,OK,"
        if name in ("GrpDisable",):
            sim["fsm"], sim["enabled"], sim["jog"], sim["target"] = 24, 0, None, None
            sim["brake"] = [0] * 6
            sim["moving"], sim["inpos"] = 0, 1
            return "GrpDisable,OK,"
        if name in ("GrpReset",):
            sim["err_code"], sim["err_axis"] = 0, 0
            return "GrpReset,OK,"
        if name in ("GrpStop",):
            sim["jog"], sim["target"], sim["moving"], sim["inpos"] = None, None, 0, 1
            return "GrpStop,OK,"
        if name in ("GrpInterrupt", "GrpContinue"):
            return f"{name},OK,"
        if name in ("XToStandby",):
            if sim["safeguard"]:
                return "XToStandby,Fail,20018,external condition not cleared,"
            if sim["fsm"] >= 24 and sim["enabled"] == 0:
                sim["fsm"], sim["enabled"] = 33, 1
            return "XToStandby,OK,"
        if name in ("EnterSafeGuard",):
            flag = int(named(name, args).get("bFlag", 1))
            sim["safeguard"] = flag
            if flag:
                sim["jog"], sim["target"], sim["enabled"], sim["fsm"] = None, None, 0, 24
                sim["brake"] = [0] * 6
            return "EnterSafeGuard,OK,"
        if name == "SetOverride":
            v = float(named(name, args)["dOverride"])
            if not 0.01 <= v <= 1.0:
                return "SetOverride,Fail,20006,Command parameter error,"
            if sim["moving"]:
                return "SetOverride,Fail,20018,command forbidden in this state,"
            sim["override"] = v
            return "SetOverride,OK,"
        if name == "MoveRelJ":
            a = named(name, args)
            axis, direction, dist = int(a["nAxisId"]), int(a["nDirection"]), float(a["dDistance"])
            if sim["enabled"] != 1:
                return "MoveRelJ,Fail,20018,command forbidden in this state,"
            lo, hi = LIMITS[axis]
            tgt = sim["joints"][axis] + dist * (1 if direction else -1)
            if not lo <= tgt <= hi:
                return f"MoveRelJ,Fail,20006, Joint {axis + 1} target {tgt:.3f} out of range [{lo},{hi}],"
            sim["target"] = (axis, tgt)
            return "MoveRelJ,OK,"
        if name == "LongJogJ":
            a = named(name, args)
            axis, direction, state = int(a["nAxisId"]), int(a["nDirection"]), int(a["nState"])
            if sim["enabled"] != 1:
                return "LongJogJ,Fail,20018,command forbidden in this state,"
            if state:
                sim["jog"] = (axis, direction)
                sim["jog_last_hb"] = time.monotonic()
            else:
                sim["jog"] = None
                sim["moving"], sim["inpos"] = 0, 1
            return "LongJogJ,OK,"
        if name == "LongMoveEvent":
            if sim["jog"] is None:
                return "LongMoveEvent,OK,"
            sim["jog_last_hb"] = time.monotonic()
            return "LongMoveEvent,OK,"
        if name == "SetEndDO":
            bit, val = int(named(name, args)["nBit"]), int(named(name, args)["nVal"])
            if not 0 <= bit <= 2:                       # 协议 p51：nBit 只有 0~2
                return "SetEndDO,Fail,20006,Command parameter error,"
            sim["end_do"][bit] = val
            return "SetEndDO,OK,"
        if name == "ReadEO":
            b = [int(x) for x in args[2:5]]
            return "ReadEO,OK," + ",".join(str(sim["end_do"][i]) for i in b) + ","
        if name == "ReadEI":
            b = [int(x) for x in args[2:5]]
            return "ReadEI,OK," + ",".join(str(sim["end_di"][i]) for i in b) + ","
        if name == "ReadEAI":
            return f"ReadEAI,OK,{sim['end_ai'][int(named(name, args)['nBit'])]:.3f},"
        if name in ("ReadBoxDO", "ReadBoxDI", "ReadBoxCI", "ReadBoxCO"):
            key = {"ReadBoxDO": "box_do", "ReadBoxDI": "box_di",
                   "ReadBoxCI": "box_ci", "ReadBoxCO": "box_co"}[name]
            idx = [int(x) for x in args[1:9]]
            return f"{name},OK," + ",".join(str(sim[key][i]) for i in idx) + ","
        if name == "ReadBoxInfo":
            return ("ReadBoxInfo,OK,1,1," + f"{sim['volt48']:.3f},0.000,0,0,0.000,0.000,")
        if name == "ShortJogJ":
            return "ShortJogJ,OK,"
        if name == "ReadActPos":
            j = list(sim["joints"])
            b = pcs_base(j)
            return ("ReadActPos,OK," + ",".join(f"{v:.3f}" for v in j) + "," +
                    ",".join(f"{v:.3f}" for v in b) + "," + ",".join(["0.000"] * 12) + ",")
        if name == "ReadCurFSM":
            return f"ReadCurFSM,OK,{sim['fsm']},"
        if name == "ReadOverride":
            return f"ReadOverride,OK,{sim['override']:.3f},"
        if name == "ReadBrakeStatus":
            return "ReadBrakeStatus,OK," + ",".join(str(x) for x in sim["brake"]) + ","
        if name == "IsSimulation":
            return "IsSimulation,OK,1,"
        if name == "ReadControllerState":
            return "ReadControllerState,OK,1,"
        if name == "PackageVersion":
            return f"PackageVersion,OK,{sim['version']},"
        if name == "ReadRobotModel":
            return f"ReadRobotModel,OK,{sim['model']},"
        if name == "GetErrorCodeStr":
            return "GetErrorCodeStr,OK,mock explanation,"
    return f"{name},Fail,20007,command format error,"


KNOWN = {"Electrify", "BlackOut", "StartMaster", "ConnectToBox", "GrpEnable", "GrpDisable",
         "GrpReset", "GrpStop", "GrpInterrupt", "GrpContinue", "XToStandby", "EnterSafeGuard",
         "SetOverride", "MoveRelJ", "LongJogJ", "LongMoveEvent", "ShortJogJ", "ReadActPos",
         "ReadCurFSM", "ReadOverride", "ReadBrakeStatus", "IsSimulation", "ReadControllerState",
         "PackageVersion", "ReadRobotModel", "GetErrorCodeStr", "SetEndDO", "ReadEO", "ReadEI",
         "ReadEAI", "ReadBoxDO", "ReadBoxDI", "ReadBoxCI", "ReadBoxCO", "ReadBoxInfo"}


class CmdHandler(socketserver.BaseRequestHandler):
    """命令口。真机实测：未使能时不推 Data Sheet，使能后才推，这里照抄。"""

    def handle(self):
        port = self.server.server_address[1]
        self.request.settimeout(30)
        stop = threading.Event()
        pump = None
        if port in (10001, 10003) and sim["enabled"]:
            def push():
                while not stop.is_set():
                    payload = json.dumps(datasheet(), ensure_ascii=False).encode("utf-8")
                    try:
                        self.request.sendall(frame(payload))
                    except OSError:
                        return
                    time.sleep(0.05)

            pump = threading.Thread(target=push, daemon=True)
            pump.start()

        acc = b""
        try:
            while True:
                try:
                    data = self.request.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    break
                acc += data
                while b",;" in acc:
                    msg, acc = acc.split(b",;", 1)
                    # 协议 p12：一次发多条只处理第一条，其余丢弃
                    parts = msg.split(b";")[0].decode("latin1").split(",")
                    reply = handle_cmd(parts[0], parts, port) + ";"
                    self.request.sendall(reply.encode("ascii"))
        except OSError:
            pass
        finally:
            stop.set()


class StreamHandler(socketserver.BaseRequestHandler):
    """10004/5/6 推 JSON Data Sheet（面板只用这三个）。

    10014/15/16 真机推的是定长 944B 结构体，字段布局尚未解析，
    这里只保证帧长和帧头一致，内容不可信，面板不读这几个口。
    """

    period = 0.05
    binary = False

    def handle(self):
        stop = threading.Event()

        def send():
            while not stop.is_set():
                sheet = datasheet()
                if self.binary:
                    joints = [float(v) for v in sheet["PosAndVel"]["Actual_Position"][:6]]
                    payload = struct.pack("<6d", *joints) + b"\x00" * (944 - 48)
                else:
                    payload = json.dumps(sheet, ensure_ascii=False).encode("utf-8")
                try:
                    self.request.sendall(frame(payload))
                except OSError:
                    return
                time.sleep(self.period)

        t = threading.Thread(target=send, daemon=True)
        t.start()
        try:
            while True:
                if not self.request.recv(4096):
                    break
        except OSError:
            pass
        finally:
            stop.set()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def make_stream(period, binary):
    return type(f"S{period}", (StreamHandler,), {"period": period, "binary": binary})


def main():
    missing = KNOWN - set(P.CMDS)
    if missing:
        raise SystemExit(f"上位机指令表缺少：{sorted(missing)}")
    threading.Thread(target=physics, daemon=True).start()
    ports = {10004: make_stream(0.05, False), 10005: make_stream(0.10, False),
             10006: make_stream(0.20, False), 10014: make_stream(0.05, True),
             10015: make_stream(0.10, True), 10016: make_stream(0.20, True),
             10003: CmdHandler, 10001: CmdHandler}
    for port, h in ports.items():
        s = Server(("127.0.0.1", port), h)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        print(f"mock listening 127.0.0.1:{port}")
    print("【仿真】假控制器已就绪，IsSimulation 返回 1，面板顶栏会标红为仿真数据")
    print("【仿真】先点【连接】(IP 填 127.0.0.1)，再走 就绪→使能 才能试运动")
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
