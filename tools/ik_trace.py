"""看逆解每一步的残差轨迹，定位为什么近处目标会发散。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sim.backend.core.kinematics import RobotModel, wrap_deg, _solve6   # noqa: E402

m = RobotModel("elfin5")
lo, up = m.lower, m.upper
Q0 = [29.069, -17.547, -120.901, 2.186, -74.089, -101.866]
p = m.pose_vec(Q0)
TGT = [p[0] + 80, p[1] - 60, p[2] - 40, p[3], p[4], p[5] + 25]
print("起点位姿", [round(v, 2) for v in p])
print("目标位姿", [round(v, 2) for v in TGT])
print("限位", [round(v, 1) for v in lo], [round(v, 1) for v in up])

w = m.ORIENT_MM_PER_DEG
q = list(Q0)
lam = 0.05
for it in range(25):
    e = m._err(q, TGT)
    es = e[:3] + [v * w for v in e[3:]]
    j = [row[:3] + [v * w for v in row[3:]] for row in m.jacobian(q)]
    jjt = [[sum(j[r][k] * j[c][k] for k in range(6)) for c in range(6)] for r in range(6)]
    md = sum(jjt[i][i] for i in range(6)) / 6.0
    for i in range(6):
        jjt[i][i] += lam * lam * md
    y = _solve6(jjt, es)
    if y is None:
        print(it, "singular")
        break
    dq = [-sum(j[c][r] * y[r] for r in range(6)) for c in range(6)]
    big = max(abs(v) for v in dq)
    sc = min(1.0, 15.0 / big) if big > 15 else 1.0
    nxt = [max(lo[i], min(up[i], q[i] + dq[i] * sc)) for i in range(6)]
    print(f"{it:2d} pos_err=({e[0]:8.2f},{e[1]:8.2f},{e[2]:8.2f}) "
          f"rot_err=({e[3]:7.2f},{e[4]:7.2f},{e[5]:7.2f}) |dq|max={big:8.2f} x{sc:.2f} "
          f"lam={lam:.4f} J1={q[0]:8.2f} J3={q[2]:8.2f}")
    q = nxt
