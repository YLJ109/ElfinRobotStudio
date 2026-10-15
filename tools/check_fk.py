"""用真机 Data Sheet 上报的基座坐标反验正运动学，判断"1:1 模型 + FK"到底对不对得上。

真机数据取自 tools/pose_capture.jsonl 与 live 抓取日志（同一台 E05-1195_Pro）。
"""

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.backend.core.kinematics import RobotModel   # noqa: E402

REAL = [
    # joints(°), PCS_Base(X,Y,Z mm, A,B,C °)  —— 2026-10-11 真机 10004 实测
    ([-3.124, -18.554, -122.896, 1.189, -74.089, -113.853],
     [-290.625, 12.364, 301.289, 178.103, -0.421, -69.574]),
    ([-3.125, -18.552, -122.897, 1.187, -74.090, -112.357],
     [-290.618, 12.373, 301.269, 178.12, -0.47, -71.07]),
]


def main():
    r = RobotModel("elfin5")
    print(f"模型 {r.variant}：可动关节 {r.names}")
    print(f"限位(°) 下 {r.lower}")
    print(f"限位(°) 上 {r.upper}")
    print(f"URDF 速度上限(°/s) {r.vmax}")
    print(f"末端法兰连杆 = {r.flange}")

    z = r.flange_pose([0] * 6)
    print(f"\n零位法兰位姿 {z['xyz_mm']} {z['rpy_deg']}")

    worst = 0.0
    for q, real in REAL:
        p = r.flange_pose(q)
        dx = [p["xyz_mm"][i] - real[i] for i in range(3)]
        dist = math.sqrt(sum(v * v for v in dx))
        worst = max(worst, dist)
        print(f"\n关节 {q}")
        print(f"  真机 PCS_Base  X={real[0]:9.3f} Y={real[1]:8.3f} Z={real[2]:9.3f}  "
              f"A={real[3]:8.3f} B={real[4]:7.3f} C={real[5]:8.3f}")
        print(f"  模型 FK        X={p['xyz_mm'][0]:9.3f} Y={p['xyz_mm'][1]:8.3f} Z={p['xyz_mm'][2]:9.3f}  "
              f"A={p['rpy_deg'][0]:8.3f} B={p['rpy_deg'][1]:7.3f} C={p['rpy_deg'][2]:8.3f}")
        print(f"  位置差         dX={dx[0]:+.3f} dY={dx[1]:+.3f} dZ={dx[2]:+.3f}  |Δ|={dist:.3f} mm")

    print(f"\n最大位置偏差 {worst:.1f} mm —— "
          + ("对得上，模型与 FK 可用" if worst < 30 else "对不上，坐标系/法兰定义需要重新核对"))

    cap = r.capsules(REAL[0][0])
    print(f"\n碰撞包络 {len(cap)} 根胶囊：")
    for c in cap:
        print(f"  {c['link']:16} a={[round(v,1) for v in c['a']]} b={[round(v,1) for v in c['b']]} "
              f"r={c['r_mm']:.1f}mm")


if __name__ == "__main__":
    main()
