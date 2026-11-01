"""云台式手掌追踪：末端原地转动，让摄像头光轴对准手掌。

与 hand_follow 的区别是控制目标不同：
* hand_follow 让末端**位置**跟到手掌映射点上；
* hand_track 让末端**朝向**指着手掌，位置基本不动，所以画面中心始终落在手上。

范围是硬限制：累计转角超过上限就停在边界，不会继续跟，也不会自己走回去。
只做纯几何与状态，不碰传输、不碰线程，便于离线判定。
"""
from __future__ import annotations

import math

from .kinematics import mat_to_xyzrpy, rpy_to_mat, rotation_error, _mat_mul
from .camera_frame import camera_front_rpy


def _normalize(v):
    n = math.sqrt(sum(c * c for c in v))
    if n < 1e-9:
        return None
    return [c / n for c in v]


def _column(m, index):
    return [m[0][index], m[1][index], m[2][index]]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def direction_from_pixel(u, v, *, hfov_deg=60.0, aspect=4 / 3):
    """归一化图像坐标 → 相机坐标系下的射线方向（z 为光轴正向）。

    u/v 用 0~1 表达，中心是 (0.5, 0.5)。超出画面也算，追踪就是要把手拉回中心。
    """
    for name, value in (('u', u), ('v', v)):
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'{name} 必须是有限数值')
    if not math.isfinite(hfov_deg) or not 15 <= hfov_deg <= 150:
        raise ValueError('水平视场角必须在 15~150 度之间')
    half = math.tan(math.radians(hfov_deg) / 2)
    x = (float(u) - .5) * 2 * half
    y = -((float(v) - .5) * 2 * half) / max(aspect, 1e-6)
    return _normalize([x, y, 1.0])


def _axis_angle_to_direction(current_rpy, target_dir):
    """返回把 z 轴转到 target_dir 所需的 (单位轴, 角度)；已对准返回 (None, 0)。"""
    current = rpy_to_mat(*map(math.radians, current_rpy))
    axis_now = _normalize(_column(current, 2))
    want = _normalize(target_dir)
    if axis_now is None or want is None:
        return None, 0.0
    axis = _cross(axis_now, want)
    sin_a = math.sqrt(sum(c * c for c in axis))
    cos_a = max(-1.0, min(1.0, _dot(axis_now, want)))
    if sin_a < 1e-9:
        if cos_a > 0:
            return None, 0.0                     # 已经对准
        # 正好反号：绕当前 x 轴翻 180°，避免除零。
        return _normalize(_column(current, 0)), 180.0
    return [c / sin_a for c in axis], math.degrees(math.atan2(sin_a, cos_a))


def rotate_rpy(current_rpy, axis, angle_deg):
    """绕给定世界轴把当前姿态旋转 angle_deg，返回新的 RPY。

    必须走完整的 Rodrigues 公式：R = I + sinθ·K + (1-cosθ)·K²。早先这里误写成
    I + K + K²（即把 k 直接当成 axis·θ），那是小角度近似，转 90° 会整体偏掉
    —— 实测 z 轴本该指向 [0,-1,0]，结果算出 [-0.52,-0.73,0.44]。
    """
    current = rpy_to_mat(*map(math.radians, current_rpy))
    a = math.radians(angle_deg)
    x, y, z = axis
    skew = [[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]]
    sq = [[sum(skew[i][t] * skew[t][j] for t in range(3)) for j in range(3)] for i in range(3)]
    s, c = math.sin(a), 1.0 - math.cos(a)
    delta = [[(1.0 if i == j else 0.0) + s * skew[i][j] + c * sq[i][j] for j in range(3)]
             for i in range(3)]
    # kinematics 用 4x4 齐次矩阵，旋转增量补齐平移行再乘。
    delta4 = [delta[0] + [0.0], delta[1] + [0.0], delta[2] + [0.0], [0.0, 0.0, 0.0, 1.0]]
    rotated = _mat_mul(delta4, current)
    _, rpy = mat_to_xyzrpy(rotated)
    return [math.degrees(v) for v in rpy]


