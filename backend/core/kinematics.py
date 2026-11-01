"""正运动学 + 碰撞包络：几何全部来自官方 URDF 导出的 robot.json，不另立数据源。

约定：
  * 单位：URDF 是米/弧度；对外（和真机 Data Sheet 对齐）用毫米/度。
  * 关节顺序 = robot.json 里带 limit 的关节顺序（elfin_joint1..6）。
"""

from __future__ import annotations

import json
import math
import time
from .mesh_clearance import mesh_vertices, lowest_vertex
from ..paths import PROJECT_ROOT as ROOT, MODEL_DIR

# 真机 PCS_Base 的参考点在法兰面，而官方 URDF 的 elfin_end_link 沿自身 Z 多出 20mm。
# 2026-10-11 用两组真机(关节角, PCS_Base)实测反验：姿态差 0.001°、X/Y 差 0.7mm、
# Z 差恒定 20.008mm，所以这是一个固定偏置而不是模型错误。沿 elfin_end_link
# 自身 +Z 补 20mm 后，两组数据的位置差都收敛到 1.3mm 以内。
TCP_OFFSET_Z_MM = 20.0


def _mat_identity():
    return [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


def _mat_mul(a, b):
    # 固定4×4变换矩阵：正解/路径预检里被调用上百万次，逐单元展开比推导式快约2.5倍。
    # 语义与 a @ b 完全一致（行×列逐项相加），只是为了避开 Python 层循环与生成器。
    a0, a1, a2, a3 = a
    b0, b1, b2, b3 = b
    return [
        [a0[0]*b0[0] + a0[1]*b1[0] + a0[2]*b2[0] + a0[3]*b3[0],
         a0[0]*b0[1] + a0[1]*b1[1] + a0[2]*b2[1] + a0[3]*b3[1],
         a0[0]*b0[2] + a0[1]*b1[2] + a0[2]*b2[2] + a0[3]*b3[2],
         a0[0]*b0[3] + a0[1]*b1[3] + a0[2]*b2[3] + a0[3]*b3[3]],
        [a1[0]*b0[0] + a1[1]*b1[0] + a1[2]*b2[0] + a1[3]*b3[0],
         a1[0]*b0[1] + a1[1]*b1[1] + a1[2]*b2[1] + a1[3]*b3[1],
         a1[0]*b0[2] + a1[1]*b1[2] + a1[2]*b2[2] + a1[3]*b3[2],
         a1[0]*b0[3] + a1[1]*b1[3] + a1[2]*b2[3] + a1[3]*b3[3]],
        [a2[0]*b0[0] + a2[1]*b1[0] + a2[2]*b2[0] + a2[3]*b3[0],
         a2[0]*b0[1] + a2[1]*b1[1] + a2[2]*b2[1] + a2[3]*b3[1],
         a2[0]*b0[2] + a2[1]*b1[2] + a2[2]*b2[2] + a2[3]*b3[2],
         a2[0]*b0[3] + a2[1]*b1[3] + a2[2]*b2[3] + a2[3]*b3[3]],
        [a3[0]*b0[0] + a3[1]*b1[0] + a3[2]*b2[0] + a3[3]*b3[0],
         a3[0]*b0[1] + a3[1]*b1[1] + a3[2]*b2[1] + a3[3]*b3[1],
         a3[0]*b0[2] + a3[1]*b1[2] + a3[2]*b2[2] + a3[3]*b3[2],
         a3[0]*b0[3] + a3[1]*b1[3] + a3[2]*b2[3] + a3[3]*b3[3]]]


def _mat_trans(x, y, z):
    m = _mat_identity()
    m[0][3], m[1][3], m[2][3] = x, y, z
    return m


def _mat_rot(axis, ang):
    """绕任意单位轴旋转（Rodrigues）。"""
    x, y, z = axis
    n = math.sqrt(x * x + y * y + z * z) or 1.0
    x, y, z = x / n, y / n, z / n
    c, s = math.cos(ang), math.sin(ang)
    C = 1 - c
    return [
        [c + x * x * C, x * y * C - z * s, x * z * C + y * s, 0],
        [y * x * C + z * s, c + y * y * C, y * z * C - x * s, 0],
        [z * x * C - y * s, z * y * C + x * s, c + z * z * C, 0],
        [0, 0, 0, 1],
    ]


def rpy_to_mat(rx, ry, rz):
    """URDF 的 rpy = 固定轴 XYZ 顺序，等价 R = Rz(rz) @ Ry(ry) @ Rx(rx)。"""
    m = _mat_rot((0, 0, 1), rz)
    m = _mat_mul(m, _mat_rot((0, 1, 0), ry))
    m = _mat_mul(m, _mat_rot((1, 0, 0), rx))
    return m


def mat_to_xyzrpy(m):
    x, y, z = m[0][3], m[1][3], m[2][3]
    sy = -m[2][0]
    sy = max(-1.0, min(1.0, sy))
    ry = math.asin(sy)
    if abs(math.cos(ry)) > 1e-9:
        rx = math.atan2(m[2][1], m[2][2])
        rz = math.atan2(m[1][0], m[0][0])
    else:
        rx = math.atan2(-m[1][2], m[1][1])
        rz = 0.0
    return [x, y, z], [rx, ry, rz]


def _solve6(a, b):
    """带主元选取的高斯消元解 6x6；奇异就返回 None，让调用方知道解不出来。"""
    m = [list(row) + [b[i]] for i, row in enumerate(a)]
    for col in range(6):
        piv = max(range(col, 6), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        pv = m[col][col]
        for j in range(col, 7):
            m[col][j] /= pv
        for r in range(6):
            if r == col:
                continue
            f = m[r][col]
            if f:
                for j in range(col, 7):
                    m[r][j] -= f * m[col][j]
    return [m[i][6] for i in range(6)]


def wrap_deg(a):
    """把角度差折到 ±180°。姿态误差必须按 360 周期折，不然 179° 和 -179° 会被当成差 358°。"""
    return (a + 180.0) % 360.0 - 180.0


def rotation_error(target, current):
    """World-frame SO(3) logarithm in degrees, including near-pi rotations."""
    r = [[sum(target[i][k] * current[j][k] for k in range(3)) for j in range(3)] for i in range(3)]
    angle = math.acos(max(-1., min(1., (sum(r[i][i] for i in range(3)) - 1.) / 2.)))
    if angle < 1e-8:
        return [math.degrees((r[2][1] - r[1][2]) / 2), math.degrees((r[0][2] - r[2][0]) / 2),
                math.degrees((r[1][0] - r[0][1]) / 2)]
    if math.pi - angle < 1e-5:
        axis = [math.sqrt(max(0., (r[i][i] + 1.) / 2.)) for i in range(3)]
        biggest = max(range(3), key=lambda i: axis[i])
        for i in range(3):
            if i != biggest and axis[biggest] > 1e-8:
                axis[i] = (r[i][biggest] + r[biggest][i]) / (4 * axis[biggest])
        # Diagonals determine an axis only up to sign. Near (but not exactly)
        # pi the skew part still identifies the shortest signed direction.
        # Losing this sign made -179.9999deg interpolate via +179.9999deg.
        skew = [r[2][1]-r[1][2],r[0][2]-r[2][0],r[1][0]-r[0][1]]
        if math.sqrt(sum(v*v for v in skew))>1e-12 and sum(a*b for a,b in zip(axis,skew))<0:
            axis = [-v for v in axis]
    else:
        axis = [(r[2][1] - r[1][2]) / (2 * math.sin(angle)),
                (r[0][2] - r[2][0]) / (2 * math.sin(angle)),
                (r[1][0] - r[0][1]) / (2 * math.sin(angle))]
    return [math.degrees(angle) * v for v in axis]


def pose_rotation_distance(start, target):
    """Shortest SO(3) rotation angle in degrees, not an Euler component delta."""
    a = rpy_to_mat(*map(math.radians,start[3:]))
    b = rpy_to_mat(*map(math.radians,target[3:]))
    return math.sqrt(sum(v*v for v in rotation_error(b,a)))


def interpolate_pose(start, target, fraction):
    """Linear translation and shortest SO(3) geodesic (SLERP equivalent)."""
    if (len(start)!=6 or len(target)!=6 or type(fraction) not in (int,float)
            or not math.isfinite(fraction) or not 0<=fraction<=1
            or not all(type(v) in (int,float) and math.isfinite(v) for v in [*start,*target])):
        raise ValueError("位姿插值需要两个有限六维位姿和0至1的比例")
    if fraction==0: return list(start)
    if fraction==1: return list(target)
    a=rpy_to_mat(*map(math.radians,start[3:]))
    b=rpy_to_mat(*map(math.radians,target[3:]))
    vector=rotation_error(b,a)
    angle=math.sqrt(sum(v*v for v in vector))
    rotation=_mat_mul(_mat_rot(vector,math.radians(angle)*fraction),a) if angle>1e-10 else a
    _,rpy=mat_to_xyzrpy(rotation)
    return [start[i]+(target[i]-start[i])*fraction for i in range(3)]+list(map(math.degrees,rpy))


class RobotModel:
    def __init__(self, variant="elfin5", tcp_translation_mm=None, tcp_rpy_deg=None):
        self.variant = variant
        self.tcp_translation_mm = [float(v) for v in (tcp_translation_mm or [0, 0, 0])]
        self.tcp_rpy_deg = [float(v) for v in (tcp_rpy_deg or [0, 0, 0])]
        if len(self.tcp_translation_mm) != 3 or len(self.tcp_rpy_deg) != 3:
            raise ValueError("TCP 偏移和姿态必须各包含三个数")
        if not all(math.isfinite(v) for v in self.tcp_translation_mm + self.tcp_rpy_deg):
            raise ValueError("TCP 偏移和姿态必须是有限数")
        self.data = json.loads((MODEL_DIR / variant / "robot.json").read_text(encoding="utf-8"))
        self.joints = self.data["joints"]
        self.links = self.data["links"]
        # Prewarm source geometry during model construction, never on palm loss
        # or in the high-frequency motion/collision loop. Shared across models.
        self._ground_meshes={name:mesh_vertices(MODEL_DIR/variant/v['visual']['mesh'])
            for name,v in self.links.items() if (v.get('visual') or {}).get('mesh')}
        # 连杆 visual 的 origin 平移/姿态是 URDF 常量，过去在每次地面间隙评估里
        # 重算一遍 rpy_to_mat（含 3 次三角函数 + 3 次矩阵乘）。这里预计算一次，
        # 热循环里就只剩一次连杆变换乘法，数值完全一致。
        self._visual_offset={}
        for name, v in self.links.items():
            visual = v.get('visual') or {}
            origin_xyz, origin_rpy = visual.get('origin_xyz'), visual.get('origin_rpy')
            if origin_xyz is None or origin_rpy is None:
                continue
            self._visual_offset[name] = _mat_mul(_mat_trans(*origin_xyz),
                                                 rpy_to_mat(*origin_rpy))
        self.movable = [j for j in self.joints if j.get("limit")]
        self.names = [j["name"] for j in self.movable]
        self.lower = [math.degrees(j["limit"]["lower"]) for j in self.movable]
        self.upper = [math.degrees(j["limit"]["upper"]) for j in self.movable]
        self.vmax = [math.degrees(j["limit"]["velocity"]) for j in self.movable]   # °/s
        self.child_of = {j["parent"]: j for j in self.joints if j.get("parent")}
        self.root = next((j["parent"] for j in self.joints
                          if j.get("parent") == "world"), self.joints[0]["parent"])
        self.flange = self._find_flange()
        self._children = {}
        for j in self.joints:
            self._children.setdefault(j.get("parent"), []).append(j)
        self._origins = {j["name"]: _mat_mul(_mat_trans(*j["xyz"]), rpy_to_mat(*j["rpy"])) for j in self.joints}
        self._fk_key, self._fk_value = None, None
        # Conservative sphere from the root to the flange: triangle inequality
        # over the actual ancestor chain, never a fitted workspace assumption.
        by_child = {j['child']: j for j in self.joints}
        link, length = self.flange, abs(TCP_OFFSET_Z_MM)/1000
        while link in by_child:
            joint = by_child[link]
            length += math.sqrt(sum(v*v for v in joint['xyz']))
            link = joint['parent']
        self.reach_bound_mm = length * 1000 + math.sqrt(sum(v*v for v in self.tcp_translation_mm))

    def _find_flange(self):
        """末端法兰：优先 elfin_end_joint 的子连杆，否则最后一个可动关节的子连杆。"""
        for j in self.joints:
            if j["type"] == "fixed" and j.get("child", "").endswith("end_link"):
                return j["child"]
        return self.movable[-1]["child"]

    def joint_transform(self, j, q_rad):
        m = self._origins[j["name"]]
        if j.get("limit"):
            m = _mat_mul(m, _mat_rot(j["axis"], q_rad))
        return m

    def fk(self, q_deg):
        """返回 {link_name: 4x4 矩阵(基座坐标系)}；q_deg 长度 = 可动关节数。"""
        if len(q_deg) != len(self.movable):
            raise ValueError(f"需要 {len(self.movable)} 个关节角")
        if not all(math.isfinite(float(v)) for v in q_deg):
            raise ValueError("关节角必须是有限数值")
        key = tuple(q_deg)
        if key == self._fk_key:
            return self._fk_value
        qi = {self.movable[i]["name"]: math.radians(v) for i, v in enumerate(q_deg)}
        children = self._children
        out = {}
        stack = [(self.root, _mat_identity())]
        while stack:
            name, m = stack.pop()
            out[name] = m
            for j in children.get(name, []):
                stack.append((j["child"], _mat_mul(m, self.joint_transform(j, qi.get(j["name"], 0.0)))))
        self._fk_key, self._fk_value = key, out
        return out

    def _flange_matrix(self, q_deg):
        tf = self.fk(q_deg)
        m = tf.get(self.flange) or tf.get("elfin_link6")
        base = _mat_mul(m, _mat_trans(0, 0, TCP_OFFSET_Z_MM / 1000.0))
        tool = rpy_to_mat(*[math.radians(v) for v in self.tcp_rpy_deg])
        tool[0][3], tool[1][3], tool[2][3] = [v / 1000.0 for v in self.tcp_translation_mm]
        return _mat_mul(base, tool)

    def flange_pose(self, q_deg):
        m = self._flange_matrix(q_deg)
        xyz, rpy = mat_to_xyzrpy(m)
        return {"xyz_mm": [round(v * 1000, 3) for v in xyz],
                "rpy_deg": [round(math.degrees(v), 3) for v in rpy],
                "link": self.flange}

    def capsules(self, q_deg, shrink=0.90, conservative=False):
        """每连杆一根胶囊：中心 = 连杆原点 -> 网格包围盒中心，半径 = 包围盒最短边的半。

        这是"近似包络"，不是逐三角形检测；宁可略保守也不能漏。半径用最短边，
        避免细长连杆被吹大成球形误报。
        """
        tf = self.fk(q_deg)
        out = []
        for j in self.movable + [x for x in self.joints if not x.get("limit")]:
            child = j.get("child")
            link = self.links.get(child)
            if not link or not link.get("visual") or not link["visual"].get("bbox"):
                continue
            m = tf.get(child)
            if m is None:
                continue
            bb = link["visual"]["bbox"]
            c = bb["center"]
            vo = link["visual"]
            local = _mat_mul(_mat_trans(*vo["origin_xyz"]), rpy_to_mat(*vo["origin_rpy"]))
            if conservative:
                transform = _mat_mul(m, local)
                axis = max(range(3), key=lambda i: bb["size"][i])
                start, end = list(c), list(c)
                start[axis] -= bb["size"][axis] / 2
                end[axis] += bb["size"][axis] / 2
                radius = math.sqrt(sum((bb["size"][i] / 2)**2 for i in range(3) if i != axis))
                out.append({"link": child, "a": [v*1000 for v in _apply(transform,start)],
                            "b": [v*1000 for v in _apply(transform,end)], "r_mm": radius*1000})
                continue
            p = _apply(_mat_mul(m, local), c)
            r = min(bb["size"]) / 2.0 * shrink
            origin = _apply(m, [0, 0, 0])
            out.append({"link": child, "a": [v * 1000 for v in origin],
                        "b": [v * 1000 for v in p], "r_mm": r * 1000})
        library = getattr(self, "library_tool", None)
        if library and library.get('id')=='robotiq-2f85':
            from .tool_contact_geometry import capsules
            out.extend(capsules(tf[self.flange],rpy_to_mat(*map(math.radians,library['rotation'])),
                                library.get('closure',0.),self.flange))
        elif library:
            bounds = library["bounds_mm"]
            center = [(a+b)/2 for a,b in zip(bounds["min"],bounds["max"])]
            half = [(b-a)/2 for a,b in zip(bounds["min"],bounds["max"])]
            axis = max(range(3),key=lambda i:half[i])
            a,b = center[:],center[:]
            a[axis] -= half[axis]; b[axis] += half[axis]
            transform = _mat_mul(tf[self.flange],rpy_to_mat(*map(math.radians,library["rotation"])))
            out.append({"link":self.flange,
                "a":[v*1000 for v in _apply(transform,[v/1000 for v in a])],
                "b":[v*1000 for v in _apply(transform,[v/1000 for v in b])],
                "r_mm":math.sqrt(sum(half[i]**2 for i in range(3) if i!=axis))})
        tool = getattr(self, "reference_tool", None)
        if tool:
            d = tool["dimensions"]
            local = rpy_to_mat(*map(math.radians, tool["rotation"]))
            transform = _mat_mul(tf[self.flange], local)

            def capsule(a, b, radius):
                out.append({"link": self.flange,
                    "a": [v * 1000 for v in _apply(transform, [x / 1000 for x in a])],
                    "b": [v * 1000 for v in _apply(transform, [x / 1000 for x in b])],
                    "r_mm": radius})

            z = d["mountHeight"] + d["thickness"] / 2
            capsule([-d["length"]/2, 0, z], [d["length"]/2, 0, z],
                    math.hypot(d["width"]/2, d["thickness"]/2))
            for sign in (-1, 1):
                x = sign * (d["length"] / 2 - d["edge"])
                top = d["mountHeight"] + d["thickness"]
                capsule([x, 0, top], [x, 0, top+d["cupHeight"]], d["cupDiameter"]/2)
            if d["mountHeight"]:
                capsule([0, 0, 0], [0, 0, d["mountHeight"]], 20)
        if not library and not tool:
            # 正确：无工具也保留安装面检测点，与 tool_plane_gaps 使用同一坐标。
            # 安装面没有 visual 网格，不能因遍历不到它而漏掉全部物体检测。
            point = [v * 1000 for v in _apply(tf[self.flange], [0, 0, 0])]
            out.append({"link": self.flange, "a": point, "b": list(point), "r_mm": 0.0})
        return out


    # ---------------------------------------------------------------- 逆解
    def moving_ground_clearances(self,q_deg):
        """Signed floor gaps of actual moving link source meshes, in mm.

        The anchored first link rotates about the mounting axis but has an
        intentionally floor-touching pedestal. Do not use inflated capsule
        radii for floor gaps: they falsely extend below that mounting surface.
        Tool envelopes are independently evaluated by safety.evaluate.
        """
        tf=self.fk(q_deg)
        anchored=self.movable[0]['child'] if self.movable else None
        result=[]
        for joint in self.movable:
            child=joint['child'];visual=(self.links.get(child) or {}).get('visual') or {}
            box=visual.get('bbox')
            if child==anchored or child not in tf or not box:continue
            offset=self._visual_offset.get(child)
            if offset is None:continue
            transform=_mat_mul(tf[child],offset)
            points=self._ground_meshes.get(child)
            if points is not None:
                gap,point=lowest_vertex(points,transform)
                result.append({'link':child,'gap_mm':gap,'point_mm':point,'geometry':'source-mesh'})
            else:
                # Missing mesh must fail conservatively, never silently green.
                # Historical bbox.center is a vertex centroid, NOT box centre.
                center=[(a+b)/2 for a,b in zip(box['min'],box['max'])]
                gap=(_apply(transform,center)[2]-sum(abs(transform[2][i])*box['size'][i]/2 for i in range(3)))*1000
                result.append({'link':child,'gap_mm':gap,'geometry':'bbox-fallback'})
        return result

    def tool_plane_gaps(self,q_deg,fence):
        """Tool-to-four-walls/floor gaps; boxes/cylinders, or full animation envelope."""
        tf=self.fk(q_deg)
        reference=getattr(self,'reference_tool',None)
        library=getattr(self,'library_tool',None)
        rotation=reference['rotation'] if reference else library['rotation'] if library else [0,0,0]
        transform=_mat_mul(tf[self.flange],rpy_to_mat(*map(math.radians,rotation)))
        planes=[('正面墙',[1,0,0],-fence['x'][0]),('背面墙',[-1,0,0],fence['x'][1]),
                ('右侧墙',[0,1,0],-fence['y'][0]),('左侧墙',[0,-1,0],fence['y'][1]),
                ('地面',[0,0,1],0)]
        result=[]
        for name,normal,constant in planes:
            local=[sum(transform[i][j]*normal[i] for i in range(3)) for j in range(3)]
            offset=sum(transform[i][3]*normal[i] for i in range(3))*1000+constant
            values=[]
            def box(center,half):
                values.append(sum(a*b for a,b in zip(local,center))-sum(abs(a)*b for a,b in zip(local,half)))
            def cylinder(x,y,z0,z1,radius):
                values.append(local[0]*x+local[1]*y+min(local[2]*z0,local[2]*z1)
                              -radius*math.hypot(local[0],local[1]))
            if reference:
                d=reference['dimensions'];top=d['mountHeight']+d['thickness']
                box([0,0,d['mountHeight']+d['thickness']/2],
                    [d['length']/2,d['width']/2,d['thickness']/2])
                for sign in (-1,1):
                    cylinder(sign*(d['length']/2-d['edge']),0,top,top+d['cupHeight'],d['cupDiameter']/2)
                if d['mountHeight']:cylinder(0,0,0,d['mountHeight'],20)
            elif library:
                b=library['bounds_mm']
                box([(a+c)/2 for a,c in zip(b['min'],b['max'])],[(c-a)/2 for a,c in zip(b['min'],b['max'])])
            else:
                values.append(0.)
            extra={}
            if name=='地面' and getattr(self,'pen_tool',False):
                # Only the nib may touch the writing plane. The pen body and
                # every robot mesh retain their normal ground checks.
                from .sim_tool import PEN_LENGTH_MM,PEN_BODY_LENGTH_MM,PEN_RADIUS_MM
                tip=offset+local[2]*PEN_LENGTH_MM
                body=offset+min(0,local[2]*PEN_BODY_LENGTH_MM)-PEN_RADIUS_MM*math.hypot(*local[:2])
                extra={'pen_contact_gap_mm':tip,'pen_body_gap_mm':body}
            result.append({'surface':name,'link':self.flange,'gap_mm':offset+min(values),**extra,
                           'type':'ground' if name=='地面' else 'fence',
                           'geometry':'full-animation-envelope' if library else 'tool-primitives'})
        return result

    # 单位约定：位置一律 mm，姿态一律 **度**。混用弧度/度是这版之前真实的 bug 来源。
    ORIENT_MM_PER_DEG = 2.0     # 把 1° 姿态误差折成约 2mm 的等价量，最小二乘才不会被位置行淹没

    def pose_vec(self, q_deg):
        # Round only public readouts. Differentiating a rounded 0.001mm value
        # at 0.001deg steps produced a quantized Jacobian and false IK stalls.
        xyz, rpy = mat_to_xyzrpy(self._flange_matrix(q_deg))
        return [v * 1000 for v in xyz] + [math.degrees(v) for v in rpy]

    def _err(self, q_deg, target):
        """6 维任务空间误差：前 3 项 mm，后 3 项度（已折到 ±180）。"""
        cur = self.pose_vec(q_deg)
        rt = rpy_to_mat(*[math.radians(v) for v in target[3:]])
        rc = rpy_to_mat(*[math.radians(v) for v in cur[3:]])
        return [target[i] - cur[i] for i in range(3)] + rotation_error(rt, rc)

    def jacobian(self, q_deg, step=1e-3):
        """URDF geometric Jacobian: mm/degree and world rotation degree/degree."""
        tf = self.fk(q_deg)
        tcp = self._flange_matrix(q_deg)
        p = [tcp[i][3] for i in range(3)]
        cols = []
        for joint in self.movable:
            origin = _mat_mul(tf[joint['parent']], self._origins[joint['name']])
            axis = joint['axis']
            norm = math.sqrt(sum(v*v for v in axis)) or 1.0
            z = [sum(origin[i][k] * axis[k] / norm for k in range(3)) for i in range(3)]
            arm = [p[i] - origin[i][3] for i in range(3)]
            cross = [z[1]*arm[2]-z[2]*arm[1], z[2]*arm[0]-z[0]*arm[2], z[0]*arm[1]-z[1]*arm[0]]
            cols.append([v * 1000 * math.pi / 180 for v in cross] + z)
        return [[cols[j][i] for j in range(6)] for i in range(6)]

    def _cost(self, e, w=None):
        """加权残差范数：位置 mm、姿态 °，1° 折算成 w mm 的等价量。"""
        w = self.ORIENT_MM_PER_DEG if w is None else w
        return math.sqrt(sum(v * v for v in e[:3]) + w * w * sum(v * v for v in e[3:]))

    def ik(self, q0, target, lower=None, upper=None, iters=200,
           tol_mm=0.05, tol_deg=0.05, lam=1e-3, max_step_deg=20.0, restarts=True, max_restarts=8,
           position_only=False, deadline=None, cancelled=None):
        """牛顿步 + 回溯线搜索的逆解。

        六轴有多组解，优先沿 q0 所在分支求解，不保证全局最近。
        position_only 必须显式启用，只约束位置，姿态由求解器决定。
        够不着时返回 converged=False 并带上真实残差，不粉饰。
        返回 (q, 位置残差mm, 姿态残差°, 是否收敛)。
        """
        lo = list(lower) if lower else list(self.lower)
        up = list(upper) if upper else list(self.upper)
        q = [max(lo[i], min(up[i], v)) for i, v in enumerate(q0)]
        def task_error(joints):
            error = self._err(joints, target)
            return error[:3] + [0., 0., 0.] if position_only else error
        e = task_error(q)
        cost = self._cost(e)
        def interrupted():
            return (cancelled is not None and cancelled()) or (deadline is not None and time.monotonic() >= deadline)
        if math.sqrt(sum(v*v for v in target[:3])) > self.reach_bound_mm + math.sqrt(3)*tol_mm:
            return q, max(abs(v) for v in e[:3]), max(abs(v) for v in e[3:]), False

        def step_from(dq):
            big = max(abs(v) for v in dq)
            if big > max_step_deg:
                dq = [v * max_step_deg / big for v in dq]
            nxt = [max(lo[i], min(up[i], q[i] + dq[i])) for i in range(6)]
            ne = task_error(nxt)
            return nxt, ne, self._cost(ne)

        for _ in range(iters):
            if interrupted():
                break
            if max(abs(v) for v in e[:3]) < tol_mm and max(abs(v) for v in e[3:]) < tol_deg:
                return q, max(abs(v) for v in e[:3]), max(abs(v) for v in e[3:]), True
            j = self.jacobian(q)
            # 先试纯牛顿步（6x6 方阵，直接解）；奇异或病态时退到阻尼最小二乘
            orientation_weight = 0. if position_only else self.ORIENT_MM_PER_DEG
            weights = [1., 1., 1., orientation_weight, orientation_weight, orientation_weight]
            jw = [[v * weights[r] for v in j[r]] for r in range(6)]
            ew = [e[r] * weights[r] for r in range(6)]
            sol = _solve6([r[:] for r in jw], ew)
            normal = rhs = None
            # A finite Newton step can still be a bad direction near limits or
            # singularities. Try damped directions if its line search fails.
            improved = False
            for bump in range(-1, 7):
                if interrupted():
                    break
                if bump == -1:
                    dq = sol
                    # Huge Newton steps are a symptom of an ill-conditioned
                    # Jacobian. Normalizing them hides that condition and can
                    # make the solver oscillate forever at the workspace edge.
                    if dq is not None and max(abs(v) for v in dq) > max_step_deg * 10:
                        continue
                else:
                    if normal is None:
                        # 牛顿方向成功时不需要构造阻尼正规方程。
                        normal = [[sum(jw[k][r] * jw[k][c] for k in range(6)) for c in range(6)] for r in range(6)]
                        rhs = [sum(jw[r][c] * ew[r] for r in range(6)) for c in range(6)]
                    damped = [r[:] for r in normal]
                    for i in range(6):
                        damped[i][i] += lam * (10.0 ** bump)
                    dq = _solve6(damped, rhs)
                if dq is None or not all(math.isfinite(v) for v in dq):
                    continue
                alpha = 1.0
                for _b in range(10):
                    if interrupted():
                        break
                    nxt, ne, nc = step_from([v * alpha for v in dq])
                    if nc < cost - max(1e-8, cost * 1e-7):
                        q, e, cost = nxt, ne, nc
                        improved = True
                        break
                    alpha *= 0.5
                if improved:
                    break
            if not improved:
                break
        best = (q, max(abs(v) for v in e[:3]), max(abs(v) for v in e[3:]), False)
        if best[1] < tol_mm and best[2] < tol_deg:
            return (*best[:3], True)
        if restarts:
            # Coordinated elbow/wrist perturbations escape zero-gradient fully
            # extended poses. Wrist seeds also avoid always choosing one branch.
            offsets = ([0,-8,16,0,-8,0], [0,8,-16,0,8,0],
                       [0,-25,50,0,-25,0], [0,25,-50,0,25,0],
                       [0,0,0,45,20,-45], [0,0,0,-45,-20,45],
                       [0,-15,30,90,-15,-90], [0,15,-30,-90,15,90])
            for offset in offsets[:max_restarts]:
                if interrupted():
                    break
                seed = [max(lo[i], min(up[i], q0[i]+offset[i])) for i in range(6)]
                candidate = self.ik(seed, target, lo, up, iters, tol_mm, tol_deg,
                                    lam, max_step_deg, restarts=False, position_only=position_only,
                                    deadline=deadline, cancelled=cancelled)
                if candidate[3]:
                    return candidate
                if candidate[1] + self.ORIENT_MM_PER_DEG*candidate[2] < best[1] + self.ORIENT_MM_PER_DEG*best[2]:
                    best = candidate
        return best


def _apply(m, p):
    x, y, z = p
    return [m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3],
            m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3],
            m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3]]


