"""拆解首动延迟：单次预检内部各阶段耗时 + 一段分拣运动的预检次数。

只读测量，不写真实数据目录。
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = Path(tempfile.mkdtemp(prefix="lat-"))
os.environ["ELFIN_DATA_DIR"] = str(TMP)
os.environ["HUYAN_NO_REAL"] = "1"

from backend.core.world import World  # noqa: E402
from backend.core import planning  # noqa: E402
from backend.core import safety  # noqa: E402
from backend.core.safety import object_capsules  # noqa: E402

w = World(data_dir=TMP / "w")
w.settings["mode"] = "sim"
# 关掉围栏/地面，隔离到"物体求距"这一项的贡献
w.settings["fence"]["enabled"] = False
w.settings["ground_enabled"] = False
model = w.motion_model()

for i in range(9):
    w.objects.items.append(w.objects.sanitize({
        "name": f"件{i}", "kind": "box",
        "xyz": [-380 + (i % 3) * 90, 300 + (i // 3) * 90, 15],
        "size": [40, 40, 30], "collide": True, "gravity": False, "floor": True,
    }))
objs = w.objects.list()
q = w.current_joints()
caps = model.capsules(q, conservative=True)
contact = object_capsules(model, caps)
print(f"物体 {len(objs)} | 全连杆胶囊 {len(caps)} | 参与物体监测 {len(contact)}")


def bench(fn, n=2000):
    fn()
    t = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t) / n * 1e6


cap = contact[0]
obj = objs[0]
mu = bench(lambda: safety._object_signed_gap(cap, obj))
mc = bench(lambda: model.capsules(q, conservative=True), 300)
me = bench(lambda: safety.evaluate(model, q, w.settings, *w.limits(), caps=caps), 300)
print(f"单次 _object_signed_gap          {mu:8.1f} us")
print(f"单次 model.capsules(全连杆)       {mc:8.1f} us")
print(f"单次 evaluate                     {me:8.1f} us")

per_sample = len(contact) * len(objs) * mu + mc + me
print(f"推算单采样点(全物体×全连杆+FK+评估) {per_sample/1000:8.2f} ms")

# 真实一段长距离运动：看采样数如何随角度增长
lower, upper = w.limits()
for deg in (10, 25, 60, 120):
    tgt = [v + deg for v in q]
    t = time.monotonic()
    rep = planning.inspect_path(model, q, tgt, w.settings, lower, upper, objs)
    dt = time.monotonic() - t
    print(f"单段 {deg:3d}°/轴 -> 采样 {rep['samples']:4d} | 间距 {rep['sample_spacing_deg']:5.2f}° | {dt*1000:6.0f} ms")
