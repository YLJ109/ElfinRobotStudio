"""逆解自检：FK→IK 往返 + 真机实测姿态对账 + 够不着的目标必须诚实报未收敛。
跑法 python tools/check_ik.py
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sim.backend.core.kinematics import RobotModel   # noqa: E402

m = RobotModel("elfin5")
lo, up = m.lower, m.upper
Q_REAL = [29.069, -17.547, -120.901, 2.186, -74.089, -101.866]   # 2026-10-12 真机实测


def wrap(a):
    return (a + 180.0) % 360.0 - 180.0


def verify(name, q0, target, must_converge, tol=0.05):
    q, emm, edeg, ok = m.ik(q0, target, lo, up)
    back = m.pose_vec(q)
    dpos = max(abs(back[i] - target[i]) for i in range(3))
    drot = max(abs(wrap(back[3 + i] - target[3 + i])) for i in range(3))
    inlim = all(lo[i] - 1e-6 <= q[i] <= up[i] + 1e-6 for i in range(6))
    good = (ok == must_converge) and inlim and (dpos < tol and drot < tol if must_converge else True)
    print(f"{'PASS' if good else 'FAIL'}  {name:22s} 收敛={ok} 残差 pos={emm:8.3f}mm rot={edeg:7.3f}° "
          f"限位内={inlim}")
    if must_converge:
        print(f"        q={[round(v, 3) for v in q]}")
    return good


p_real = m.pose_vec(Q_REAL)
near = [p_real[0] + 80, p_real[1] - 60, p_real[2] - 40,
        p_real[3], p_real[4], p_real[5] + 25]
far = [0, 0, 0, 0, 0, 0]
beyond = m.pose_vec([0, 90, -150, 0, 60, 0])     # J3 -150 超出 URDF 的 ±149.5，够不着

ok_n = 0
total = 0
for fn in (
    lambda: verify("真机姿态原路往返", Q_REAL, p_real, True),
    lambda: verify("大跨度往返", [10, 20, -30, 40, -50, 60],
                   m.pose_vec([10, 20, -30, 40, -50, 60]), True),
    lambda: verify("近处平移+绕Z转 25°", Q_REAL, near, True),
    lambda: verify("零位起解可达目标", far, m.pose_vec([15, -20, 30, 10, -25, 40]), True),
    lambda: verify("限位外目标必须报未收敛", Q_REAL, beyond, False),
):
    total += 1
    ok_n += 1 if fn() else 0

# 反解结果拿去 FK 必须复现目标 —— 这条是"不许自欺"的底牌
q2, _, _, ok2 = m.ik(Q_REAL, near, lo, up)
b = m.pose_vec(q2)
drift = max(abs(b[i] - near[i]) for i in range(3))
total += 1
ok_n += 1 if drift < 0.05 else 0
print(f"{'PASS' if drift < 0.05 else 'FAIL'}  反解角 FK 复现目标位置，误差 {drift:.5f}mm")

print(f"\n{ok_n}/{total} 通过")
sys.exit(0 if ok_n == total else 1)
