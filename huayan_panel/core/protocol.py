"""协议层：LTBR 帧解析、ASCII 消息编解码、指令规格、限位与错误码。

依据 HuayanRobotV6控制通信协议接口_V1.0.19.1.pdf（页码见注释），不含推测语法。
"""

from __future__ import annotations

import json
import re
import struct

MAGIC = b"LTBR"
HDR = struct.Struct("<4sII")

# 文档 3.2 服务端口表 p18 + 实测
PORT_STREAM_JSON = (10004, 10005, 10006)   # JSON Data Sheet 50/100/200ms
PORT_STREAM_STRUCT = (10014, 10015, 10016)  # Struct Data Sheet，实测定长 944B
PORT_CMD = 10003        # IF 命令服务端口，实测 8/8 只读指令通过
PORT_FAST_CMD = 10001   # IF 快速命令端口，实测拒绝部分指令（20099）

REPLY_RE = re.compile(rb"([A-Za-z]\w*),?(OK|Fail),?(.*?);", re.S)


def encode(name: str, *params) -> bytes:
    """命令名,p1,...,;  （协议 p14 2.1 消息格式）"""
    if params:
        return f"{name},{','.join(str(p) for p in params)},;".encode("ascii")
    return f"{name},;".encode("ascii")


def split_frames(buf: bytes):
    """从缓冲区切出 LTBR 帧，返回 (payload 列表, 剩余字节, 剩余中的文本段)。

    注意半截帧的坑：指令口会把 ASCII 回执**粘在周期帧后面**一起送上来。
    如果只按"帧还没收完"死等，回执就永远留在缓冲区里，表现为指令超时且之后
    每条都超时（整条会话楔死）。所以当缓冲区里已经能找到一条完整回执时，
    就把那一段当作文本先交出去，从不完整的帧体里重同步。
    """
    payloads, text = [], b""
    i = 0
    while i < len(buf):
        j = buf.find(MAGIC, i)
        if j < 0:
            text += buf[i:]
            return payloads, b"", text
        text += buf[i:j]
        if len(buf) < j + HDR.size:
            return payloads, buf[j:], text
        _, _, plen = HDR.unpack(buf[j:j + HDR.size])
        if plen > 4 * 1024 * 1024:
            return payloads, b"", text + buf[j:]
        if len(buf) < j + HDR.size + plen:
            return payloads, buf[j:], text
        payloads.append(buf[j + HDR.size:j + HDR.size + plen])
        i = j + HDR.size + plen
    return payloads, b"", text


def parse_reply(chunk: bytes):
    """解析一条 ASCII 回执，返回 (命令名, 是否成功, 字段列表, 剩余字节)。"""
    m = REPLY_RE.search(chunk)
    if not m:
        return None
    name = m.group(1).decode("latin1")
    ok = m.group(2) == b"OK"
    body = m.group(3).decode("latin1").rstrip(",")
    fields = body.split(",") if body else []
    return name, ok, fields, chunk[m.end():]


def decode_state(payload: bytes) -> dict:
    return json.loads(payload.decode("utf-8"))


