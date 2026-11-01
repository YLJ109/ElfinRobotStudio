"""Finite exact tray22 reach probes; no motion, no wider IK tolerances."""
from _offline_guard import require_offline_copy
require_offline_copy()
import copy
import json
import time
import unittest
import check_board_robot as fixtures
from backend.application.board_robot import tray_center
from backend.application.board_tools import contact_pose,grasp_geometry
from backend.core import sim_tool
from backend.core.planning import inspect_path

class Reach(unittest.IsolatedAsyncioTestCase):
    async def test_exact_home22_lift_candidates_actual_model_and_collision(self):
        case=fixtures.BoardRobotCases('test_build_all_five_real_collision_boards_and_matching_tools')
        await case.asyncSetUp()
        try:
            case.games.new('xiangqi',{'opponent':'human'});case.board.ensure('xiangqi',force=True)
            descriptor=sim_tool.normalize(case.w.settings['sim_tool'])
            piece=next(p for p in case.board._members() if p['task']['slot']==7)
            place=[*tray_center('xiangqi',22),163.5]
            model=copy.copy(case.w.motion_model());lo,hi=case.w.limits()
            closed=grasp_geometry(descriptor,piece)['closure']
            model.library_tool={**model.library_tool,'closure':closed}
            objects=[{**obj,'xyz':list(place)} if obj['id']==piece['id'] else obj for obj in case.w.objects.list()]
            rows=[]
            for lift in (70.,55.,50.,40.):
                target=contact_pose(descriptor,piece,place,yaw=90.);target[2]+=lift
                best=None
                for seed in ([226.6604,-65.587,0.,0.,113.776,-43.34], [180.,-45.,90.,0.,45.,0.], [0.,45.,-90.,0.,90.,0.]):
                    result=model.ik(seed,target,lo,hi,iters=250,max_restarts=4,deadline=time.monotonic()+2.)
                    if best is None or result[1]+2*result[2]<best[1]+2*best[2]:best=result
                    if result[3]:break
                report=inspect_path(model,best[0],best[0],case.w.settings,lo,hi,objects,contact_ids={piece['id']}) if best[3] else None
                row=dict(lift=lift,target=target,converged=best[3],mm=best[1],deg=best[2],q=best[0],
                    collision=report['issues'] if report else None)
                print('XIANGQI_LIFT',json.dumps(row,ensure_ascii=False),flush=True);rows.append(row)
            candidate=next(row for row in rows if row['lift']==55.)
            self.assertTrue(candidate['converged'],candidate)
            self.assertFalse(candidate['collision'],candidate)
        finally:
            await case.asyncTearDown();case.doCleanups()

if __name__=='__main__':unittest.main(verbosity=2)
