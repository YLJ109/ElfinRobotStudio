"""Actual placement score projection; never owns motion, scene or attachment."""
import copy
import math
import time

class SortingScore:
    def __init__(self,*,clock=time.monotonic):
        self.clock=clock;self.value=None;self.goals={};self.placements={};self.started=None
    def reset(self,group):
        if self.value and self.value['group']==group:
            self.value=None;self.goals={};self.placements={};self.started=None

    def begin(self,plan):
        self.goals=copy.deepcopy(plan['bindings']);self.placements={};self.started=self.clock()
        self.value=dict(run_id=plan['id'],group=plan['group'],criterion=plan['criterion'],status='running',
                        total=len(self.goals),completed=0,correct=0,accuracy_pct=None,elapsed_s=0.,score=0,reason='')
    def placed(self,run_id,oid,xyz,slot):
        if not self.value or self.value['status']!='running' or self.value['run_id']!=run_id or oid not in self.goals:return
        goal=self.goals[oid]
        # Repeated notifications never inflate counters; latest actual commit wins.
        self.placements[oid]=slot==goal['slot'] and math.dist(xyz,goal['xyz'])<=.5
    def finish(self,run_id,state,all_at_goals):
        if not self.value or self.value['status']!='running' or self.value['run_id']!=run_id:return
        correct=sum(self.placements.values());total=len(self.goals)
        success=state=='finished' and all_at_goals and correct==total and total>0
        self.value.update(status='completed' if success else 'cancelled' if state in ('aborted','cancelled') else 'failed',
                          elapsed_s=round(max(0,self.clock()-self.started),3),
                          reason='' if success else '保留实际完成结果，未完成全部分拣')
    def snapshot(self):
        if self.value is None:return None
        value=dict(self.value);done=len(self.placements);correct=sum(self.placements.values())
        value.update(completed=done,correct=correct,accuracy_pct=round(100*correct/done,1) if done else None,
                     score=100 if value['status']=='completed' else int(99*correct/value['total']) if value['total'] else 0)
        if value['status']=='running':value['elapsed_s']=round(max(0,self.clock()-self.started),3)
        return value
