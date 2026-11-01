"""Offline regressions for the end-effector palm tracking controller.

Covers the two things that were wrong in practice:
* "去中心" used to keep the current orientation, so the tool arrived at the box
  centre still pointing at the ceiling -- on screen that reads as a plate
  wiping the glass. It must arrive facing the operator.
* positions outside the arm's reach used to leave the arm standing still; they
  must retreat to the nearest reachable boundary instead.
"""
from _offline_guard import require_offline_copy
require_offline_copy()

import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core import sim_tool
from backend.core.hand_track import HandTrack
from backend.core.kinematics import rpy_to_mat
from backend.core.camera_frame import camera_axes


# 和跟随共用同一套工作区：中心在 -X（场景"正面"），宽 900、深 300、高 500。
CENTER = [-500.0, 0.0, 700.0]
SIZE = [900.0, 300.0, 500.0]
DIRECTION = 270


def build(tool=None):
    model = sim_tool.model_for('elfin5', sim_tool.normalize(tool or sim_tool.DEFAULT_TOOL))
    return model, model.lower, model.upper


def _angle_between(a, b):
    dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))
    return math.degrees(math.acos(dot))


def axes(model, q):
    """Extract the tool frame axes in world coordinates."""
    rotation = rpy_to_mat(*map(math.radians, model.pose_vec(list(q))[3:]))
    return [[rotation[i][c] for i in range(3)] for c in range(3)]


def drive_to_center(track, model, lower, upper, start, limit=10):
    q = list(start)
    for _ in range(limit):
        ok, goal, why = track.step(u=None, v=None, current_q=q, lower=lower, upper=upper)
        if not ok:
            raise AssertionError(f'去中心失败：{why}')
        q = list(goal)
        if track.centered:
            return q
    raise AssertionError('去中心没有在限定步数内完成')


