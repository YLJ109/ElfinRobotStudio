"""全局配置与持久化设置。

密钥只从环境变量 / .env 读，绝不写进源码。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from .storage import write_json

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "sim" / "backend" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
ENV_FILE = ROOT / ".env"


# 阈值口径：按用户 2026-10-11 的要求定为 80 / 90 / 100
SAFETY_YELLOW_PCT = 80.0
SAFETY_RED_PCT = 90.0
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
    "sim_speed_pct": 10.0,
    "real_speed_pct": 5.0,              # 实测口径：真机只放行过 5%
    "vel_source": "urdf",               # urdf(90°/s) | manual(J1-4 180, J5-6 200)
    "limit_source": "controller",       # controller（手册 p54=协议 p69）| urdf（建模口径）
    # 围栏 = 允许工作范围（keep-in）。默认给足余量：E05 臂展 800mm、零位法兰高 1195mm。
    "fence": {"x": [-1200, 1200], "y": [-1200, 1200], "z": [-600, 1400], "enabled": True},
    "self_collision": True,
    "ground_enabled": True,
    "ghost_opacity": 0.34,              # 残影那台假手的透明度
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
    # 开机即连真机（点对点直连时的默认姿势）。关掉就以仿真启动。
    "auto_connect": True,
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
# （360/135/153/360/180/360），所以这套是"控制器真会执行"的口径，默认用它做 80/90/100 告警。
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


def get_settings():
    global _settings
    with _lock:
        if _settings is None:
            _settings = dict(DEFAULTS)
            if settings_path().exists():
                try:
                    loaded = json.loads(settings_path().read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    loaded = {}
                # 旧版只有一个全局 speed_pct，升级时迁到仿真那一路，
                # 免得用户辛苦调好的数被默认值冲掉。
                legacy = loaded.pop("speed_pct", None)
                if legacy is not None and "sim_speed_pct" not in loaded:
                    loaded["sim_speed_pct"] = legacy
                loaded.pop("ghost_len", None)      # 旧"残影帧数"：改成假手模型后没有意义了
                _settings.update(loaded)
            _settings.pop("speed_pct", None)
            # 2026-10-12 拆掉"替操作者做主"的真机保险：软件不再限制能动哪根轴、
            # 单步多大、速度多快。旧配置里残留的这几项直接丢掉。
            for k in ("motion_guard_on", "motion_axes", "motion_max_step_deg",
                      "motion_speed_max_pct"):
                _settings.pop(k, None)
        return _settings


def save_settings(patch):
    with _lock:
        s = get_settings()
        s.update(patch)
        write_json(settings_path(), s)
        return dict(s)


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
