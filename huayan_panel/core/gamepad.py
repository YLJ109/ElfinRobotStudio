"""Xbox 手柄：Windows 原生 XInput 读取，零第三方依赖。

只做"读"和"映射"，不碰机器人；发指令的门槛仍然由 Link 的许可等级和联锁负责。
PyQt6 不带 QtGamepad 模块，所以直接用 ctypes 调 xinput DLL。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

DLL_NAMES = ("xinput9_1_0.dll", "xinput1_4.dll", "xinput1_3.dll")

BTN = {
    "DPAD_UP": 0x0001, "DPAD_DOWN": 0x0002, "DPAD_LEFT": 0x0004, "DPAD_RIGHT": 0x0008,
    "START": 0x0010, "BACK": 0x0020, "LSTICK": 0x0040, "RSTICK": 0x0080,
    "LB": 0x0100, "RB": 0x0200, "A": 0x1000, "B": 0x2000, "X": 0x4000, "Y": 0x8000,
}

# XINPUT_GAMEPAD_LEFT_THUMB_DEADZONE / RIGHT_THUMB_DEADZONE 官方值
DEADZONE = 7849
THUMB_FULL = 32767.0
ERROR_DEVICE_NOT_CONNECTED = 1167


class _Triggers(ctypes.Structure):
    _fields_ = [("bLeft", ctypes.c_ubyte), ("bRight", ctypes.c_ubyte)]


class _Gamepad(ctypes.Structure):
    # 字段顺序必须和 XINPUT_GAMEPAD 完全一致：扳机是两个 BYTE，漏了会让摇杆读数全部错位
    _fields_ = [("wButtons", ctypes.c_ushort),
                ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_short), ("sThumbLY", ctypes.c_short),
                ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]


class _State(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_uint), ("gamepad", _Gamepad)]


def stick_to_dir(value, deadzone=DEADZONE):
    """摇杆分量 -> 点动方向：1=正方向，0=负方向，None=在中位死区内。"""
    if value is None:
        return None
    if value > deadzone:
        return 1
    if value < -deadzone:
        return 0
    return None


def stick_pct(value):
    """摇杆分量 -> -100~100 的百分比，用于界面上显示真实推量。"""
    return 0.0 if value is None else round(value / THUMB_FULL * 100.0, 1)


class XboxPad:
    """轮询式手柄读取。read() 返回 None 表示没连上，不返回假数据。"""

    def __init__(self, user=0):
        self.user = user
        self.dll = None
        self._last_packet = 0
        for name in DLL_NAMES:
            try:
                self.dll = ctypes.WinDLL(name)
                break
            except OSError:
                continue
        if self.dll is None:
            self.err = "系统里没有 xinput DLL（Xbox 手柄需要 XInput）"
            return
        self._State = _State
        try:
            self.dll.XInputGetState.argtypes = [wintypes.DWORD, ctypes.POINTER(_State)]
            self.dll.XInputGetState.restype = ctypes.c_uint
        except AttributeError:
            self.dll = None
            self.err = "该 xinput DLL 没有 XInputGetState 导出"
            return
        self.err = ""

    def read(self):
        """返回 {'lx','ly','rx','ry','lt','rt','buttons':set(),'packet':int} 或 None。"""
        if self.dll is None:
            return None
        st = _State()
        rc = self.dll.XInputGetState(self.user, ctypes.byref(st))
        if rc != 0:
            self.last_rc = rc
            return None
        g = st.gamepad
        return {
            "lx": g.sThumbLX, "ly": g.sThumbLY, "rx": g.sThumbRX, "ry": g.sThumbRY,
            "lt": g.bLeft if hasattr(g, "bLeft") else 0,
            "rt": 0,
            "triggers": (st.gamepad.__class__ is not None),
            "buttons": {n for n, m in BTN.items() if g.wButtons & m},
            "packet": st.dwPacketNumber,
            "wButtons": g.wButtons,
        }