class OrientationTests(unittest.TestCase):
    def test_camera_pose_uses_actual_mounted_tcp_rotation(self):
        from backend.core.camera_frame import camera_front_rpy
        from backend.core.kinematics import _mat_mul
        for rotation in ([0,0,90],[0,0,0],[12,-8,37]):
            tool={'kind':'vacuum-reference','rotation':rotation}
            model,_,_=build(tool)
            pose=camera_front_rpy(model,[-1,0,0])
            tcp=rpy_to_mat(*map(math.radians,pose))
            mounted=rpy_to_mat(*map(math.radians,model.tcp_rpy_deg))
            flange=[[sum(tcp[i][k]*mounted[j][k] for k in range(3)) for j in range(3)] for i in range(3)]
            self.assertAlmostEqual(flange[0][2],-1.,places=8)
            self.assertAlmostEqual(-flange[2][0],1.,places=8)

    def test_camera_forward_solver_rejects_expired_cancelled_and_invalid_data(self):
        from backend.core.camera_frame import solve_camera_forward_step,solve_camera_forward_center
        import time
        model,lower,upper=build()
        q=[0.]*6
        self.assertIsNone(solve_camera_forward_step(model,q,[-500,0,700],lower,upper,[-1,0,0],deadline=time.monotonic()-1)['q'])
        self.assertIsNone(solve_camera_forward_step(model,q,[-500,0,700],lower,upper,[-1,0,0],cancelled=lambda:True)['q'])
        self.assertIsNone(solve_camera_forward_step(model,q,[float('nan'),0,700],lower,upper,[-1,0,0])['q'])
        self.assertIsNone(solve_camera_forward_center(model,q,[-500,0,700],lower,upper,[-1,0,0],cancelled=lambda:True)['q'])

    def test_follow_saved_ranges_keep_full_upright_pose_while_translating(self):
        from backend.core.hand_follow import HandFollow
        from backend.core.camera_frame import solve_camera_forward_step
        import time
        for center,size in (([-600,0,650],[700,150,350]),([-500,0,700],[900,300,500])):
            model,lower,upper=build()
            from backend.core import config
            lower,upper=map(list,zip(*config.MANUAL_LIMITS))
            follow=HandFollow(model)
            enclosure={'min':[v-s/2 for v,s in zip(center,size)],'max':[v+s/2 for v,s in zip(center,size)]}
            result=follow.configure(dict(session='camera-pose-test',aspect=4/3,depth=False,rotation=False,
                direction=270,enclosure=enclosure),lower=lower,upper=upper,now=10.)
            self.assertTrue(result['ok'],result.get('why'))
            self.assertEqual(follow.snapshot()['bounds'],enclosure)
            q=follow._center_q[:]
            axis,up=camera_axes(model,q)
            self.assertGreater(up[2],.99);self.assertLess(axis[0],-.995);self.assertEqual(q[5],0.)
            for dy,dz in ((200.,0.),(-200.,0.),(0.,120.),(0.,-120.),(0.,0.)):
                target=[center[0],dy,center[2]+dz]
                for _ in range(100):
                    if math.dist(model.pose_vec(q)[:3],target)<.05:break
                    solved=solve_camera_forward_step(model,q,target,lower,upper,[-1.,0.,0.],deadline=time.monotonic()+.1)
                    self.assertIsNotNone(solved['q'],solved)
                    next_q=solved['q'];axis,up=camera_axes(model,next_q)
                    self.assertLessEqual(max(abs(a-b) for a,b in zip(q,next_q)),8.+1e-8)
                    self.assertLessEqual(math.dist(model.pose_vec(q)[:3],model.pose_vec(next_q)[:3]),24.+1e-8)
                    self.assertLess(axis[0],-math.cos(math.radians(.5)));self.assertGreater(up[2],math.cos(math.radians(.5)))
                    for fraction in (.25,.5,.75):
                        intermediate=[a+(b-a)*fraction for a,b in zip(q,next_q)]
                        optical,camera_up=camera_axes(model,intermediate)
                        self.assertLess(optical[0],-math.cos(math.radians(.5)))
                        self.assertGreater(camera_up[2],math.cos(math.radians(.5)))
                    q=next_q
                self.assertLess(math.dist(model.pose_vec(q)[:3],target),.05)

    def test_camera_faces_front_not_floor_or_ceiling(self):
        """末端镜头水平朝正面（-X）：不朝地、不朝天、不朝左右。

        现场要的是"只看前面"：手术平台在前方，镜头朝前才看得见手。
        朝下会看地面、朝上会看天花板、跟着手转会让工具板翻来翻去。
        """
        model, lower, upper = build()
        for start in ([0.0] * 6, [0.0, -30.0, 60.0, 0.0, 40.0, 0.0], [-40.0, -30.0, 60.0, 0.0, 40.0, 0.0]):
            track = HandTrack(model)
            track.configure({'center': CENTER, 'size': SIZE, 'direction': DIRECTION}, current_q=start)
            q = drive_to_center(track, model, lower, upper, start)
            frame = axes(model, q)
            z, x = frame[2], frame[0]
            self.assertAlmostEqual(z[0], -1.0, places=1, msg=f'镜头没有水平朝正面：{z}')
            self.assertAlmostEqual(z[2], 0.0, places=1, msg=f'镜头有俯仰（看地/看天）：{z}')
            # 相机偏置在物理法兰 -X，不能用含安装Rz90的TCP X轴冒充。
            optical,camera_up=camera_axes(model,q)
            self.assertAlmostEqual(camera_up[2],1.0,places=1,msg=f'实际相机不在上方：{camera_up}')
            self.assertAlmostEqual(optical[0],-1.0,places=1)
            # 停在框中心后方，不贴着框中心（那是贴着手掌）。
            reached = model.pose_vec(list(q))[:3]
            self.assertGreater(reached[0], CENTER[0], '末端应退到框中心后方')
            self.assertAlmostEqual(reached[0] - CENTER[0], HandTrack.STANDOFF_MM, delta=45.0)

    def test_center_lands_behind_the_box_center(self):
        model, lower, upper = build()
        for start in ([0.0] * 6, [0.0, -30.0, 60.0, 0.0, 40.0, 0.0], [-40.0, -30.0, 60.0, 0.0, 40.0, 0.0]):
            track = HandTrack(model)
            track.configure({'center': CENTER, 'size': SIZE, 'direction': DIRECTION}, current_q=start)
            q = drive_to_center(track, model, lower, upper, start)
            reached = model.pose_vec(list(q))[:3]
            self.assertGreater(reached[0], CENTER[0], f'起点 {start} 末端没有退到框后方：{reached}')

    def test_orientation_is_identical_regardless_of_start_pose(self):
        """朝向只由框决定，不该跟着上一次停机的姿态漂。"""
        model, lower, upper = build()
        seen = []
        for start in ([0.0] * 6, [0.0, -30.0, 60.0, 0.0, 40.0, 0.0], [-40.0, -30.0, 60.0, 0.0, 40.0, 0.0]):
            track = HandTrack(model)
            track.configure({'center': CENTER, 'size': SIZE, 'direction': DIRECTION}, current_q=start)
            q = drive_to_center(track, model, lower, upper, start)
            seen.append(axes(model, q)[2])
        for z in seen[1:]:
            for a, b in zip(seen[0], z):
                self.assertAlmostEqual(a, b, places=1, msg=f'起始姿态不同导致朝向不同：{seen}')

    def test_look_direction_is_fixed_and_comes_from_the_box(self):
        """姿态由框决定、全程不变；同样的框，从哪个起始姿态进入都一致。

        原来这里是"镜头跟着手掌转"，那是错的方向：手掌一动姿态就翻，
        末端工具现场看起来在"转来转去"。跟随能稳是因为它只动位置。
        """
        model, lower, upper = build()
        looks = []
        for start in ([0.0] * 6, [0.0, -30.0, 60.0, 0.0, 40.0, 0.0], [-40.0, -30.0, 60.0, 0.0, 40.0, 0.0]):
            track = HandTrack(model)
            track.configure({'center': CENTER, 'size': SIZE, 'direction': DIRECTION}, current_q=start)
            q = drive_to_center(track, model, lower, upper, start)
            fixed = axes(model, q)[2]
            for u, v in ((.2, .3), (.8, .7), (.5, .5)):
                ok, goal, _ = track.step(u=u, v=v, depth=.5, current_q=q, lower=lower, upper=upper)
                self.assertTrue(ok)
                self.assertLess(_angle_between(fixed, axes(model, goal)[2]), 6.0,
                                msg=f'u={u} v={v} 姿态变了')
                q = list(goal)
            looks.append(fixed)
        for z in looks[1:]:
            self.assertLess(_angle_between(looks[0], z), 5.0, msg=f'同样的框得到不同朝向：{looks}')


