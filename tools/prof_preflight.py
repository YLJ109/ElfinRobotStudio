"""定位路径预检的性能热点：分拣/绘制各跑一次预检，输出耗时分布。"""
import cProfile
import os
import pstats
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = Path(tempfile.mkdtemp(prefix="prof-"))
os.environ["ELFIN_DATA_DIR"] = str(TMP)
os.environ["HUYAN_NO_REAL"] = "1"

from backend.core.world import World  # noqa: E402
from backend.core import planning  # noqa: E402

w = World(data_dir=TMP / "w")
w.settings["mode"] = "sim"
model = w.motion_model()

# 造一个接近真实分拣规模的现场：9 个物料 + 2 个区域
for i in range(9):
    w.objects.items.append(w.objects.sanitize({
        "name": f"件{i}", "kind": "box", "xyz": [-380 + (i % 3) * 90, 300 + (i // 3) * 90, 15],
        "size": [40, 40, 30], "collide": True, "gravity": False, "floor": True,
    }))
objs = w.objects.list()
print("物体数", len(objs), "连杆数", len(model.capsules(w.current_joints(), conservative=True)))

q0 = w.current_joints()
target = [q + 25 for q in q0]

pr = cProfile.Profile()
t = time.monotonic()
pr.enable()
lower, upper = w.limits()
rep = planning.inspect_path(model, q0, target, w.settings, lower, upper, objs)
pr.disable()
elapsed = time.monotonic() - t
print(f"inspect_path 用时 {elapsed*1000:.0f} ms, 采样 {rep.get('samples')}")

st = pstats.Stats(pr)
st.sort_stats("cumulative").print_stats(14)