def look_at_rpy(current_rpy, target_dir):
    """返回让末端 z 轴指向 target_dir 的 RPY，尽量保持原有滚转。

    绕"当前轴 → 目标轴"的最小旋转做，所以绕光轴的滚转不会自己漂。
    """
    axis, angle = _axis_angle_to_direction(current_rpy, target_dir)
    if axis is None:
        return list(current_rpy) if angle == 0.0 else rotate_rpy(current_rpy, [1.0, 0.0, 0.0], 180.0)
    return rotate_rpy(current_rpy, axis, angle)


def face_rpy(direction):
    """z 轴指向 direction（世界系）的姿态，画面上下对齐世界 +Z。

    朝向不能写死成某个常量：基座正面在 -X，但框和手掌可以被拖到任何一侧，
    写死"朝 -Y"时目标在别处，末端就是侧着的（实测 A=90/B=0/C=0 在默认机位
    下正好横向对着画面）。按输入方向现算，指哪儿朝哪儿。
    """
    z = _normalize(direction)
    if z is None:
        return [0.0, 0.0, 0.0]
    # 用世界 +Z 当"上"，保证画面上下不随目标位置歪掉。
    x = _normalize(_cross([0.0, 0.0, 1.0], z))
    if x is None:            # 目标在正上方/正下方：换 +X 兜底，避免退化
        x = _normalize(_cross([1.0, 0.0, 0.0], z)) or [1.0, 0.0, 0.0]
    y = _cross(z, x)
    matrix = [[x[0], y[0], z[0], 0.0],
              [x[1], y[1], z[1], 0.0],
              [x[2], y[2], z[2], 0.0],
              [0.0, 0.0, 0.0, 1.0]]
    _, rpy = mat_to_xyzrpy(matrix)
    return [math.degrees(v) for v in rpy]


def box_face_rpy(center, origin=(0.0, 0.0, 0.0)):
    """从 origin 看向 center 的姿态。"""
    return face_rpy([center[i] - origin[i] for i in range(3)])


