"""性能基线：把主循环每一块的耗时拆开量，改前改后各跑一次贴对比。

跑法： python tools/perf_probe.py            # 打印并写 tools/_perf.json
      python tools/perf_probe.py after       # 写到 _perf_after.json，方便 diff
"""
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sim.backend.core import config                      # noqa: E402
from sim.backend.core.objects import ObjectStore         # noqa: E402
from sim.backend.core.safety import evaluate             # noqa: E402
from sim.backend.core.world import World                 # noqa: E402

TAG = sys.argv[1] if len(sys.argv) > 1 else "before"
OUT = Path(__file__).with_name(f"_perf_{TAG}.json")
N = 300


def bench(fn, n=N, warmup=20):
    for _ in range(warmup):
        fn()
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000.0)
    ts.sort()
    return {"mean_ms": round(statistics.fmean(ts), 4), "p50_ms": round(ts[len(ts) // 2], 4),
            "p95_ms": round(ts[int(len(ts) * 0.95)], 4), "max_ms": round(ts[-1], 4)}


w = World()
w.settings["mode"] = "sim"
q_moving = [29.0, -17.5, -120.9, 2.19, -74.0, -101.8]      # 今天真机实测的那组姿态
q_idle = [0.0] * 6
store = ObjectStore()

res = {"tag": TAG, "python": sys.version.split()[0], "samples": N, "parts": {}}

for name, q in (("运动位姿", q_moving), ("零位", q_idle)):
    caps = w.model.capsules(q)
    res["parts"][f"capsules[{name}]"] = bench(lambda q=q: w.model.capsules(q))
    res["parts"][f"safety[{name}]"] = bench(
        lambda q=q, caps=caps: evaluate(w.model, q, w.settings, w.sim.lower, w.sim.upper,
                                        caps=caps, obj_hits=[]))
    # 关掉全部碰撞检测时的对照：说明这三项各占多少
    loose = dict(w.settings, self_collision=False, ground_enabled=False,
                 fence=dict(w.settings["fence"], enabled=False))
    res["parts"][f"safety全关[{name}]"] = bench(
        lambda q=q, caps=caps: evaluate(w.model, q, loose, w.sim.lower, w.sim.upper,
                                        caps=caps, obj_hits=[]))

# 摆放物物理：放 12 个会掉的盒子再量
for i in range(12):
    store.add({"kind": "box", "size": [120, 120, 120], "xyz": [300 + i * 90, 0, 900],
               "gravity": True, "floor": True, "collide": True})
caps_m = w.model.capsules(q_moving)
res["parts"]["objects.step x12"] = bench(lambda: store.step(0.02, caps_m))
store.clear()

snap = w.snapshot()
res["parts"]["snapshot() 构造"] = bench(lambda: w.snapshot())
res["parts"]["json.dumps(快照) 每个客户端一次"] = bench(lambda: json.dumps(snap, ensure_ascii=False))
res["bytes/帧"] = len(json.dumps(snap, ensure_ascii=False).encode("utf-8"))
res["log 帧内条数"] = len(snap["log"])

# 整轮 tick：模拟 50Hz 跑 6 秒，看单帧耗时与预算占用
w.sim.move_j(q_moving)
w.sim.traj = None
w.sim.q = list(q_moving)
tick_ts = []
for _ in range(300):
    t0 = time.perf_counter()
    w._tick(0.02, time.monotonic())
    s = w.snapshot()
    json.dumps(s, ensure_ascii=False)
    tick_ts.append((time.perf_counter() - t0) * 1000.0)
tick_ts.sort()
res["整轮 tick+快照+序列化"] = {"mean_ms": round(statistics.fmean(tick_ts), 4),
                              "p50_ms": round(tick_ts[len(tick_ts) // 2], 4),
                              "p95_ms": round(tick_ts[int(len(tick_ts) * 0.95)], 4),
                              "max_ms": round(tick_ts[-1], 4)}
res["50Hz 预算占用%"] = round(statistics.fmean(tick_ts) / 20.0 * 100.0, 2)
res["settings"] = {k: w.settings[k] for k in ("self_collision", "ground_enabled", "limit_source")}

OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"[{TAG}] 每帧 {res['bytes/帧'] / 1024:.1f}KB，log {res['log 帧内条数']} 条，"
      f"50Hz 预算占用 {res['50Hz 预算占用%']}%")
for k, v in res["parts"].items():
    if isinstance(v, dict):
        print(f"  {k:34s} mean={v['mean_ms']:8.4f}ms p95={v['p95_ms']:8.4f}ms max={v['max_ms']:8.4f}ms")
