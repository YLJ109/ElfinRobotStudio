"""字段对齐测试：官方 Data Sheet 里有的数，本软件必须要么接、要么显式登记为"不接"。

动机（2026-10-12）：真机帧里其实有 PCS_TCP、关节速度/加速度、六维力、电箱 8 路 IO、
控制器脚本执行位、EtherCAT 丢帧、各轴温度电压等一大堆字段，最早只解析了 14 个 ——
属于"守着金矿要饭"。这张测试把对照表钉死，之后谁加了字段没登记、或解析读错了节，
当场失败。

fixture 是 tools/datasheet_full.json（真机 10004 抓的一帧，非编造）。

跑法： python tools/check_datasheet_fields.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.backend.core.robot_real import FIELD_MAP, RealLink   # noqa: E402

FIX = Path(__file__).with_name("datasheet_full.json")

# 帧里有、但**故意**不单独进快照的：要么是同一组的第二通道（已折进同一个键），
# 要么是本软件用不上的内部字段。必须写在这里，否则测试会以为漏接了。
FOLDED = {
    "ElectricBoxAnalogIO.BoxAnalogIn_2": "折进 box_ai[1]",
    "ElectricBoxAnalogIO.BoxAnalogOut_2": "折进 box_ao[1]",
    "ElectricBoxAnalogIO.BoxAnalogOutMode_1": "折进 box_ao_mode[0]",
    "ElectricBoxAnalogIO.BoxAnalogOutMode_2": "折进 box_ao_mode[1]",
    "MsgTitle.Stamp": "控制器毫秒时间戳，已用 UpdateTime 显示，不重复存",
    "RobotAuthorization.CheckSum": "授权校验和，本软件不做授权操作",
    "RobotAuthorization.LangAuthData": "语言授权位图，界面上没有维文开关",
    "RobotAuthorization.LangnDeviceCode": "静态设备码，已展示 DynDeviceCode",
}

ok_n = fail_n = 0


def check(name, cond, detail=""):
    global ok_n, fail_n
    if cond:
        ok_n += 1
    else:
        fail_n += 1
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


def walk(obj, prefix=""):
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out |= walk(v, (prefix + "." if prefix else "") + k)
    else:
        out.add(prefix)
    return out


frame = json.loads(FIX.read_text(encoding="utf-8-sig"))
link = RealLink("127.0.0.1")
link._on_frame(frame)

paths = walk(frame)
mapped = {f"{sec}.{fld}" for sec, fld, _k, _n in FIELD_MAP}
keys = [k for _s, _f, k, _n in FIELD_MAP]

check("对照表覆盖帧内所有字段（接上或显式登记不接）",
      paths <= mapped | set(FOLDED),
      f"未登记 {sorted(paths - mapped - set(FOLDED))}")
check("没有登记了却根本不在帧里的字段（防凭记忆编字段名）",
      mapped <= paths, f"表里多出来 {sorted(mapped - paths)}")
check("快照键没有重复", len(keys) == len(set(keys)), f"{len(keys)} 条")
missing = [k for _s, _f, k, _n in FIELD_MAP if link.snap.get(k) is None]
check("喂真实帧后每个登记字段都取到值", not missing, f"取不到 {missing}")
check("快照里没有表外的野键",
      set(k for k, v in link.snap.items() if v is not None) <= set(keys) | {"stamp", "connection_epoch", "frame_seq"},
      str(sorted(set(k for k, v in link.snap.items() if v is not None) - set(keys) - {"stamp", "connection_epoch", "frame_seq"})))

# 关键数值抽查：这几条错了整个监控就是假数据
check("关节角取到 6 个", len(link.snap["joints"] or []) == 6, str(link.snap["joints"]))
check("PCS_Base 与 FK 同口径 6 元", len(link.snap["pcs_base"] or []) == 6, str(link.snap["pcs_base"]))
check("override 是 0~1 的小数", 0 < (link.snap["override"] or 0) <= 1.0, str(link.snap["override"]))
check("FSM 读到 33（抓帧时机器已就绪）", link.snap["fsm"] == 33, str(link.snap["fsm"]))
check("末端可写 DO 只有 3 路（协议 p51）", len(link.snap["end_do"] or []) == 4,
      "帧里给 4 个，其中第 4 个不可写")
check("电箱 DI/DO 各 8 路", len(link.snap["box_di"] or []) == 8 and len(link.snap["box_do"] or []) == 8,
      f"{len(link.snap['box_di'] or [])}/{len(link.snap['box_do'] or [])}")
check("六维力有 6 个分量", len(link.snap["ft_data"] or []) == 6, str(link.snap["ft_data"]))
check("温度 6 轴都有", len(link.snap["temps"] or []) == 6, str(link.snap["temps"]))
check("手拖回读位存在", link.snap["free_drive"] in (0, 1), str(link.snap["free_drive"]))
check("慢分组（1Hz）在首帧就刷过", link.snap["device_sn"] == frame["RobotAuthorization"]["DeviceSN"],
      str(link.snap["device_sn"]))

print("=" * 56)
print(f"{ok_n} 通过 / {fail_n} 失败")
sys.exit(1 if fail_n else 0)
