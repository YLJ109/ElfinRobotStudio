"""统计一次绘制预检里 inspect_path 被调了多少次、各占多少时间。

结论性证据：首动延迟 ≈ 调用次数 × 单次耗时，不是某一次特别慢。
字形轮廓正常由前端 canvas 提取，这里用等价点数的折线代替，只为量出规模关系。
只读，隔离 data_dir。
"""
import asyncio
import math
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="cnt-"))
os.environ["ELFIN_DATA_DIR"] = str(TMP)
os.environ["HUYAN_NO_REAL"] = "1"

from backend.core.world import World  # noqa: E402
from backend.core import planning  # noqa: E402
from backend.application import drawing as drawing_mod  # noqa: E402

w = World(data_dir=TMP / "w")
w.settings["mode"] = "sim"
w.settings["sim_tool"] = {"kind": "pen", "rotation": [0, 0, 0]}
for i in range(9):
    w.objects.items.append(w.objects.sanitize({
        "name": f"件{i}", "kind": "box",
        "xyz": [-620 + (i % 3) * 60, 520 + (i // 3) * 60, 15],
        "size": [40, 40, 30], "collide": True, "gravity": False, "floor": True,
    }))


def stroke(n, cx, cy, r):
    return [[cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)]
            for k in range(n)]


real = planning.inspect_path
calls = []


def counted(*a, **kw):
    t = time.perf_counter()
    try:
        return real(*a, **kw)
    finally:
        calls.append((time.perf_counter() - t) * 1000)


drawing_mod.inspect_path = counted

for npts in (40, 120, 300):
    calls.clear()
    w.sim.q = [0., 0., 0., 0., 0., 0.]
    d = drawing_mod.Drawing(w)
    cfg = {"shape": "text", "text": "测", "font_size_mm": 60, "writing_z_mm": 160.0,
           "direction": 0, "range_center_mm": [-300.0, 300.0, 175.0],
           "center_policy": "fixed", "range_size_mm": [900.0, 900.0, 300.0],
           "center": [-300.0, 300.0, 175.0, 180.0, 0.0, 0.0], "plane": "xy",
           "width_mm": 400.0, "height_mm": 400.0, "angle_deg": 0.0,
           "speed_pct": 100, "repeats": 1,
           "strokes": [stroke(npts, 0.0, 0.0, 0.6)]}
    async def drive():
        d.preview(cfg)
        if d.worker is not None:
            await d.worker

    t0 = time.perf_counter()
    try:
        asyncio.run(drive())
        tail = "" if d.phase == "ready" else f" | 阶段 {d.phase}: {d.why[:34]}"
    except Exception as exc:  # noqa: BLE001
        tail = f" | 中止: {str(exc)[:38]}"
    total = (time.perf_counter() - t0) * 1000
    s = sorted(calls)
    med = f"{s[len(s)//2]:.0f}" if s else "-"
    print(f"路径点 {npts:3d} -> preview {total:6.0f} ms | inspect_path {len(calls):3d} 次 "
          f"| 累计 {sum(calls):6.0f} ms | 单次中位 {med} ms{tail}")