class HandTrack:
    """追踪状态机：只负责“该转多少”，不负责发指令。"""

    # 真正的范围是用户设的那个空间盒子：手再偏也只会落在盒子里，方向天然有界。
    # 这个角度上限只是兜底，给到 120° 基本不会误拦；把盒子调小才是收紧范围的正路。
    DEFAULT_MAX_TURN_DEG = 120.0
    DEFAULT_STEP_DEG = 1.5
    DEFAULT_HFOV_DEG = 60.0
    CENTER_EPS_PX = .02          # 手在中心 2% 内视为已对准，不再微动
    # 判定"已对准"的夹角。必须大于 IK 的姿态容差（2°），否则解出来的姿态
    # 永远差一点点对不上，去中心这一步就卡死在那里走不完。
    ALIGN_TOL_DEG = 2.5
    # 末端停在框中心后方这么远，镜头朝正面看手掌。相机贴在手掌上什么都看不见，
    # 退一步手才落在画面里；退太多又像"没跟到"。
    STANDOFF_MM = 120.0
    # 面向正面：工具轴（z）指向 -X，同时**绕工具轴转到让相机模块在上方**。
    # 相机位于物理法兰 -X 侧，TCP 还含工具安装 Rz90：不能把 TCP 的 X
    # 轴误当相机偏置。下列值只对应默认工具；实际按模型安装旋转计算。
    FACE_FRONT_RPY = (90.0, 0.0, -90.0)

    def __init__(self, model):
        self.model = model
        self.reset()

    def reset(self):
        self.active = False
        self.reason = ''
        self.base_rpy = None          # 起始朝向，用于累计转角上限
        self.base_q = None
        self.range_deg = self.DEFAULT_MAX_TURN_DEG
        self.step_deg = self.DEFAULT_STEP_DEG
        self.hfov_deg = self.DEFAULT_HFOV_DEG
        self.turned_deg = 0.0
        self.last_offset = None
        self.last_q = None
        # 手掌允许出现的空间盒子：追踪只能在这个范围里找手，出框就不追。
        self.enclosure = None
        self.center = None            # 世界坐标中心
        self.size = None              # 盒子自身的宽/深/高
        self.direction = 0            # 绕竖直轴的朝向，和跟随一致
        self.centered = False          # 是否已经完成"先去中心"这一步

    @staticmethod
    def _enclosure(value):
        if not isinstance(value, dict):
            raise ValueError('追踪范围必须是对象')
        lo, hi = value.get('min'), value.get('max')
        if not isinstance(lo, list) or not isinstance(hi, list) or len(lo) != 3 or len(hi) != 3:
            raise ValueError('追踪范围需要 min/max 各三个数')
        out = []
        for name, pair in (('min', lo), ('max', hi)):
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in pair):
                raise ValueError(f'追踪范围 {name} 必须是有限数值')
            out.append([float(v) for v in pair])
        if any(out[0][i] >= out[1][i] for i in range(3)):
            raise ValueError('追踪范围的 min 必须小于 max')
        if any(abs(out[1][i] - out[0][i]) > 3000 for i in range(3)):
            raise ValueError('追踪范围单边不能超过 3000mm')
        return {'min': out[0], 'max': out[1]}

    def _center_point(self):
        if self.center is not None:
            return list(self.center)
        lo, hi = self.enclosure['min'], self.enclosure['max']
        return [(lo[i] + hi[i]) / 2 for i in range(3)]

    def _point_from_pixel(self, u, v, depth):
        """把手掌在画面里的位置映射到范围盒子里的一个点。

        盒子按 direction 旋转，和跟随的工作区同一套约定：center 是世界坐标，
        size 是盒子自己的宽/深/高，direction 是绕竖直轴的朝向。
        """
        uu = .5 if u is None else max(0., min(1., float(u)))
        vv = .5 if v is None else max(0., min(1., float(v)))
        dd = .5 if depth is None else max(0., min(1., float(depth)))
        if self.center is None or self.size is None:
            lo, hi = self.enclosure['min'], self.enclosure['max']
            return [lo[0] + (hi[0] - lo[0]) * uu,
                    lo[1] + (hi[1] - lo[1]) * dd,
                    lo[2] + (hi[2] - lo[2]) * (1. - vv)]
        cx, cy, cz = self.center
        sx, sy, sz = self.size
        lx, ly = (uu - .5) * sx, (dd - .5) * sy
        a = math.radians(self.direction)
        c, s = math.cos(a), math.sin(a)
        return [cx + lx * c - ly * s, cy + lx * s + ly * c, cz + (.5 - vv) * sz]

    def configure(self, payload, *, current_q, current_rpy=None):
        if not isinstance(payload, dict):
            raise ValueError('追踪配置必须是对象')
        range_deg = payload.get('range_deg', self.DEFAULT_MAX_TURN_DEG)
        step_deg = payload.get('step_deg', self.DEFAULT_STEP_DEG)
        hfov_deg = payload.get('hfov_deg', self.DEFAULT_HFOV_DEG)
        enclosure = self._enclosure(payload.get('enclosure')) if payload.get('enclosure') else None
        center = payload.get('center')
        size = payload.get('size')
        direction = payload.get('direction', 0)
        if center is not None or size is not None:
            if not (isinstance(center, list) and isinstance(size, list)
                    and len(center) == 3 and len(size) == 3
                    and all(type(v) in (int, float) and math.isfinite(v) for v in [*center, *size])):
                raise ValueError('追踪范围需要 center 与 size 各三个数')
            if any(v <= 0 for v in size):
                raise ValueError('范围尺寸必须大于 0')
            center = [float(v) for v in center]
            size = [float(v) for v in size]
            enclosure = {'min': [center[i] - size[i] / 2 for i in range(3)],
                         'max': [center[i] + size[i] / 2 for i in range(3)]}
        if direction not in (0, 90, 180, 270):
            direction = 0
        if not isinstance(range_deg, (int, float)) or not 1 <= range_deg <= 120:
            raise ValueError('追踪范围必须在 1~120 度之间')
        if not isinstance(step_deg, (int, float)) or not .1 <= step_deg <= 10:
            raise ValueError('单步转角必须在 0.1~10 度之间')
        if not isinstance(hfov_deg, (int, float)) or not 15 <= hfov_deg <= 150:
            raise ValueError('水平视场角必须在 15~150 度之间')
        self.reset()
        self.base_q = list(current_q)
        start_rpy = list(current_rpy) if current_rpy is not None else list(self.model.pose_vec(list(current_q))[3:])
        self.range_deg, self.step_deg, self.hfov_deg = float(range_deg), float(step_deg), float(hfov_deg)
        self.enclosure = enclosure
        self.center = center
        self.size = size
        self.direction = direction
        if enclosure is not None:
            # 末端正对工作框：方向取"基座 → 框中心"，框在哪就朝哪。
            # 早先写死成朝 -Y，而基座正面其实在 -X，结果末端横向对着画面
            # （界面上看就是"侧着"）。固定方向还顺带保证了不管上一次停在
            # 什么姿态，追踪开始时都是同一个视角。
            self.center_rpy = camera_front_rpy(self.model,[-1.,0.,0.])
        else:
            self.center_rpy = list(start_rpy)
        self.base_rpy = list(self.center_rpy)
        # 有范围盒时先进"去中心"阶段：先把镜头对准盒子中心，再跟手掌。
        self.centered = enclosure is None
        self.active = True
        self.reason = '追踪已就绪，先去中心' if enclosure is not None else '追踪已就绪'
        return {'ok': True, 'range_deg': self.range_deg, 'step_deg': self.step_deg,
                'hfov_deg': self.hfov_deg, 'base_rpy': self.base_rpy,
                'enclosure': self.enclosure, 'centered': self.centered}

    def stop(self, reason='已停止追踪'):
        self.active = False
        self.reason = reason
        return {'ok': True}

    def _turned_from_base(self, rpy):
        base = rpy_to_mat(*map(math.radians, self.base_rpy))
        now = rpy_to_mat(*map(math.radians, rpy))
        return math.sqrt(sum(v * v for v in rotation_error(now, base)))

    def _retreat_to_reach(self, seed, point, rpy, lower, upper):
        """把够不到的目标朝盒子中心收缩，返回最靠近目标的那个可达点。

        二分 4 次即可把边界定位到目标与中心间距的 1/16（默认盒子上就是十几
        毫米），比逐级扫描省一半 IK 次数。返回 (点, 关节, 位置残差, 姿态残差)。
        """
        center = self._center_point()
        near, far, best = 0.0, 1.0, None
        for _ in range(4):
            ratio = (near + far) / 2
            probe = [point[i] + (center[i] - point[i]) * ratio for i in range(3)]
            q, emm, edeg, converged = self.model.ik(
                list(seed), [*probe, *rpy], lower, upper,
                iters=200, max_restarts=6, position_only=False, tol_mm=6.0, tol_deg=4.0)
            if converged:
                far, best = ratio, (probe, q, emm, edeg)
            else:
                near = ratio
        return best

    def _world_bounds(self):
        """框在**世界坐标**里的上下界。

        self.enclosure 是未旋转的盒子，direction 90/270 时 X/Y 要交换 ——
        场景里画框、算手掌位置都按旋转后的来，只有这里直接用 enclosure
        会算到另一个坐标系去（表现为"末端越出框"）。
        """
        cx, cy, cz = self._center_point()
        size = self.size or [900.0, 300.0, 500.0]
        if self.direction in (90, 270):
            hx, hy = size[1] / 2, size[0] / 2
        else:
            hx, hy = size[0] / 2, size[1] / 2
        return [cx - hx, cy - hy, cz - size[2] / 2], [cx + hx, cy + hy, cz + size[2] / 2]

    def _pixel_goal(self, u, v, depth=None):
        """手掌在画面里的位置 → 末端该站哪（姿态固定为面向正面）。

        末端就站在**手掌映射点正后方** STANDOFF_MM 处，镜头朝正面看过去，
        手掌正好在画面中心。这里必须复用 _point_from_pixel：前端画那颗绿手掌
        用的就是它，两边各算一套的话，场景里手掌在这、机器人跑去了框外，
        对不上的正是这个。
        """
        hand = self._point_from_pixel(u, v, depth)
        lo, hi = self._world_bounds()
        # 退后可能把末端推出框外（手掌贴框前壁时）：夹在框的世界范围内，
        # 机器人不跑到框外面去。
        x = min(max(hand[0] + self.STANDOFF_MM, lo[0]), hi[0])
        y = min(max(hand[1], lo[1]), hi[1])
        z = min(max(hand[2], lo[2]), hi[2])
        return [x, y, z], list(self.center_rpy)

    def step(self, *, u, v, current_q, lower, upper, depth=None, speed_pct=None):
        """输入手掌在画面里的位置，返回 (ok, goal_q, detail)。

        设了范围盒时：先朝盒子中心转过去（"去中心"），到位后才开始跟手掌，
        并且手掌位置只会落在盒子内 —— 转到范围边界就停，不会追出框。

        单步转角跟着顶栏速度走：100% 时一帧最多转 7° 左右，10% 时不到 1°，
        这样"快就追得快、慢就追得慢"，不用再手工填一个度数。
        """
        if not self.active:
            return False, None, self.reason or '追踪未开启'
        if isinstance(speed_pct, (int, float)) and math.isfinite(speed_pct):
            self.step_deg = max(.4, min(8., .4 + max(0., min(100., float(speed_pct))) * .066))
        pose = self.model.pose_vec(list(current_q))
        camera_pos = pose[:3]
        if self.enclosure is not None and not self.centered:
            # 去中心 = 退到框中心后方、镜头看向框中心，并且姿态一次给到位。
            # 姿态必须跟着目标走，不能沿用当前姿态（pose[3:]）：沿用的话
            # 位置到了、末端还朝天，画面里看不到正面的手，看上去就是一块板子
            # 平躺着乱晃（"擦玻璃"）。位置和姿态一步同时给，IK 才解成同一个姿态。
            # 去中心 = 悬到框中心上方、工具朝下。框被设得太高时（现场常见：
            # 直接沿用了跟随那个 z=700 的框），悬停点会超出臂展，逆解不收敛
            # —— 于是机器人一动不动，看着像"没反应"。这里逐级降低悬停高度，
            # 能到哪算哪，并把降了多少说出来，而不是干等一个失败。
            center = self._center_point()
            aim = aim_rpy = q = None
            for lift in (self.STANDOFF_MM, self.STANDOFF_MM / 2, 0.0, -60.0):
                probe = [center[0] + lift, center[1], center[2]]
                candidate, emm, edeg, converged = self.model.ik(
                    list(current_q), [*probe, *self.center_rpy], lower, upper,
                    iters=260, max_restarts=6, position_only=False, tol_mm=4.0, tol_deg=.8)
                if converged:
                    aim, aim_rpy, q = probe, list(self.center_rpy), candidate
                    break
            if aim is None:
                self.reason = (f'框中心够不到（差 {emm:.0f}mm/{edeg:.1f}°）：'
                               f'请把范围中心移到机器人正前方、高度调低')
                self.last_q = list(current_q)
                return True, list(current_q), self.reason
            goal = [*aim, *aim_rpy]
            actual = self.model.pose_vec(list(q))
            gap = math.dist(actual[:3], aim)
            _, align = _axis_angle_to_direction(actual[3:], _column(
                rpy_to_mat(*map(math.radians, aim_rpy)), 2))
            # 位置到位还不够：姿态也得真的转过来。只看位置会出现"位置到了、
            # 末端还朝天也算到中心"，之后跟手一直用错误姿态跑。
            self.centered = gap < 8.0 and align <= self.ALIGN_TOL_DEG
            if self.centered:
                self.turned_deg = 0.0
                self.reason = '已到中心'
            else:
                self.reason = f'正在去中心（差 {gap:.0f}mm/{align:.1f}°）'
            self.last_q = list(q)
            return True, list(q), self.reason
        if self.enclosure is not None:
            # 每帧重算提示：否则上一帧的"已到可达边界"会一直挂着，正常跟手
            # 时界面还在说"够不到"，看不出真实状态。
            self.reason = ''
            # IBVS：手掌在画面偏哪边，末端就往哪边移，把手掌拉回画面中心。
            # 末端始终停在框中心后方、水平朝正面，所以画面里永远看的是"前面"，
            # 不会翻成看地面/天花板/侧墙。
            aim, aim_rpy = self._pixel_goal(u, v, depth)
            goal = [*aim, *aim_rpy]
            # 以"去中心时的那组关节"为初值，而不是上一帧的解：连续用上一帧的
            # 解做种子会一路漂，实测从 400mm 的位置再解 800mm 就直接不收敛了。
            seed = list(self.base_q) if self.base_q else list(current_q)
            q, emm, edeg, converged = self.model.ik(seed, goal, lower, upper,
                                                    iters=220, max_restarts=6,
                                                    position_only=False, tol_mm=4.0, tol_deg=.8)
            if not converged:
                # 够不到不代表该停在原地，更不该一步缩回中心：盒子开得比臂展
                # 大时，靠基座那一侧本来就超出可达范围。朝盒子中心二分收缩，
                # 停在**最靠近目标**的可达点上 —— 手再往外推，机器人贴着边界
                # 跟；一步跳回中心会看成"手往右推，机器臂反而缩回去"。
                retreat = self._retreat_to_reach(seed, aim, aim_rpy, lower, upper)
                if retreat is None:
                    self.reason = f'这个位置够不到（差 {emm:.0f}mm/{edeg:.1f}°）'
                    self.last_q = list(current_q)
                    return True, list(current_q), self.reason
                aim, q, emm, edeg = retreat
                self.reason = '已到可达边界（再往外够不到）'
            gap = math.dist(self.model.pose_vec(list(q))[:3], aim)
            self.turned_deg = round(gap, 1)
            if not self.centered and gap < 8.0:
                self.centered = True
                self.reason = '已到中心'
            elif self.centered and not self.reason:
                self.reason = '已对准' if gap < 8.0 else ''
            self.last_q = list(q)
            return True, list(q), self.reason
        # 没有范围盒时退回"只转朝向"的老路径。
        cam_dir = direction_from_pixel(u, v, hfov_deg=self.hfov_deg)
        # 把相机系方向转到世界系：相机的 x/y/z 就是末端矩阵的三列。
        camera = rpy_to_mat(*map(math.radians, pose[3:]))
        target_dir = _normalize([
            camera[i][0] * cam_dir[0] + camera[i][1] * cam_dir[1] + camera[i][2] * cam_dir[2]
            for i in range(3)])
        if target_dir is None:
            return False, None, '手掌方向无效'
        self.last_offset = (None if u is None or v is None
                            else round(math.hypot(float(u) - .5, float(v) - .5), 4))
        axis, angle = _axis_angle_to_direction(pose[3:], target_dir)
        if axis is None or angle <= self.ALIGN_TOL_DEG:
            centered_now = self.centered
            if self.enclosure is not None and not self.centered:
                self.centered = True
                self.reason = '已到中心'
                # 到中心后重新计数：界面上的"已转 X°"应该是从中心偏出去多少，
                # 不是从随手停机的位置算起的累计值。
                self.turned_deg = 0.0
            self.last_q = list(current_q)
            return True, list(current_q), self.reason if not centered_now else '已对准'
        want_rpy = rotate_rpy(pose[3:], axis, min(angle, self.step_deg))
        # 范围限制：只在"已经在中心、开始跟手"之后才生效。去中心这一段本身
        # 不受范围约束，否则头一步就可能把自己卡在边界上。
        if self.centered and self._turned_from_base(want_rpy) > self.range_deg:
            self.reason = f'已到追踪范围上限 {self.range_deg:.0f}°'
            self.last_q = list(current_q)
            return True, list(current_q), self.reason
        target_pose = [*pose[:3], *want_rpy]
        # 原地转向时转动中心不是 TCP，转 3° 末端位置本来就会漂几毫米；要求
        # 位置也精确到 1mm 会让 IK 解不出来（实测残差 1.2mm/0.92° 就失败）。
        # 追踪要的是"指向对不对"，不是点位精度，所以位置放到 4mm、姿态 1.2°，
        # 单步只有 3°，这个容差不会让画面中心跑偏。
        q, emm, edeg, converged = self.model.ik(list(current_q), target_pose, lower, upper,
                                                iters=160, max_restarts=2,
                                                position_only=False, tol_mm=10.0, tol_deg=.4)
        if not converged:
            # 那个方向的姿态这台机器转不过去（关节限位或接近奇异）。追踪不该
            # 因此整条中断 —— 停在原处并说明，人把手挪回来就能继续跟。
            self.reason = f'朝这个方向转不过去（残差 {edeg:.1f}°）'
            self.last_q = list(current_q)
            return True, list(current_q), self.reason
        if any(not lo <= val <= hi for val, lo, hi in zip(q, lower, upper)):
            return False, None, '朝向解超出关节限位'
        self.turned_deg = self._turned_from_base(self.model.pose_vec(list(q))[3:])
        self.last_q = list(q)
        self.reason = ''
        return True, list(q), ''

    def snapshot(self):
        return {'active': self.active, 'reason': self.reason,
                'range_deg': self.range_deg, 'turned_deg': round(self.turned_deg, 2),
                'offset': None if self.last_offset is None else round(self.last_offset, 4),
                'centered': self.centered, 'enclosure': self.enclosure}
