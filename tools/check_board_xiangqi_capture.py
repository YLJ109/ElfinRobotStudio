"""Exact reported Xiangqi two-capture course in a disposable World only."""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio
import json
import copy
import time
import unittest
from unittest.mock import patch
import check_board_robot as fixtures
from backend.core.kinematics import RobotModel

class XiangqiCapture(unittest.IsolatedAsyncioTestCase):
    async def test_reported_two_captures_keep_actual_piece_and_path_in_sync(self):
        case=fixtures.BoardRobotCases('test_build_all_five_real_collision_boards_and_matching_tools')
        await case.asyncSetUp()
        original=RobotModel.ik;failures=[]
        def trace(model,q,target,*args,**kwargs):
            answer=original(model,q,target,*args,**kwargs)
            if not answer[3] and kwargs.get('restarts',True):
                failures.append((copy.copy(model),list(q),list(target),answer))
                print('XIANGQI_IK_FAILED',json.dumps(dict(start=q,target=target,result=answer,
                    remaining=(kwargs['deadline']-time.monotonic()) if kwargs.get('deadline') else None),default=str),flush=True)
            return answer
        tracing=patch.object(RobotModel,'ik',trace);tracing.start()
        try:
            await case.begin('xiangqi')
            # The screenshot's capture slot is home22 (-502.5,-532.5),
            # while a fresh ordered scene assigns this horse home7. Home is
            # scene identity, so exchange the two IDs without moving geometry.
            objects=case.w.objects.list()
            horse=next(p for p in objects if p.get('task',{}).get('slot')==7 and p['task'].get('role')=='piece')
            other=next(p for p in objects if p.get('task',{}).get('home')==22 and p['task'].get('role')=='piece')
            horse['task']['home'],other['task']['home']=other['task']['home'],horse['task']['home']
            case.w.objects.replace(objects,validate_all=False)
            moves=[{'from':[6,4],'to':[5,4]}, {'from':[2,1],'to':[9,1]}, {'from':[7,7],'to':[0,7]}]
            for index,move in enumerate(moves):
                print('XIANGQI_MOVE',index,move,flush=True)
                case.games.move('xiangqi',move,auto_reply=False);case.board.note('xiangqi')
                while case.board.queue or case.board.current:
                    result=await case.board.step('xiangqi',case.board.group)
                    print('XIANGQI_ACTION',json.dumps({k:v for k,v in result.items() if k!='state'},ensure_ascii=False,default=str),flush=True)
                    if result.get('running'):
                        try:await case.pump(lambda:case.w.runner.state=='finished')
                        except Exception:
                            print('XIANGQI_FAILURE',json.dumps(dict(q=case.w.current_joints(),runner=case.w.runner.snapshot(),
                                source=case.w.runner.source if hasattr(case.w.runner,'source') else None),ensure_ascii=False,default=str),flush=True)
                            raise
                actual=case.board.actual_state()
                self.assertFalse(actual.get('physical_pending',False))
                print('XIANGQI_COMPLETED',index,flush=True)
            self.assertEqual(len(case.games._games['xiangqi'].history),3)
            captured=[p for p in case.board._members() if p['task']['slot']<0]
            self.assertEqual(len(captured),2)
        finally:
            tracing.stop()
            if failures:
                model,start,target,answer=failures[-1]
                for seed in (start,answer[0],[0.,-45.,90.,0.,45.,0.],[0.,45.,-90.,0.,90.,0.]):
                    result=original(model,seed,target,*case.w.limits(),iters=400,max_restarts=8,deadline=time.monotonic()+3.)
                    print('XIANGQI_INDEPENDENT_ENDPOINT',json.dumps(dict(target=target,seed=seed,result=result),default=str),flush=True)
            await case.asyncTearDown();case.doCleanups()

if __name__=='__main__':unittest.main(verbosity=2)
