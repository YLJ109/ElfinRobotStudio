"""鼠标平面网格：真实模型/逆解，临时副本，无设备连接。"""
from _offline_guard import require_offline_copy
require_offline_copy()
import json
import math
import os
from pathlib import Path
import sys
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.core import config
from backend.core.hand_follow import HandFollow
from backend.core.kinematics import pose_rotation_distance
from backend.core.ik_reachability import reachability_bound
from backend.core.sim_tool import DEFAULT_TOOL, model_for


class PlaneGrid(unittest.TestCase):
    directions=[(270,(-600,0,650)),(0,(0,-600,650)),(90,(600,0,650)),(180,(0,600,650))]

    def points(self):
        points=[(u/4,v/4,.5) for v in range(5) for u in (range(5) if v%2==0 else range(4,-1,-1))]
        return points+[(.5,.5,.5),(0,0,.5),(1,1,.5),(0,1,.5),(1,0,.5),(.5,.5,.5)]

    def test_screenshot_plane_sweep_and_return(self):
        failures=[];rows=[];unreachable=[]
        # 四朝向沿用同尺寸跟随平面；正面就是用户截图的中心X=-600。
        for direction,center in self.directions:
            if os.environ.get('ELFIN_GRID_DIAG') and direction!=270:continue
            f=HandFollow(model_for('elfin5',DEFAULT_TOOL))
            low,high=map(list,zip(*config.MANUAL_LIMITS))
            result=f.configure(dict(session='grid',aspect=4/3,depth=True,rotation=False,
                direction=direction,depth_mode='gesture',enclosure={
                    'min':[v-h for v,h in zip(center,(350,75,175))],
                    'max':[v+h for v,h in zip(center,(350,75,175))]}),
                lower=low,upper=high,now=10.)
            self.assertTrue(result['ok'],result.get('why'))
            f.start(dict(token=f.token,session=f.session),current_q=f._center_q,now=10.)
            for u,v,opening in self.points():
                began=time.perf_counter();still=0;last_error=math.inf
                for tick in range(240):
                    now=f._clock_value+1/30
                    before=list(f.q)
                    reply=f.sample(dict(token=f.token,session=f.session,seq=f.seq+1,u=u,v=v,
                        hand_open=opening,palm_scale=.2,angle=0,age_ms=0),now=now)
                    self.assertTrue(reply['ok'],reply.get('why'))
                    f.tick(1/30,now=now,speed_pct=100)
                    self.assertTrue(f._valid_q(f.q));self.assertTrue(f._inside(f.actual))
                    self.assertLess(math.dist(f.actual[:3],f.model.pose_vec(f.q)[:3]),1e-6)
                    self.assertLess(pose_rotation_distance(f.actual,f.model.pose_vec(f.q)),1e-6)
                    self.assertTrue(all(math.isfinite(v) for v in f.actual))
                    for a,b,cap in zip(before,f.q,f.model.vmax):
                        self.assertLessEqual(abs(b-a),cap/30+1e-6)
                    error=math.dist(f.actual[:3],f._tracking_target(f.target)[:3])
                    if os.environ.get('ELFIN_GRID_TRACE') and tick%30==0:
                        print('GRID_TRACE',json.dumps(dict(u=u,v=v,depth=opening,tick=tick,
                            error=error,q=f.q,route=f._wrist_recovery_target,
                            detail=f._solver_detail),ensure_ascii=False),flush=True)
                    if error<1.:break
                    # 腕轴回正可以保持TCP不动；只有关节也没有进展才算卡住。
                    still=still+1 if abs(last_error-error)<1e-7 and max(abs(a-b) for a,b in zip(before,f.q))<1e-6 else 0
                    last_error=error
                    if still>=25:break
                row=dict(direction=direction,u=u,v=v,depth=opening,error_mm=round(error,4),ticks=tick+1,
                         wall_ms=round((time.perf_counter()-began)*1000,2),cause=f._solver_cause)
                rows.append(row)
                if error>=1.:
                    row['q']=[round(x,4) for x in f.q]
                    row['last_joint_step']=max(abs(a-b) for a,b in zip(before,f.q))
                    row['proof']=reachability_bound(f.model,f.target,position_only=True)
                    if os.environ.get('ELFIN_GRID_DIAG'):
                        from backend.core.position_servo import solve_position_step
                        diagnosed=solve_position_step(f.model,f.q,f._tracking_target(f.target)[:3],low,high,deadline=time.monotonic()+.8)
                        row['endpoint']={key:value for key,value in diagnosed.items() if key!='resume'}
                        print('GRID_FAILURE',json.dumps(row,ensure_ascii=False),flush=True)
                    # 有几何证明的不可达点不能伪装成IK失败或强行要求到达。
                    # 仍实际驱动到边界，下一点必须恢复，限速/限位/姿态照常验收。
                    (unreachable if row['proof'] else failures).append(row)
            # 鼠标移出必须回中，不能由失效输入维持运动。
            for tick in range(240):
                now=f._clock_value+1/30
                f.sample(dict(token=f.token,session=f.session,seq=f.seq+1,u=.5,v=.5,
                    palm_scale=.2,angle=0,age_ms=0,hand_present=False),now=now)
                f.tick(1/30,now=now,speed_pct=100)
                if math.dist(f.actual[:3],f._base[:3])<1.:break
            self.assertLess(math.dist(f.actual[:3],f._base[:3]),1.)
        print('PLANE_GRID',json.dumps(dict(points=len(rows),failures=failures,
            unreachable=len(unreachable),max_ms=max(r['wall_ms'] for r in rows)),ensure_ascii=False),flush=True)
        self.assertFalse(failures,failures)


if __name__=='__main__':unittest.main(verbosity=2)
