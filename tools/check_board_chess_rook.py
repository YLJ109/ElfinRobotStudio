"""Focused isolated actual execution of the reported dense-rank rook move."""
from _offline_guard import require_offline_copy
require_offline_copy()
import math
import unittest
from check_board_robot import BoardRobotCases, LAYOUTS, cell_center

class RookRegression(BoardRobotCases):
    async def test_reported_opening_executes_rook_beside_bishop(self):
        await self.begin('chess')
        for source,target in (([6,4],[4,4]),([0,1],[2,2]),([7,6],[5,5]),([0,0],[0,1])):
            print('ROOK_MOVE',source,target,flush=True)
            self.games.move('chess',{'from':source,'to':target},auto_reply=False)
            self.board.note('chess')
            while self.board.queue or self.board.current:
                result=await self.board.step('chess',self.board.group)
                if result.get('running'):
                    await self.pump(lambda:self.w.runner.state=='finished')
            slot=target[0]*8+target[1]
            placed=next(p for p in self.board._members() if p['task']['slot']==slot)
            self.assertLess(math.dist(placed['xyz'][:2],cell_center('chess',*target)),.5)
            self.assertIsNone(self.play.held)
            self.assertEqual(self.w.settings['sim_speed_pct'],100)
        self.assertEqual(len(self.games._games['chess'].history),4)
        self.assertEqual(next(p for p in self.board._members() if p['task']['slot']==1)['task']['code'].upper(),'R')

    async def test_gripper_orientation_is_internal_only_and_finite(self):
        from fastapi import HTTPException
        for yaw in (True,float('nan'),float('inf'),-1.,361.,90.):
            with self.subTest(yaw=yaw),self.assertRaises(HTTPException):
                await self.play.generate({'group':'forged','action':'board','tool_id':'robotiq-2f85','gripper_yaw':yaw})
        self.assertFalse(self.play.busy)
        self.assertIsNone(self.play.held)

    async def test_fixed_zero_yaw_rejects_real_neighbour_while_perpendicular_grasp_is_clear(self):
        import asyncio
        from backend.application.board_tools import probe_gripper_yaw
        self.games.new('chess',{'opponent':'human'})
        for source,target in (([6,4],[4,4]),([0,1],[2,2]),([7,6],[5,5])):
            self.games.move('chess',{'from':source,'to':target},auto_reply=False)
        self.board.ensure('chess',force=True)
        rook=next(p for p in self.board._members() if p['task']['slot']==0)
        bishop=next(p for p in self.board._members() if p['task']['slot']==2)
        place=[*cell_center('chess',0,1),rook['xyz'][2]]
        bad=await asyncio.to_thread(probe_gripper_yaw,self.w,rook,place,0.,budget_s=5.)
        self.assertFalse(bad['ok'],bad)
        self.assertIn('碰撞',bad['why'])
        self.assertIn(bishop['name'],bad['why'])
        print('ROOK_NEIGHBOUR_REJECT',bad['why'],flush=True)
        self.assertTrue(bishop['collide'])
        good=await asyncio.to_thread(probe_gripper_yaw,self.w,rook,place,90.,budget_s=5.)
        self.assertTrue(good['ok'],good)

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([
        RookRegression('test_gripper_orientation_is_internal_only_and_finite'),
        RookRegression('test_fixed_zero_yaw_rejects_real_neighbour_while_perpendicular_grasp_is_clear'),
        RookRegression('test_reported_opening_executes_rook_beside_bishop')]))
    raise SystemExit(not result.wasSuccessful())