# ---------------------------------------------------------------- 指令规格
# range 为 None 表示文档未给有效范围（不代表无限）
CMDS = {
    "IsSimulation": ("", 25),
    "ReadControllerState": ("", 26),
    "PackageVersion": ("", 29),
    "ReadRobotModel": ("", 28),
    "StartMaster": ("", 23),
    "ConnectToBox": ("", 20),
    "Electrify": ("", 21),          # 上电：只通 48V，抱闸仍吸合（手册 p15）
    "BlackOut": ("", 22),          # 下电
    "GrpEnable": ("nRbtID", 30),
    "GrpDisable": ("nRbtID", 31),
    "GrpReset": ("nRbtID", 32),
    "GrpStop": ("nRbtID", 33),
    "GrpInterrupt": ("nRbtID", 34),
    "GrpContinue": ("nRbtID", 35),
    "XToStandby": ("nRbtID", 38),
    "EnterSafeGuard": ("nRbtID,bFlag", 38),
    # ---- 手拖示教（Basic Free Drive：松闸、可用人力推动）协议 4.2.7 / 4.2.8 ----
    "GrpOpenFreeDriver": ("nRbtID", 26),
    "GrpCloseFreeDriver": ("nRbtID", 25),
    # ---- 力控/牵引（Advanced Free Drive 一族，需要机器人处于力控状态）----
    # 未取证的本机行为不猜：这几条只做"发出并如实回显回执"，不拿来当已验证功能用。
    "SetFTFreeDriveState": ("nRbtID,nState", 164),
    "ReadFTFreeDriveSpeedMode": ("nRbtID", 165),
    "SetFTFreeDriveSpeedMode": ("nRbtID,nState", 166),
    "ReadFTFreeFactor": ("nRbtID", 170),
    "SetFTFreeFactor": ("nRbtID,dLinear,dAngular", 167),
    "HRSetControlFreedom": ("nRbtID,nX,nY,nZ,nRx,nRy,nRz", 151),
    "SetOverride": ("nRbtID,dOverride", 72),
    "ReadOverride": ("nRbtID", 87),
    "ReadCurFSM": ("nRbtID", 97),
    "ReadActPos": ("nRbtID", 102),
    "ReadBrakeStatus": ("boxID", 40),   # 实测只能走 10003，走 10001 报 20099
    "MoveRelJ": ("nRbtID,nAxisId,nDirection,dDistance", 184),
    "MoveJTo": ("nRbtID,dJ1,dJ2,dJ3,dJ4,dJ5,dJ6", 198),
    "MoveLTo": ("nRbtID,dX,dY,dZ,dRx,dRy,dRz,sTcpName,sUcsName", 198),
    "ShortJogJ": ("nRbtID,nAxisId,nDirection", 211),
    "LongJogJ": ("nRbtID,nAxisId,nDirection,nState", 213),
    "LongMoveEvent": ("nRbtID", 215),
    "GetErrorCodeStr": ("nErrorCode", 27),
    # ---- 末端 / 电箱 IO（吸气·放气·按钮走这里，协议 4.4 章）----
    "ReadBoxInfo": ("nRbtID", 39),
    "ReadBoxCI": ("nBit0,nBit1,nBit2,nBit3,nBit4,nBit5,nBit6,nBit7", 41),
    "ReadBoxDI": ("nBit0,nBit1,nBit2,nBit3,nBit4,nBit5,nBit6,nBit7", 42),
    "ReadBoxCO": ("nBit0,nBit1,nBit2,nBit3,nBit4,nBit5,nBit6,nBit7", 43),
    "ReadBoxDO": ("nBit0,nBit1,nBit2,nBit3,nBit4,nBit5,nBit6,nBit7", 44),
    "ReadBoxAI": ("nBit0,nBit1", 45),
    "ReadBoxAO": ("nBit", 46),
    "SetBoxCO": ("nBit,nVal", 47),
    "SetBoxDO": ("nBit,nVal", 48),
    "SetBoxAOMode": ("nBit,nMode", 49),      # nMode 1/2（协议 p49）
    "SetBoxAO": ("nBit,dVal,nMode", 50),
    "SetEndDO": ("nRbtID,nBit,nVal", 51),    # nBit 0~2，即 EndDO1~3（协议 p51）
    "ReadEI": ("nRbtID,nBit0,nBit1,nBit2", 52),
    "ReadEO": ("nRbtID,nBit0,nBit1,nBit2", 53),
    "ReadEAI": ("nRbtID,nBit", 54),           # nBit 0/1
    "SetMaxAcsRange": ("nRbtID,dMaxJ1,dMaxJ2,dMaxJ3,dMaxJ4,dMaxJ5,dMaxJ6,"
                       "dMinJ1,dMinJ2,dMinJ3,dMinJ4,dMinJ5,dMinJ6", 69),
    "SetMaxPcsRange": ("nRbtID,dMaxX,dMaxY,dMaxZ,dMaxRx,dMaxRy,dMaxRz,"
                       "dMinX,dMinY,dMinZ,dMinRx,dMinRy,dMinRz", 71),
    "cdsSetIO": ("nEndDOMask,nEndDOVal,nBoxDOMask,nBoxDOVal,nBoxCOMask,nBoxCOVal,"
                 "nBoxAOCH0_Mask,nBoxAOCH0_Mode,nBoxAOCH1_Mask,nBoxAOCH1_Mode,"
                 "dbBoxAOCH0_Val,dbBoxAOCH1_Val", 55),   # 一次性原子写多路 IO
}

LIMITS = {
    "nRbtID": (0, 5),
    "nAxisId": (0, 5),     # 0~5 对应 J1~J6（协议 p184，示例注释"关节2"=1）
    "nDirection": (0, 1),  # 0 负方向 / 1 正方向
    "nState": (0, 1),      # 0 关闭点动 / 1 开启点动
    "bFlag": (0, 1),       # 0 退出软急停 / 1 进入软急停
    "dOverride": (0.01, 1.0),
    "dDistance": (0.001, 10.0),  # 文档 p184 未给范围，上限为本机软件自设硬卡
}

