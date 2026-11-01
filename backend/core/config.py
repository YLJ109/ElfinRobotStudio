"""全局配置与持久化设置。

密钥只从环境变量 / .env 读，绝不写进源码。
"""

from __future__ import annotations

import json
import math
import os
import threading
from copy import deepcopy
from .storage import write_json
from ..paths import PROJECT_ROOT as ROOT, DATA_DIR

ENV_FILE = ROOT / ".env"


# 当前用户口径：接近碰撞90%黄、95%红，接触/硬边界100%红闪。
SAFETY_YELLOW_PCT = 90.0
SAFETY_RED_PCT = 95.0
SAFETY_ALARM_PCT = 100.0


def load_dotenv(path=ENV_FILE):
    """极简 .env 解析：KEY=VALUE，# 注释。已存在的环境变量优先。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


DEFAULTS = {
    "mode": "sim",                      # sim | real
    "robot_ip": "192.168.0.10",
    # 仿真速度和真机速度是两回事：仿真只是插值快慢，真机那路会下发 SetOverride。
    # 顶栏滑条只驱动「当前模式」对应的那一个，互不覆盖。
    "sim_speed_pct": 100.0,
    "real_speed_pct": 10.0,             # 用户指定冷启显示目标；不发送命令，专用任务<=5%门槛另行保留
    "vel_source": "urdf",               # urdf(90°/s) | manual(J1-4 180, J5-6 200)
    "limit_source": "controller",       # controller（手册 p54=协议 p69）| urdf（建模口径）
    # 围栏 = 允许工作范围（keep-in）。默认给足余量：E05 臂展 800mm、零位法兰高 1195mm。
    "fence": {"x": [-900, 900], "y": [-900, 900], "z": [0, 1400], "enabled": True},
    "self_collision": True,
    "ground_enabled": True,
    "ghost_opacity": 0.34,              # 残影那台假手的透明度
    "interaction_range_default": None,   # shared UI preference; None keeps legacy fallback
    "ghost_mode": "ghost",              # ghost=拖预设出残影 | live=只实时跟随不留残影
    # 残影收掉的判据：真机六轴都进到目标 ±tol 以内、且站住 hold 秒、且控制器说没在动。
    "ghost_align_tol_deg": 0.3,
    "ghost_align_hold_s": 0.5,
    "safety_yellow_pct": SAFETY_YELLOW_PCT,
    "safety_red_pct": SAFETY_RED_PCT,
    "safety_alarm_pct": SAFETY_ALARM_PCT,
    "tts": True,
    "tts_rate": 1.0,
    "ai_model": "glm-4-flash",
    "ai_base_url": "https://open.bigmodel.cn/api/paas/v4",
    "real_interpolation": True,
    "real_move_cmd": "relj",           # relj=已实测验证的逐轴逼近 | movejto=协议 p198，本机未取证
    "custom_limits": None,             # 示教器上读到的软限位，[[lo,hi] x6]；填了才生效
    "log_keep": 2000,
    # 首次启动不连接硬件；仅操作者明确保存后才允许自动连接。
    "auto_connect": False,
    # 手拖示教（Basic Free Drive）的看门狗：单次按住上限 + 末端外力自动退出阈值。
    # 力阈值 0 = 不设力闸（这台机器是否给了可用的 FTData 要实测，不能凭空当依据）。
    "free_hold_max_s": 8.0,
    "free_force_max_n": 0.0,
    "play_len_s": 300,                  # 回放缓冲时长
    "trail_len": 40,                    # 末端轨迹拖尾长度（帧），10~100
}

# 手册《中文_UL_Elfin机械说明书 ver3.0》E05 表：J1-J4 180°/s、J5-J6 200°/s
MANUAL_VEL = [180.0, 180.0, 180.0, 180.0, 200.0, 200.0]
# 控制器软限位：手册 p54 与协议 p69 的 SetMaxAcsRange 官方示例完全一致
# （360/135/153/360/180/360），所以这套是"控制器真会执行"的口径，默认用它做安全余量分级。
MANUAL_LIMITS = [(-360, 360), (-135, 135), (-153, 153), (-360, 360), (-180, 180), (-360, 360)]
LIMIT_SOURCES = {
    "controller": ("控制器软限位（手册 p54 = 协议 p69 示例）", MANUAL_LIMITS),
    "urdf": ("官方 URDF 建模限位", None),      # None = 用 robot.json 里的值
    "manual": ("示教器实测限位（需你在设置里手填）", None),
}

_lock = threading.RLock()
_settings = None


def speed_key(mode):
    """按驱动模式取对应的速度键；未知模式按仿真处理。"""
    return "real_speed_pct" if mode == "real" else "sim_speed_pct"


def settings_path():
    return DATA_DIR / "settings.json"


def valid_interaction_range_default(value):
    if value is None:
        return True
    if not isinstance(value, dict) or set(value) != {'center', 'size', 'direction', 'depthRange'}:
        return False
    def vector(values, length, low, high):
        return (isinstance(values, list) and len(values) == length
                and all(type(v) in (int, float) and math.isfinite(v) and low <= v <= high for v in values))
    return (vector(value['center'], 3, -10000, 10000)
            and vector(value['size'], 3, 1, 5000)
            and type(value['direction']) is int and value['direction'] in (0, 90, 180, 270)
            and vector(value['depthRange'], 2, .005, 2)
            and value['depthRange'][1] - value['depthRange'][0] >= .01)


def valid_log_keep(value):
    return type(value) is int and 1 <= value <= 100000


def valid_play_length(value):
    return type(value) in (int,float) and math.isfinite(value) and 1 <= value <= 3600


def _historical_fence(value):
    """只在内存中修复历史坏字段，不改写磁盘，也不修改合法自定义范围。"""
    result = deepcopy(DEFAULTS['fence'])
    if not isinstance(value, dict):
        return result
    result.update(value)
    for axis in ('x', 'y', 'z'):
        bounds = result.get(axis)
        try:
            valid = (isinstance(bounds, list) and len(bounds) == 2
                     and all(type(v) in (int, float) and math.isfinite(v) for v in bounds)
                     and bounds[0] < bounds[1]
                     and (axis != 'z' or bounds[1] > 0))
        except OverflowError:
            valid = False
        if not valid:
            result[axis] = deepcopy(DEFAULTS['fence'][axis])
    result['z'] = [0, result['z'][1]]
    return result


def _load_settings(path):
    settings = deepcopy(DEFAULTS)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            loaded = {}
        if not isinstance(loaded, dict):
            loaded = {}
        if not valid_interaction_range_default(loaded.get('interaction_range_default')):
            loaded['interaction_range_default'] = None
        if not valid_log_keep(loaded.get('log_keep', DEFAULTS['log_keep'])):
            loaded['log_keep'] = DEFAULTS['log_keep']
        if not valid_play_length(loaded.get('play_len_s', DEFAULTS['play_len_s'])):
            loaded['play_len_s'] = DEFAULTS['play_len_s']
        if 'fence' in loaded:
            loaded['fence'] = _historical_fence(loaded['fence'])
        # 旧版只有一个全局 speed_pct，升级时迁到仿真那一路，
        # 免得用户辛苦调好的数被默认值冲掉。
        legacy = loaded.pop("speed_pct", None)
        if legacy is not None and "sim_speed_pct" not in loaded:
            loaded["sim_speed_pct"] = legacy
        loaded.pop("ghost_len", None)      # 旧"残影帧数"：改成假手模型后没有意义了
        fence = loaded.get("fence")
        if isinstance(fence, dict) and fence.get("x") == [-1200,1200] and fence.get("y") == [-1200,1200]:
            loaded["fence"] = {**fence,"x":[-900,900],"y":[-900,900],
                "z":[max(0,fence.get("z",[0,1400])[0]),fence.get("z",[0,1400])[1]]}
        # Upgrade the prior built-in thresholds while preserving any
        # values the operator explicitly customized in Settings.
        if (loaded.get("safety_yellow_pct"),loaded.get("safety_red_pct")) in ((80.,90.),(95.,97.)):
            loaded["safety_yellow_pct"], loaded["safety_red_pct"] = SAFETY_YELLOW_PCT, SAFETY_RED_PCT
        if isinstance(loaded.get('fence'),dict):
            loaded['fence']['z']=[0,loaded['fence'].get('z',[0,1400])[1]]
        settings.update(loaded)
    # Simulation keeps its saved speed. Each backend start begins with
    # a 10% real target; the live override is still read from the robot,
    # and no command is sent merely by loading these preferences.
    settings["real_speed_pct"] = DEFAULTS["real_speed_pct"]
    # 正确：打开桌面窗口不自动建立硬件连接，操作员仍可在界面主动连接。
    if os.environ.get('ELFIN_DESKTOP') == '1':
        settings['auto_connect'] = False
    settings.pop("speed_pct", None)
    # 2026-10-12 拆掉"替操作者做主"的真机保险：软件不再限制能动哪根轴、
    # 单步多大、速度多快。旧配置里残留的这几项直接丢掉。
    for k in ("motion_guard_on", "motion_axes", "motion_max_step_deg",
              "motion_speed_max_pct"):
        settings.pop(k, None)
    return settings


def get_settings():
    global _settings
    with _lock:
        if _settings is None:
            _settings = _load_settings(settings_path())
        return _settings


def _persist_settings(current, path, patch):
    if 'interaction_range_default' in patch and not valid_interaction_range_default(patch['interaction_range_default']):
        raise ValueError('默认交互范围无效')
    if 'play_len_s' in patch and not valid_play_length(patch['play_len_s']):
        raise ValueError('回放时长需要1~3600秒')
    if 'log_keep' in patch and not valid_log_keep(patch['log_keep']):
        raise ValueError('日志条数需要1~100000的整数')
    candidate = deepcopy(current)
    candidate.update(deepcopy(patch))
    write_json(path, candidate)
    current.clear()
    current.update(candidate)
    return deepcopy(current)


def save_settings(patch):
    with _lock:
        return _persist_settings(get_settings(),settings_path(),patch)


class SettingsStore:
    """One settings cache and storage path per application data domain."""
    def __init__(self, data_dir):
        from pathlib import Path
        self.path = Path(data_dir) / 'settings.json'
        self._lock = threading.RLock()
        self._settings = None

    def get_settings(self):
        with self._lock:
            if self._settings is None:
                self._settings = _load_settings(self.path)
            return self._settings

    def save_settings(self, patch):
        with self._lock:
            return _persist_settings(self.get_settings(),self.path,patch)


def no_real():
    """进程级隔离闸：置了 HUYAN_NO_REAL 就**物理上不允许**连控制器。

    自检脚本会真的下发指令（点动、DO 写入、就绪泵），一旦它连上真机就是拿
    一台通电的机械臂跑单测 —— 2026-10-12 就发生过：check_backend 把 J6 拖了 2°
    并把 DO1 留在通电状态。所以这道闸必须在 World 之外、在环境变量层面。
    """
    return (os.environ.get("HUYAN_NO_REAL") or "").strip().lower() in ("1", "true", "yes", "on")


def zhipu_key():
    load_dotenv()
    return os.environ.get("ZHIPU_API_KEY", "")


# AI 助手面板下发的「厂商 / 模型 / Base URL / Key」运行时配置。
# 只活在当前进程内存里：不写 settings.json、不进日志，重启后回到 .env 或默认值。
# None = 面板还没下发过（回落到环境变量）；{} = 面板显式清空（就是不配置）。
_ai_runtime = None


def ai_runtime():
    return dict(_ai_runtime) if _ai_runtime else {}


def set_ai_runtime(provider, model, base_url, api_key):
    global _ai_runtime
    _ai_runtime = {"provider": provider, "model": model,
                   "base_url": base_url, "api_key": api_key}


def clear_ai_runtime():
    global _ai_runtime
    _ai_runtime = {}


def ai_api_key():
    """面板配置优先；面板没下发过才回落 .env 的 ZHIPU_API_KEY。"""
    if _ai_runtime is not None:
        return str(_ai_runtime.get("api_key") or "")
    return zhipu_key()


def ai_endpoint():
    """生效的 (base_url, model)：面板配置 > 持久化设置 > 出厂默认。"""
    settings = get_settings()
    base_url = settings.get("ai_base_url") or DEFAULTS["ai_base_url"]
    model = settings.get("ai_model") or DEFAULTS["ai_model"]
    if _ai_runtime:
        base_url = _ai_runtime.get("base_url") or base_url
        model = _ai_runtime.get("model") or model
    return base_url, model