class FollowTests(unittest.TestCase):
    def test_hand_moves_the_tool_to_the_matching_side(self):
        model, lower, upper = build()
        track = HandTrack(model)
        track.configure({'center': CENTER, 'size': SIZE, 'direction': DIRECTION}, current_q=[0.0] * 6)
        q = drive_to_center(track, model, lower, upper, [0.0] * 6)
        base = axes(model, q)[2]

        def move(u, v, depth=.5):
            ok, goal, _ = track.step(u=u, v=v, depth=depth, current_q=q, lower=lower, upper=upper)
            self.assertTrue(ok)
            pose = model.pose_vec(list(goal))
            # 姿态必须一动不动：手掌每动一下姿态就跟着翻，现场看就是
            # "末端工具转来转去"。跟随稳是因为它只动位置。
            self.assertLess(_angle_between(base, axes(model, goal)[2]), 6.0,
                            msg=f'u={u} v={v} 姿态被手掌带着转了')
            return pose[:3]

        center_pose = move(.5, .5)
        # 末端必须站在手掌映射点的正后方：Y/Z 和手掌重合，只差那一段退后。
        # 两边各算一套公式就会出现"场景里手掌在这、机器人跑去了框外"。
        for u, v, depth in ((.2, .5, .5), (.8, .5, .5), (.5, .2, .5), (.5, .8, .5)):
            pose = move(u, v, depth)
            hand = track._point_from_pixel(u, v, depth)
            self.assertAlmostEqual(pose[1], hand[1], delta=12.0, msg=f'u={u} 末端和手掌 Y 对不上')
            self.assertAlmostEqual(pose[2], hand[2], delta=12.0, msg=f'v={v} 末端和手掌 Z 对不上')
            self.assertAlmostEqual(pose[0] - hand[0], HandTrack.STANDOFF_MM, delta=15.0,
                                   msg='末端不在手掌后方')
            # 框外不许去：手掌永远在框内，末端退后也只能退到框内。
            wlo, whi = track._world_bounds()
            for axis, (lo, hi) in enumerate(zip(wlo, whi)):
                self.assertGreaterEqual(pose[axis], lo - 1.0, f'末端越出框下界：{pose}')
                self.assertLessEqual(pose[axis], hi + 1.0, f'末端越出框上界：{pose}')

    def test_unreachable_hand_stops_at_the_boundary_instead_of_freezing(self):
        model, lower, upper = build()
        # 手掌推到画面右上角：末端要同时到 Z+250、Y+450 —— 实测够不到。
        # 必须退到可达边界，而不是原地不动、也不是一步缩回中心。
        track = HandTrack(model)
        track.configure({'center': CENTER, 'size': SIZE, 'direction': DIRECTION}, current_q=[0.0] * 6)
        q = drive_to_center(track, model, lower, upper, [0.0] * 6)
        ok, goal, why = track.step(u=1.0, v=0.0, depth=.5, current_q=q, lower=lower, upper=upper)
        self.assertTrue(ok)
        reached = model.pose_vec(list(goal))[:3]
        self.assertGreater(reached[2], CENTER[2] + 100, f'没有朝手掌方向移动：{reached}')
        self.assertIn('边界', why)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        model, _, _ = build()
        self.model = model

    def test_rejects_non_positive_size(self):
        track = HandTrack(self.model)
        with self.assertRaises(ValueError):
            track.configure({'center': CENTER, 'size': [400, 0, 400]}, current_q=[0.0] * 6)

    def test_rejects_inverted_enclosure(self):
        track = HandTrack(self.model)
        with self.assertRaises(ValueError):
            track.configure({'enclosure': {'min': [0, 0, 0], 'max': [100, 100, -1]}}, current_q=[0.0] * 6)

    def test_inactive_track_refuses_to_step(self):
        track = HandTrack(self.model)
        ok, goal, why = track.step(u=.5, v=.5, current_q=[0.0] * 6,
                                   lower=self.model.lower, upper=self.model.upper)
        self.assertFalse(ok)
        self.assertIsNone(goal)
        self.assertTrue(why)


if __name__ == '__main__':
    unittest.main(verbosity=2)
