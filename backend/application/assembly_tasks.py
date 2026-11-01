"""Recipe/pixel layouts over the one RobotPlay executor and current vision evidence."""
import copy
import math
import secrets
import time
from ..core.objects import half_height
from ..core.play_vision import bind_observations, evidence_current, scene_bindings
from ..play_routes import fields, fail


def grid(payload,*,group=True):
    keys=('mode','group','columns','rows','cells') if group else ('mode','columns','rows','cells')
    fields(payload,(*keys,'perception'),keys)
    if payload.get('perception','camera') not in ('camera','scene'):raise ValueError('物料来源无效')
    if payload['mode'] not in ('kit','pattern'):raise ValueError('请选择配套装盒或图案拼摆')
    cols,rows=payload['columns'],payload['rows'];cells=payload['cells']
    if any(type(v) is not int or not 1<=v<=5 for v in (cols,rows)):raise ValueError('行列分别需要1～5格')
    if not isinstance(cells,list) or len(cells)!=cols*rows or any(v not in (None,'red','green','blue') for v in cells):raise ValueError('每格请选择红绿蓝或留空')
    if not any(cells):raise ValueError('至少选择一个目标格')
    return cols,rows,cells


def layout(payload, pieces, zone, evidence):
    cols,rows,cells=grid(payload)
    available={color:[] for color in ('red','green','blue')}
    for piece in pieces:
        if piece['task']['slot']>=0:continue
        detected=evidence['bindings'].get(piece['id'])
        if detected:
            expected={'box':'rectangle','cylinder':'circle'}.get(piece['kind'])
            if expected is None or detected.get('shape')!=expected:raise ValueError('识别形状与平顶工件不一致，不能确定抓取面')
            available[detected['color']].append(piece)
    sx=(zone['size'][0]-30)/cols;sy=(zone['size'][1]-30)/rows;output=[]
    for slot,color in enumerate(cells):
        if color is None:continue
        if not available[color]:raise ValueError('实际识别物料不足：'+{'red':'红色','green':'绿色','blue':'蓝色'}[color])
        piece=available[color].pop(0)
        width=2*piece['size'][0] if piece['kind']=='cylinder' else piece['size'][0]
        depth=2*piece['size'][0] if piece['kind']=='cylinder' else piece['size'][1]
        if piece['kind'] not in ('box','cylinder') or width+10>sx or depth+10>sy:raise ValueError('格位容量不足，请减少行列或改用较小的平顶工件')
        xyz=[zone['xyz'][0]+(slot%cols-(cols-1)/2)*sx,zone['xyz'][1]+(slot//cols-(rows-1)/2)*sy,zone['xyz'][2]+half_height(zone)+half_height(piece)]
        output.append((piece,xyz,slot,color))
    return output


class AssemblyTasks:
    def __init__(self,world,*,clock=time.monotonic):
        self.world=world;self.clock=clock;self.current=None;self.plan=None;self.phase='idle';self.message='';self.generation=0

    @property
    def play(self):return self.world.robot_play

    def setup(self,payload):
        _,_,cells=grid(payload,group=False)
        self.play.scene.guard.require_idle()
        counts={color:cells.count(color) for color in ('red','green','blue')}
        # Reuse the same transactional fixture builder. The public sorting
        # endpoint keeps its existing 0..9 contract; only a bounded recipe may
        # request more same-colour pieces, with a total cap of 25 here.
        result=self.play.scene.setup({'mode':'sorting','counts':counts},recipe=True)
        self.generation+=1;self.current=None;self.plan=None;self.phase='idle'
        self.message='物料已摆放'
        return {**self.state(),'group':result['group'],'objects':result['objects']}

    def _pieces(self,group):return self.play.scene.group(self.world.objects.list(),group,'sorting')

    def _observations(self,pieces):
        snap=self.play.vision_snapshot()
        return snap,bind_observations(snap,pieces,time.time(),criterion='shape')

    def _current(self,token):
        if not self.current or token!=self.current['preview_id']:fail('任务预览已变化，请重新预览',409)
        if self.world.settings.get('mode')!='sim' or self.world.real is not None:fail('当前任务仅限未连接真机的仿真',409)
        if self.clock()>self.current['expires']:fail('任务预览已过期，请重新识别预览',409)
        if self.current['evidence'].get('source')!='scene' and not evidence_current(self.current['evidence'],self.play.vision_snapshot()):fail('识别来源或标定已变化，请重新预览',409)
        if self.play.context(set())!=self.current['context']:fail('场景、工具或物料已变化，请重新预览',409)

    def preview(self,payload):
        fields(payload,('mode','group','columns','rows','cells','perception'),('mode','group','columns','rows','cells'))
        if self.world.settings.get('mode')!='sim' or self.world.real is not None:fail('当前任务仅限未连接真机的仿真',409)
        self.play.scene.guard.require_idle()
        if self.play.busy or self.play.active or self.play.held:fail('请先停止取放并处理持件',409)
        # Failed replacement invalidates the previous ticket too.
        self.generation+=1;self.current=None;self.plan=None;self.phase='idle'
        pieces,_,zone=self._pieces(payload.get('group'))
        evidence=scene_bindings(pieces) if payload.get('perception')=='scene' else self._observations(pieces)[1]
        values=layout(payload,pieces,zone,evidence)
        self.current={**copy.deepcopy(payload),'preview_id':secrets.token_hex(16),'values':values,'evidence':evidence,
                      'context':self.play.context(set()),'expires':self.clock()+180}
        self.phase='preview';self.message='格位已预览，尚未启动运动'
        return self.state()

    def _fresh(self):
        if self.current['evidence'].get('source')=='scene':
            pieces,_,_=self._pieces(self.current['group'])
            latest=scene_bindings(pieces)
            if latest!=self.current['evidence']:fail('场景物料已变化，请重新预览',409)
            return latest
        pieces,_,_=self._pieces(self.current['group']);_,latest=self._observations(pieces)
        old=self.current['evidence']
        if latest['source_key']!=old['source_key'] or latest['calibration']!=old['calibration']:fail('视觉来源或标定已变化，请重新预览',409)
        for piece,_,_,_ in self.current['values']:
            left=old['bindings'][piece['id']];right=latest['bindings'].get(piece['id'])
            if not right or left['color']!=right['color'] or left['shape']!=right['shape'] or math.dist(left['xyz_mm'],right['xyz_mm'])>2:fail('目标物料识别已变化，请重新预览',409)
        return latest

    async def inspect(self,payload):
        fields(payload,('preview_id',),('preview_id',));self._current(payload['preview_id'])
        if self.phase!='preview':fail('请先重新预览再预检',409)
        self._fresh();generation=self.generation;self.phase='checking';self.plan=None
        try:
            values=[(piece,xyz,slot) for piece,xyz,slot,_ in self.current['values']]
            result=await self.play.generate({'group':self.current['group'],'action':'handling','speed':self.world.settings['sim_speed_pct']},bound_targets=values)
            if generation!=self.generation or self.phase!='checking':
                self.play.plans.pop(result['plan_id'],None)
                fail('预检已取消',409)
            self._current(payload['preview_id'])
            self.play.plans[result['plan_id']]['vision_evidence']=copy.deepcopy(self.current['evidence'])
            self.play.plans[result['plan_id']]['action']=self.current['mode']
            result['action']=self.current['mode']
            self.plan=result;self.phase='checked';self.message='完整路径已预检，请确认执行'
            return self.state()
        except BaseException:
            if generation==self.generation:self.phase='preview';self.message='预检未通过，未启动运动'
            raise

    def run(self,payload):
        fields(payload,('preview_id','plan_id'),('preview_id','plan_id'));self._current(payload['preview_id'])
        if self.phase!='checked' or not self.plan or payload['plan_id']!=self.plan['plan_id']:fail('预检票据无效，请重新预检',409)
        self._fresh()  # retained display is never sufficient motion evidence
        result=self.play.run({'plan_id':self.plan['plan_id'],'source':self.plan['source']})
        if result.get('ok'):self.phase='running';self.message='正在按格位实际取放'
        else:self.phase='preview';self.plan=None;self.message=result.get('why','执行未启动')
        return {**self.state(),**({} if result.get('ok') else result)}

    def state(self):
        current=self.current;confirmed=[]
        if current:
            objects={v['id']:v for v in self.world.objects.list()}
            for piece,xyz,slot,_ in current['values']:
                actual=objects.get(piece['id'])
                if actual and actual.get('task',{}).get('slot')==slot and math.dist(actual['xyz'],xyz)<=.5:confirmed.append(piece['id'])
            if self.phase=='running' and not self.play.active:
                self.phase='completed' if self.world.runner.state=='finished' and len(confirmed)==len(current['values']) and not self.play.held else 'cancelled' if self.world.runner.state=='aborted' else 'failed'
                self.message='全部格位实际取放完成' if self.phase=='completed' else '已停止或未完成，保留实际工件与已确认格位'
        return {'ok':True,'phase':self.phase,'message':self.message,'preview_id':current['preview_id'] if current else None,
                'plan_id':self.plan['plan_id'] if self.plan else None,'program':self.plan['source'] if self.plan else '',
                **({k:copy.deepcopy(current[k]) for k in ('mode','group','columns','rows','cells')} if current else {}),
                'placements':[{'object_id':p['id'],'color':color,'slot':slot,'xyz':xyz,'confirmed':p['id'] in confirmed} for p,xyz,slot,color in current['values']] if current else [],
                'completed':len(confirmed),'total':len(current['values']) if current else 0,'held':self.play.held and self.play.held['id'],
                'planning':self.phase=='checking','progress':dict(getattr(self.play,'progress',{}))}

    async def cancel(self,payload):
        fields(payload,('preview_id',),('preview_id',))
        if not self.current or payload['preview_id']!=self.current['preview_id']:fail('旧任务不能取消当前任务',409)
        self.generation+=1
        if self.phase=='checking':self.play.cancel_planning()
        if self.plan:self.play.plans.pop(self.plan['plan_id'],None)
        if self.phase=='running' and self.play.active and self.plan and self.play.active['id']==self.plan['plan_id']:
            await self.world.stop_execution('取消格位取放任务')
        self.phase='cancelled';self.message='已取消，保留实际工件与已确认格位';return self.state()