def _lerp(a, b, t):
    return [a[i] + (b[i] - a[i]) * t for i in range(3)]


def _seg_seg_closest(p1, q1, p2, q2):
    """两条线段最近点对（Ericson 闭式解）。退化平行时退回端点枚举。"""
    d1 = [q1[i] - p1[i] for i in range(3)]
    d2 = [q2[i] - p2[i] for i in range(3)]
    r = [p1[i] - p2[i] for i in range(3)]
    a = sum(v * v for v in d1)
    e = sum(v * v for v in d2)
    f = sum(d2[i] * r[i] for i in range(3))
    if a <= 1e-12 and e <= 1e-12:
        return p1, p2
    if a <= 1e-12:
        s, t = 0.0, max(0.0, min(1.0, f / e))
    else:
        c = sum(d1[i] * r[i] for i in range(3))
        if e <= 1e-12:
            t, s = 0.0, max(0.0, min(1.0, -c / a))
        else:
            b = sum(d1[i] * d2[i] for i in range(3))
            denom = a * e - b * b
            s = max(0.0, min(1.0, (b * f - c * e) / denom)) if denom > 1e-12 else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t, s = 0.0, max(0.0, min(1.0, -c / a))
            elif t > 1.0:
                t, s = 1.0, max(0.0, min(1.0, (b - c) / a))
    pA = [p1[i] + d1[i] * s for i in range(3)]
    pB = [p2[i] + d2[i] * t for i in range(3)]
    return pA, pB


def capsule_distance(c1, c2, n=None):
    """两根胶囊的表面间隙：<0 就是重叠。

    原来是 9×9 离散采样（一对就要 81 次 sqrt），换成闭式解后既快又准 ——
    采样还会漏掉细长交叉的最近点，闭式解不会。n 参数保留只为兼容旧调用。
    """
    pA, pB = _seg_seg_closest(c1["a"], c1["b"], c2["a"], c2["b"])
    return math.sqrt(sum((pA[i] - pB[i]) ** 2 for i in range(3))) - c1["r_mm"] - c2["r_mm"]
