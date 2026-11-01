"""Diagnostic: inclusive CPU stages of actual three-piece scene planning; no motion."""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio
import json
import time
from unittest.mock import patch
from backend.core.world import World
from backend.core.kinematics import RobotModel
from backend import robot_play_routes as routes
from backend.core import planning


async def main():
    world=World()
    world.robot_play=routes.RobotPlay(world)
    group=world.robot_play.scene.setup({'mode':'sorting','counts':{'red':1,'green':1,'blue':1}})['group']
    before=list(world.sim.q);cost={}
    def measured(name,fn):
        def wrapped(*args,**kwargs):
            start=time.perf_counter()
            try:return fn(*args,**kwargs)
            finally:
                row=cost.setdefault(name,{'calls':0,'seconds':0})
                row['calls']+=1;row['seconds']+=time.perf_counter()-start
        return wrapped
    began=time.perf_counter()
    with patch.object(RobotModel,'ik',measured('ik',RobotModel.ik)), \
         patch.object(planning,'inspect_path',measured('cartesian_curve_check',planning.inspect_path)), \
         patch.object(routes,'inspect_path',measured('initial_joint_check',routes.inspect_path)), \
         patch.object(routes,'carried_clearance',measured('carried_clearance',routes.carried_clearance)):
        result=await world.robot_play.generate({'group':group,'action':'sorting','perception':'scene','speed':100})
    assert result['ok'] and result['cycles']==3
    assert list(world.sim.q)==before and world.robot_play.active is None
    print('PREFLIGHT_PROFILE '+json.dumps({'elapsed_s':time.perf_counter()-began,'inclusive_stages':cost}),flush=True)


if __name__=='__main__':asyncio.run(main())
