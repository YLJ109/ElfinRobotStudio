"""分拣一次「开始」的耗时构成：是几何慢，还是调用次数多、IK 贵。

只读，隔离 data_dir，不起服务。
"""
import cProfile
import os
import pstats
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="sort-"))
os.environ["ELFIN_DATA_DIR"] = str(TMP)
os.environ["HUYAN_NO_REAL"] = "1"

import asyncio  # noqa: E402

from backend.core.world import World  # noqa: E402
from backend.robot_play_routes import RobotPlay  # noqa: E402

w = World(data_dir=TMP / "w")
w.settings["mode"] = "sim"
w.settings["sim_tool"] = {"kind": "vacuum-reference", "rotation": [0, 0, 90]}
w.robot_play = RobotPlay(w)
w.sim.q = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

from backend.play_routes import PlayService  # noqa: E402

group = PlayService(w).setup(
    {"mode": "sorting", "criterion": "color",
     "counts": {"red": 3, "green": 3, "blue": 3}})["group"]
print("工件数", len([o for o in w.objects.list() if (o.get("task") or {}).get("group") == group]))

payload = {"group": group, "action": "sorting", "perception": "scene",
           "clearance": 120, "speed": 100, "wait": [400, 0, 450, 180, 0, 0],
           "criterion": "color"}

pr = cProfile.Profile()
pr.enable()
r = asyncio.run(w.robot_play.generate(payload))
pr.disable()
print("ok =", r.get("ok"), "| why =", str(r.get("why"))[:60])
st = pstats.Stats(pr)
st.sort_stats("cumulative").print_stats(16)
