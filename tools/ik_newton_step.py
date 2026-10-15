"""隔离测试：一步精确牛顿步 dq = J⁻¹ e，看它是否真的指向目标。
不夹步长、不加阻尼、不做行缩放 —— 如果这一步都不对，问题在雅可比或位姿本身。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sim.backend.core.kinematics import RobotModel, _solve6   # noqa: E402

m = RobotModel("elfin5")
Q0 = [29.069, -17.547, -120.901, 2.186, -74.089, -101.866]
p0 = m.pose_vec(Q0)
TGT = [p0[0] + 80, p0[1] - 60, p0[2] - 40, p0[3], p0[4], p0[5] + 25]
print("起点", [round(v, 2) for v in p0])
print("目标", [round(v, 2) for v in TGT])

e = m._err(Q0, TGT)
print("误差 e =", [round(v, 3) for v in e])
J = m.jacobian(Q0)

# 先自证雅可比：绕 J2 转 0.5°，看预测的位姿变化对不对得上实际 FK
import copy
dq_test = [0, 0.5, 0, 0, 0, 0]
pred = [sum(J[i][j] * dq_test[j] for j in range(6)) for i in range(6)]
qp = copy.copy(Q0)
qp[1] += 0.5
actual = [TGT[i] - TGT[i] for i in range(6)]
p1 = m.pose_vec(qp)
actual = [p1[i] - p0[i] for i in range(3)] + [p1[3 + i] - p0[3 + i] for i in range(3)]
print("\n雅可比自检（J2 +0.5°）")
print("  预测 Δpose =", [round(v, 4) for v in pred])
print("  实际 Δpose =", [round(v, 4) for v in actual])

y = _solve6([row[:] for row in J], e)
if y is None:
    print("J 奇异")
    sys.exit(1)
print("\n精确牛顿步 dq = J⁻¹e =", [round(v, 3) for v in y])
q1 = [Q0[i] + y[i] for i in range(6)]
p_after = m.pose_vec(q1)
err_after = m._err(q1, TGT)
print("q1 =", [round(v, 3) for v in q1])
print("FK(q1) =", [round(v, 2) for v in p_after])
print("剩余误差 =", [round(v, 3) for v in err_after])
print("=> 一步后位置误差", round(max(abs(v) for v in err_after[:3]), 3), "mm，姿态",
      round(max(abs(v) for v in err_after[3:]), 3), "°")