# 按命令分别限定参数取值（LIMITS 是按参数名通用的，IO 类每条命令的位号范围不同）
# 来源都是协议 4.4 章正文或示例，未写明的不猜。
CMD_RANGES = {
    ("SetEndDO", "nRbtID"): (0, 5),        # p51
    ("SetEndDO", "nBit"): (0, 2),          # p51 明写 nBit 0~2，即 EndDO1~3
    ("SetEndDO", "nVal"): (0, 1),          # p51
    ("ReadEO", "nRbtID"): (0, 5),          # p53
    ("ReadEI", "nRbtID"): (0, 5),          # p52
    **{(f"ReadE{io}", f"nBit{i}"): (0, 2)        # p52/53 示例 ReadEO,0,0,1,2,;
       for io in "OI" for i in range(3)},
    **{("Read" + bx, f"nBit{i}"): (0, 7)         # p41~44 位号示例 0~7
       for bx in ("BoxCI", "BoxDI", "BoxCO", "BoxDO") for i in range(8)},
    ("ReadEAI", "nRbtID"): (0, 5),         # p54
    ("ReadEAI", "nBit"): (0, 1),           # p54 明写 0/1
    ("SetBoxAOMode", "nBit"): (0, 1),      # p49 明写 0/1
    ("SetBoxAOMode", "nMode"): (1, 2),     # p49 明写 1/2
    ("SetBoxCO", "nBit"): (0, 7),          # p47 示例 SetBoxCO,5,1,;，通道数 8（实测 Data Sheet）
    ("SetBoxDO", "nBit"): (0, 7),          # p48，通道数 8（实测 Data Sheet）
    ("SetBoxCO", "nVal"): (0, 1),
    ("SetBoxDO", "nVal"): (0, 1),
    # 手拖/牵引一族：范围全部照协议正文表格，文档没写的不加。
    ("GrpOpenFreeDriver", "nRbtID"): (0, 5),      # p26
    ("GrpCloseFreeDriver", "nRbtID"): (0, 5),     # p25
    ("SetFTFreeDriveState", "nState"): (0, 1),    # p164 关/开
    ("SetFTFreeDriveSpeedMode", "nState"): (0, 3),   # p166 四档，比 LIMITS 的通用 nState 宽
    ("SetFTFreeFactor", "dLinear"): (0, 100),     # p167
    ("SetFTFreeFactor", "dAngular"): (0, 100),    # p167
    **{("HRSetControlFreedom", k): (0, 1)          # p151 六个方向各自 0/1
       for k in ("nX", "nY", "nZ", "nRx", "nRy", "nRz")},
}

# 关节软限位：来自《Elfin软件使用手册V6.5》p54-55 截图。
# 真机 ReadRobotModel 返回 E05-1195_Pro，与截图机型是否一致未核对 → verified=False。
JOINT_LIMITS = [
    (-360.0, 360.0),
    (-135.0, 135.0),
    (-153.0, 153.0),
    (-360.0, 360.0),
    (-180.0, 180.0),
    (-360.0, 360.0),
]
LIMITS_VERIFIED = False
LIMIT_MARGIN_DEG = 2.0

# FSM 码：完整状态机表在 HuayanRobot_ErrorCode.docx，仓库内缺失。
# 含义来自 SDK 注释推断 + 本机逐条实测（2026-10-12）：
#   7 未上电 →(Electrify)→ 14 已上电 →(StartMaster)→ 15 过渡 → 24 初始化完成
#   →(GrpEnable)→ 33 已就绪；下电后实测会自己跳 14→6→7→14。
FSM_HINT = {6: "下电过渡(实测)", 7: "未上电(实测)", 14: "已上电(实测)",
            15: "主控启动中(实测，StartMaster 后的瞬时档)",
            24: "控制器初始化成功(实测)", 33: "已就绪(实测)"}

ERROR_HINT = {
    20007: "指令格式错误 command format error",
    20006: "指令参数错误 Command parameter error",
    20018: "当前状态不允许该指令 command forbidden in this state",
    20029: "TCP 不存在",
    20030: "UCS 不存在",
    20099: "该指令不允许走当前端口（实测 ReadBrakeStatus 走 10001 触发）",
    # 协议 p164 示例回执：力控类指令（SetFTFreeDriveState 一族）要求先进入力控状态
    40020: "机器人不在力控状态 Robot not in force control state",
}

STEP_CHOICES = (0.1, 0.5, 1.0, 5.0, 10.0)
