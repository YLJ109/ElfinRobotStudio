"""复现并验证「仿真一打开就被碰撞告警锁死」的修复（仅本轮使用）。

只读仓库、只写临时目录（World(data_dir=...)），不连真机、不起服务。
运行：backend/.venv/Scripts/python.exe -B tools/check_startup_block.py
退出码 0 表示全部通过。
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="elfin-start-block-"))
os.environ["ELFIN_DATA_DIR"] = str(TMP)
os.environ["HUYAN_NO_REAL"] = "1"

from backend.core.world import World          # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + name + ("   | " + detail if detail else ""))


def new_world(tag):
    w = World(data_dir=TMP / tag)
    w.settings["mode"] = "sim"
    w.settings["fence"] = {"x": [-3000.0, 3000.0], "y": [-3000.0, 3000.0],
                           "z": [0.0, 3000.0], "enabled": True}
    w.settings["ground_enabled"] = False
    return w


def box(name, xyz, size=(80.0, 80.0, 80.0)):
    return {"name": name, "kind": "box", "xyz": list(xyz),
            "size": list(size), "scale": 1, "collide": True, "gravity": False,
            "floating": True, "floor": True}


def tick(w, n=4, dt=0.05):
    for _ in range(n):
        w._tick(dt, time.monotonic())


def main():
    # 1) 放在机器人基座/臂里的物体必须被位置检查认出来
    w = new_world("overlap")
    inside = w.objects.sanitize(box("贴臂块", [0.0, 0.0, 200.0], (120.0, 120.0, 120.0)))
    link = w.robot_overlap(inside)
    check("1 与机器人重叠的位置被识别", link is not None, f"link={link}")

    # 2) 空地上的物体不算重叠（不能把正常摆放也拦掉）
    far = w.objects.sanitize(box("空地块", [900.0, 900.0, 40.0]))
    check("2 空地摆放不误拦", w.robot_overlap(far) is None)

    # 3) 场景里本来就相交（例如旧现场/导入）→ 只报一次，不锁死仿真
    w2 = new_world("preexisting")
    # 模拟导入的旧现场：绕过落位校验，直接把相交物体放进现场
    w2.objects.items = [w2.objects.sanitize(box("旧现场块", [0.0, 0.0, 200.0], (120.0, 120.0, 120.0)))]
    tick(w2)
    level = w2.safety["level"]
    paused = bool(w2.sim.paused)
    texts = [row.get("text", "") for row in w2.log.tail(200)]
    joined = " ".join(str(t) for t in texts)
    check("3a 相交仍然被如实报告", level in ("alarm", "red", "ALARM", "RED"), f"level={level}")
    check("3b 静止相交不锁定仿真", paused is False, f"paused={paused}")
    check("3c 提示可退离而不是要求人工复位",
          ("沿脱离" in joined or "可沿" in joined) and "需人工复位" not in joined,
          f"log={joined[-120:]}")

    # 4) 移除障碍后告警自动解除（不需要人工复位）
    w2.objects.clear()
    tick(w2)
    check("4 障碍移走后自动恢复", w2.safety["level"] not in ("alarm", "ALARM", "red", "RED"),
          f"level={w2.safety['level']}")

    # 5) 运动中出现新碰撞仍然暂停（直接验决策函数，避免依赖后台线程时序）
    w3 = new_world("moving")
    w3._on_alarm(True)
    check("5 运动中碰撞仍然自动暂停", bool(w3.sim.paused), f"paused={w3.sim.paused}")
    w3.sim.paused = False
    w3._on_alarm(False)
    check("5b 静止告警不暂停", w3.sim.paused is False, f"paused={w3.sim.paused}")

    # 6) 承托用保守包络、命中判定用预检口径：两者必须分开
    w4 = new_world("caliber")
    model = w4.motion_model()
    q = w4.current_joints()
    caps = model.capsules(q)
    from backend.core.safety import object_capsules
    check("6a 命中口径排除固定底座",
          all(cap["link"] != getattr(model, "base", "elfin_base")
              for cap in object_capsules(model, caps)),
          "base 不在命中口径内")
    check("6b 命中口径不含膨胀包络",
          all(cap in object_capsules(model, caps) for cap in object_capsules(model, caps))
          and len(object_capsules(model, caps)) <= len(caps))

    total = len(RESULTS)
    bad = [name for name, ok in RESULTS if not ok]
    print(f"\n共 {total} 项，失败 {len(bad)} 项")
    for name in bad:
        print("  FAILED:", name)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
