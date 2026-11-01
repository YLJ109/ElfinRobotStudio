"""Course setup and evidence-based scoring around World's single RobotPlay."""
import copy
import math
import secrets
import time
import uuid
from ..domain.handling_challenge import LEVELS, level_by_id, at_goal, score
from ..core.objects import half_height
from ..core.planning import digest
from ..play_routes import homes, fail, fields


class HandlingChallenge:
    def __init__(self,world,*,clock=time.monotonic):
        self.world=world;self.clock=clock;self.challenge=None;self.plan=None
        self.phase='idle';self.message='';self.confirmed=set();self.started=None;self.ended=None
        self.results=[];self.generation=0
        world.robot_play.event_observer=self.event

    @property
    def play(self):return self.world.robot_play

    def setup(self,payload):
        fields(payload,('level',),('level',));level=level_by_id(payload['level'])
        self.play.scene.guard.require_idle()
        if self.play.busy or self.play.active or self.play.held:fail('请先停止当前取放并处理持件',409)
        group=uuid.uuid4().hex;goals={}
        def create(items):
            items[:]=[item for item in items if not item.get('task')]
            def add(role,name,xyz,size,*,collide=False,home=0):
                item={'id':uuid.uuid4().hex,'name':name,'kind':'box','preset':'box','size':size,'xyz':xyz,
                      'color':'#e0a33b' if role=='obstacle' else '#4fd8cf','surface':'painted',
                      'gravity':False,'floating':True,'collide':collide,'locked':role!='piece',
                      'task':{'group':group,'mode':'handling','role':role,'symbol':'','slot':-1,'home':home}}
                if role in ('zone_a','zone_b'):item['preset']=role
                items.append(item);return item
            a=add('zone_a','闯关 A 取料区',[450,-350,2],[330,330,4])
            b=add('zone_b','闯关 B 目标区',[450,350,2],[330,330,4])
            for index,((x,y),(gx,gy)) in enumerate(zip(homes(level['pieces'],a),homes(level['pieces'],b))):
                piece=add('piece',f'闯关工件{index+1}',[x,y,16],[72,72,24],collide=True,home=index)
                goals[piece['id']]={'xyz':[gx,gy,16],'slot':index}
            for index,obstacle in enumerate(level['obstacles']):
                add('obstacle',f'闯关固定障碍{index+1}',obstacle['xyz'],obstacle['size'],collide=True)
            return {'group':group}
        self.play.scene.transaction(create)
        self.generation+=1;self.challenge={'id':secrets.token_hex(16),'group':group,'level':level['id'],
                                        'goals':goals,'scene':digest({'objects':self.world.objects.list(),'context':self.play.context(set(goals))})}
        self.confirmed=set();self.plan=None;self.phase='ready';self.started=self.ended=None;self.message='关卡已布置，尚未启动机器人'
        return self.state()

    def state(self):
        challenge=self.challenge
        total=len(challenge['goals']) if challenge else 0
        elapsed=0. if self.started is None else max(0.,(self.ended if self.ended is not None else self.clock())-self.started)
        return {'ok':True,'phase':self.phase,'message':self.message,'group':challenge['group'] if challenge else None,
                'challenge_id':challenge['id'] if challenge else None,
                'level':challenge['level'] if challenge else None,'total':total,'completed':len(self.confirmed),
                'elapsed_s':round(elapsed,2),'score':score(len(self.confirmed),total,self.phase=='completed'),
                'held':self.play.held and self.play.held['id'],'levels':[{'id':l['id'],'name':l['name'],'pieces':l['pieces'],'obstacles':len(l['obstacles'])} for l in LEVELS],
                'plan_id':self.plan['plan_id'] if self.plan else None,'run_id':self.plan['plan_id'] if self.plan and self.started is not None else None,'results':copy.deepcopy(self.results[-10:]),
                'progress':dict(getattr(self.play,'progress',{})),'planning':self.play.busy,
                'goals':copy.deepcopy(challenge['goals']) if challenge else {}}

    def _current(self):
        if not self.challenge:fail('请先选择并布置关卡',409)
        if self.world.settings.get('mode')!='sim' or self.world.real is not None:fail('搬运闯关仅限未连接真机的仿真',409)
        if self.challenge['scene']!=digest({'objects':self.world.objects.list(),'context':self.play.context(set(self.challenge['goals']))}):fail('关卡、工具或障碍已变化，请重新布置',409)

    async def inspect(self,payload):
        fields(payload,('challenge_id',),('challenge_id',))
        self._current()
        if payload['challenge_id']!=self.challenge['id']:fail('旧关卡请求已失效',409)
        if self.phase!='ready':fail('请重新布置关卡后预检',409)
        self.phase='checking';self.plan=None;generation=self.generation
        rows={item['id']:item for item in self.world.objects.list()};values=[]
        for oid,goal in self.challenge['goals'].items():
            if oid not in rows or rows[oid]['task']['slot']!=-1:fail('工件已移动，需重新布置',409)
            values.append((rows[oid],goal['xyz'],goal['slot']))
        try:
            result=await self.play.generate({'group':self.challenge['group'],'action':'handling',
                'clearance':level_by_id(self.challenge['level'])['clearance'],'speed':self.world.settings['sim_speed_pct']},bound_targets=values)
            if generation!=self.generation or self.phase!='checking':fail('本次闯关预检已取消',409)
            self._current()
            if not result.get('ok'):raise ValueError(result.get('why','路径未通过'))
            self.play.plans[result['plan_id']]['challenge_id']=self.challenge['id']
            self.plan=result;self.phase='checked';self.message='整条取放路径已预检，请确认执行'
            return {**self.state(),'program':result['source']}
        except BaseException:
            if self.phase=='checking':self.phase='ready';self.message='预检未通过，未启动运动'
            raise

    def run(self,payload):
        fields(payload,('challenge_id','plan_id'),('challenge_id','plan_id'))
        self._current()
        if self.phase!='checked' or not self.plan:fail('请先完成当前关卡预检',409)
        if payload['challenge_id']!=self.challenge['id'] or payload['plan_id']!=self.plan['plan_id']:fail('关卡或预检票据已变化，拒绝旧执行请求',409)
        try:
            result=self.play.run({'plan_id':self.plan['plan_id'],'source':self.plan['source']})
        except BaseException:
            self.phase='ready';self.plan=None;self.message='执行请求未接受，请重新预检'
            raise
        if not result.get('ok'):
            self.phase='ready';self.plan=None;self.message=result.get('why','执行未启动，请重新预检')
            return result
        self.phase='running';self.started=self.clock();self.ended=None;self.message='机器人正在执行本关取放'
        return self.state()

    def event(self,event,plan,**values):
        if not self.challenge or not plan or plan.get('challenge_id')!=self.challenge['id'] or self.phase!='running':return
        if event=='placed':
            oid=values.get('object_id');goal=self.challenge['goals'].get(oid)
            if not goal:return
            item=next((item for item in self.world.objects.list() if item['id']==oid),None)
            if at_goal(item,goal['xyz'],self.challenge['group'],goal['slot']):self.confirmed.add(oid)
        elif event=='terminal':
            rows={item['id']:item for item in self.world.objects.list()}
            self.confirmed={oid for oid in self.confirmed if at_goal(rows.get(oid),self.challenge['goals'][oid]['xyz'],self.challenge['group'],self.challenge['goals'][oid]['slot'])}
            successful=values.get('state')=='finished' and len(self.confirmed)==len(self.challenge['goals']) and not self.play.held
            self.phase='completed' if successful else 'cancelled' if values.get('state') in ('aborted','cancelled') else 'failed'
            self.ended=self.clock()  # same event-loop terminal observation, excludes preflight
            self.message='本关全部实际取放已完成' if successful else '已停止或未完成，保留当前工件与部分成绩'
            value=self.state();self.results.append({key:value[key] for key in ('run_id','level','phase','completed','total','elapsed_s','score')})
            self.results=self.results[-10:]

    async def cancel(self):
        self.generation+=1
        if self.plan:self.play.plans.pop(self.plan['plan_id'],None)
        if self.phase=='checking':
            self.play.cancel_planning();self.phase='cancelled';self.message='已撤销本次预检，等待线程退出'
        elif self.phase=='running' and self.play.active and self.play.active.get('challenge_id')==self.challenge['id']:
            await self.world.stop_execution('取消搬运闯关')
        elif self.phase in ('ready','checked'):
            self.phase='cancelled';self.plan=None;self.message='本关已取消，未启动运动'
        return self.state()
