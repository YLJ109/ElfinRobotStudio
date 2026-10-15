"""TCP translation pivot fit and three-point workcell frames; no hardware writes."""
import json
import math
import time
from . import config
from .kinematics import _solve6, rpy_to_mat
from .storage import write_json


def norm(vector):
    return math.sqrt(sum(v*v for v in vector))


def workcell(points):
    if len(points) != 3 or any(len(p) != 3 or not all(math.isfinite(float(v)) for v in p) for p in points):
        raise ValueError("需要原点、X方向点、XY平面点三个三维坐标")
    origin, xpoint, ypoint = points
    x = [xpoint[i] - origin[i] for i in range(3)]
    y = [ypoint[i] - origin[i] for i in range(3)]
    nx, ny = norm(x), norm(y)
    if min(nx, ny) < 1:
        raise ValueError("采样点距离太小，不能确定坐标方向")
    x = [v / nx for v in x]
    z = [x[1]*y[2] - x[2]*y[1], x[2]*y[0] - x[0]*y[2], x[0]*y[1] - x[1]*y[0]]
    sine = norm(z) / ny
    if sine < .05:
        raise ValueError("三个点接近共线，请重新采样")
    z = [v / norm(z) for v in z]
    y = [z[1]*x[2] - z[2]*x[1], z[2]*x[0] - z[0]*x[2], z[0]*x[1] - z[1]*x[0]]
    matrix = [[x[i], y[i], z[i], float(origin[i])] for i in range(3)] + [[0, 0, 0, 1]]
    return {"matrix": matrix, "conditioning_sine": round(sine, 5),
            "note": "三点确定坐标系；独立验证点才能评估绝对精度"}


def tcp_fit(poses):
    if not 4 <= len(poses) <= 100 or any(len(p) != 6 or not all(math.isfinite(float(v)) for v in p) for p in poses):
        raise ValueError("需要4至100组 X,Y,Z,A,B,C，保持工具尖端固定并改变姿态")
    rows, rhs = [], []
    rotations = [rpy_to_mat(*[math.radians(v) for v in p[3:]]) for p in poses]
    for pose, rotation in zip(poses, rotations):
        for i in range(3):
            rows.append(rotation[i][:3] + [-1. if i == j else 0. for j in range(3)])
            rhs.append(-pose[i])
    normal = [[sum(row[i]*row[j] for row in rows) for j in range(6)] for i in range(6)]
    from .planning import eigenvalues_symmetric
    eigenvalues = eigenvalues_symmetric(normal)
    if eigenvalues[0] < 1e-6 or eigenvalues[-1] / eigenvalues[0] > 1e8:
        raise ValueError("姿态变化不足，TCP拟合病态；请使用多个不同旋转轴采样")
    vector = [sum(row[i]*b for row, b in zip(rows, rhs)) for i in range(6)]
    result = _solve6(normal, vector)
    if result is None:
        raise ValueError("姿态变化不足，TCP不可辨识；请使用多个不同旋转轴采样")
    residuals = []
    for pose, rotation in zip(poses, rotations):
        error = [sum(rotation[i][j]*result[j] for j in range(3)) + pose[i] - result[3+i] for i in range(3)]
        residuals.append(norm(error))
    return {"tcp_offset_mm": result[:3], "pivot_base_mm": result[3:],
            "rms_mm": math.sqrt(sum(v*v for v in residuals) / len(residuals)),
            "max_mm": max(residuals), "samples": len(poses),
            "note": "仅标定TCP平移，未标定工具方向；采样残差不是独立定位精度"}


class CalibrationStore:
    def __init__(self):
        self.path = config.DATA_DIR / "calibrations.json"
        try:
            self.items = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.items = {"version": 0, "frames": {}, "tools": {}, "io": {}}

    def save(self, group, name, value):
        if group not in ("frames", "tools", "io") or not name or len(name) > 64:
            raise ValueError("标定类别或名称无效")
        self.items["version"] += 1
        value = dict(value, version=self.items["version"], updated=time.time())
        self.items[group][name] = value
        write_json(self.path, self.items)
        return value
