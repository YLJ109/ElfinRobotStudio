"""Bounded read-only tasks over the sole measured vision snapshot; never motion."""
import copy,hashlib,json,math,secrets,time,threading
from ..core.play_vision import camera_identity
from ..core.vision_workspace import COLORS
SHAPES=('circle','rectangle','triangle','irregular')
NAMES={'红色':'red','黄色':'yellow','绿色':'green','青色':'cyan','蓝色':'blue','紫色':'purple','白色':'white','黑色':'black','圆形':'circle','矩形':'rectangle','三角形':'triangle','不规则':'irregular'}
class VisionTaskError(ValueError):
 def __init__(self,message,status=409):super().__init__(message);self.status=status

def number(v):return type(v) in (int,float) and math.isfinite(v)
def reject(message):raise VisionTaskError(message,422)
class VisionTasks:
 def __init__(self,vision,clock=time.time):self.vision=vision;self.clock=clock;self.task=None;self.lock=threading.RLock()
 def _snapshot(self):
  lock=getattr(self.vision,'lock',self.lock)
  with lock:
   snap=self.vision.snapshot()
   if (snap.get('config') or {}).get('input')=='camera':snap['camera_identity']=(self.vision.cameras.snapshot().get('active') or {})
  return snap
 def _current(self):
  snap=self._snapshot();r=snap.get('result') or {};c=snap.get('config') or {};stamp=r.get('received_at') if r.get('source')=='remote' else r.get('captured_at');image=r.get('image') or {}
  if not c.get('enabled') or not c.get('detection_enabled') or snap.get('error'):raise VisionTaskError(snap.get('error') or '请开启检测并等待新帧')
  if r.get('source') not in ('virtual','camera','remote') or not camera_identity(snap):raise VisionTaskError('需要当前相机会话的识别结果')
  if not number(stamp) or not 0<=self.clock()-stamp<=1.5 or snap.get('stale'):raise VisionTaskError('识别结果已过期，请等待新帧')
  w,h=image.get('width'),image.get('height')
  if type(w)!=int or type(h)!=int or not 0<w<=4096 or not 0<h<=4096 or not isinstance(image.get('base64'),str) or len(image['base64'])>8*1024*1024:raise VisionTaskError('识别图像无效')
  context={k:snap.get(k) for k in ('instance_id','config_generation','config','calibration')};context.update(source_key=r.get('source_key'),identity=camera_identity(snap))
  key=hashlib.sha256(json.dumps(context,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
  frame={'source':r['source'],'source_key':r['source_key'],'seq':r.get('frame_seq'),'captured_at':stamp,'age_ms':max(0,(self.clock()-stamp)*1000),'width':w,'height':h}
  if r.get('source')=='remote':frame.update(captured_at=None,received_at=stamp,age_basis='workstation_receive',capture_age_available=False,valid_until=stamp+1.5)
  obs=[];ids=set()
  if len(r.get('objects',[]))>50:raise VisionTaskError('观测数量超限')
  for obj in r.get('objects',[]):
   if obj.get('status')!='observed':continue
   center=obj.get('center_px');corners=obj.get('corners');ident=obj.get('id')
   if not isinstance(ident,str) or ident in ids or obj.get('color') not in COLORS or obj.get('shape') not in SHAPES:raise VisionTaskError('观测标识或类别无效')
   if not isinstance(center,list) or len(center)!=2 or not all(number(x) for x in center) or not isinstance(corners,list) or len(corners)!=4 or any(not isinstance(p,list) or len(p)!=2 or not all(number(x) for x in p) for p in corners):raise VisionTaskError('观测坐标无效')
   ids.add(ident);obs.append({'id':ident,'name':ident,'color':obj['color'],'shape':obj['shape'],'center_px':center,'center_normalized':[center[0]/w,center[1]/h],'corners':corners,'mapped':copy.deepcopy(obj.get('mapped'))})
  return snap,key,frame,obs
 def _selector(self,value):
  if not isinstance(value,dict) or not value or set(value)-{'name','track_id','color','shape'}:reject('请选择识别名称、颜色或轮廓类别')
  v=copy.deepcopy(value);name=v.pop('name',None)
  if name:
   if not isinstance(name,str) or len(name)>40:reject('名称无效')
   if name.startswith('V') and name[1:].isdigit():v['track_id']=name
   else:
    rest=name
    for label,category in NAMES.items():
     if label in rest:v['color' if category in COLORS else 'shape']=category;rest=rest.replace(label,'')
    if rest.strip():reject('当前检测不支持此名称，请选择已观测目标或颜色与轮廓')
  if not v or ('color' in v and v['color'] not in COLORS) or ('shape' in v and v['shape'] not in SHAPES) or ('track_id' in v and (not isinstance(v['track_id'],str) or len(v['track_id'])>40)):reject('类别或跟踪标识无效')
  return v
 def _config(self,p):
  if not isinstance(p,dict) or set(p)-{'mode','context_id','roi','selector','expected_count','slots','tolerance_mm'}:reject('任务字段无效')
  mode=p.get('mode');roi=p.get('roi',[0,0,1,1])
  if mode not in ('find','inspection') or not isinstance(roi,list) or len(roi)!=4 or not all(number(v) for v in roi) or min(roi[:2])<0 or min(roi[2:])<=0 or roi[0]+roi[2]>1 or roi[1]+roi[3]>1:reject('任务或检测区域无效')
  c={'mode':mode,'roi':roi,'selector':self._selector(p.get('selector'))}
  if mode=='find':
   if set(p)&{'expected_count','slots','tolerance_mm'}:reject('找物不接受计数或格位参数')
   return c
  count=p.get('expected_count');slots=p.get('slots',[])
  if count is not None and (type(count)!=int or not 0<=count<=50):reject('预期数量须为0至50')
  if not isinstance(slots,list) or len(slots)>50 or (count is None and not slots):reject('请提供预期数量或格位')
  if slots:
   tol=p.get('tolerance_mm')
   if not number(tol) or not .1<=tol<=100:reject('格位容差须为0.1至100毫米')
   seen=set()
   for s in slots:
    if not isinstance(s,dict) or set(s)-{'id','xy_mm','color','shape'} or not isinstance(s.get('id'),str) or not 0<len(s['id'])<=40 or s['id'] in seen:reject('格位标识无效')
    xy=s.get('xy_mm')
    if not isinstance(xy,list) or len(xy)!=2 or not all(number(x) and abs(x)<=100000 for x in xy):reject('格位坐标无效')
    for key,allowed in [('color',COLORS),('shape',SHAPES)]:
     if key in s and s[key] not in allowed:reject('格位类别无效')
    seen.add(s['id'])
   if count is not None and count!=len(slots):reject('格位数与预期数量不一致')
   if any(math.dist(a['xy_mm'],b['xy_mm'])<=2*tol for i,a in enumerate(slots) for b in slots[i+1:]):reject('格位容差区域重叠')
   c.update(slots=copy.deepcopy(slots),tolerance_mm=tol);count=len(slots)
  elif 'tolerance_mm' in p:reject('未设置格位，不接受容差参数')
  c['expected_count']=count;return c
 def _measure(self,c,snap,key,frame,obs):
  x,y,w,h=c['roi'];iw,ih=frame['width'],frame['height'];selector=c['selector']
  matches=[o for o in obs if all(o.get(k if k!='track_id' else 'id')==v for k,v in selector.items()) and all(x<=p[0]/iw<=x+w and y<=p[1]/ih<=y+h for p in o['corners'])]
  r={'mode':c['mode'],'valid':True,'context_id':key,'frame':frame,'expires_at':frame.get('valid_until',0) if frame['source']=='remote' else frame['captured_at']+1.5,'clock_domain':'server_wall','matches':matches,'image':copy.deepcopy(snap['result']['image']),'read_only':True,'motion':False,'pick_available':False,'pick_reason':'尚未核验抓取面、姿态与工具资格','roi_rule':'目标轮廓框完全位于区域内'}
  if c['mode']=='find':r.update(selected=matches[0] if len(matches)==1 else None,status='found' if len(matches)==1 else 'ambiguous' if matches else 'not_found');return r
  expected=c['expected_count'];r.update(count=len(matches),expected_count=expected,missing_count=max(0,expected-len(matches)),extra_count=max(0,len(matches)-expected),slots=[],extras=[],misaligned_count=0,ambiguous_count=0)
  if c.get('slots'):
   cal=snap.get('calibration') or {};errors=cal.get('check_errors_mm')
   if not snap['result'].get('calibrated') or cal.get('source_key')!=frame['source_key'] or cal.get('image_size')!=[iw,ih] or not isinstance(errors,list) or len(errors)!=2 or any(not number(e) or not 0<=e<=10 for e in errors):raise VisionTaskError('格位检查需要当前来源的平面标定与独立复核')
   import numpy as np
   import cv2 as cv
   try:inverse=np.linalg.inv(np.asarray(cal['matrix'],dtype=float))
   except Exception:raise VisionTaskError('标定矩阵无效')
   for s in c['slots']:
    projected=inverse@np.array([*s['xy_mm'],1.]);px=projected[:2]/projected[2] if abs(projected[2])>1e-9 else [float('nan')]*2
    if not all(number(float(v)) for v in px) or not x<=px[0]/iw<=x+w or not y<=px[1]/ih<=y+h or cv.pointPolygonTest(np.asarray(cal.get('pixel_hull',[]),dtype=np.float32),tuple(map(float,px)),False)<0:raise VisionTaskError('预期格位超出已校准检测区域')
   for o in matches:
    mapped=o.get('mapped') or {};xy=mapped.get('xyz_mm')
    if mapped.get('coordinate_space')!='base_xy_planar_estimate' or not isinstance(xy,list) or len(xy)!=3 or not all(number(v) for v in xy):raise VisionTaskError('目标超出标定范围，不能判定缺失或错位')
   radius=max(50.,3*c['tolerance_mm']);candidates=[]
   for s in c['slots']:
    candidates.append([(math.dist(s['xy_mm'],o['mapped']['xyz_mm'][:2]),o) for o in matches if all(o.get(k)==s[k] for k in ('color','shape') if k in s) and math.dist(s['xy_mm'],o['mapped']['xyz_mm'][:2])<=radius])
   used=set()
   for s,poss in zip(c['slots'],candidates):
    entry={'id':s['id'],'xy_mm':s['xy_mm'],'status':'missing'}
    if poss:
     poss.sort(key=lambda t:t[0]);distance,o=poss[0]
     if len(poss)>1 or sum(any(z[1]['id']==o['id'] for z in group) for group in candidates)>1:entry['status']='ambiguous';r['ambiguous_count']+=1
     else:entry.update(status='ok' if distance<=c['tolerance_mm'] else 'misaligned',observed_id=o['id'],error_mm=distance);used.add(o['id']);r['misaligned_count']+=int(entry['status']=='misaligned')
    r['slots'].append(entry)
   r['extras']=[o['id'] for o in matches if o['id'] not in used];r['association_radius_mm']=radius
  r['passed']=len(matches)==expected and all(s['status']=='ok' for s in r['slots']) and not r['extras'];r['status']='passed' if r['passed'] else 'attention';return r
 def _invalidate(self,reason):
  if self.task:
   self.task['phase']='invalid';self.task['error']=reason
   if self.task.get('result'):self.task['result'].update(valid=False,selected=None)
 def state(self):
  with self.lock:
   try:
    snap,key,frame,obs=self._current();available=True;why=''
    if self.task and (self.task['context_id']!=key or self.clock()>self.task['created_at']+300):self._invalidate('来源、配置已变化或任务已过期')
    if self.task and self.task.get('result') and self.clock()>self.task['result']['expires_at']:self._invalidate('测量结果已过期，请重新检查')
   except VisionTaskError as e:key=None;frame=None;obs=[];available=False;why=str(e);self._invalidate(why)
   return {'ok':True,'capabilities':{'colors':list(COLORS),'shapes':list(SHAPES),'read_only':True,'motion':False},'available':available,'why':why,'context_id':key,'frame':frame,'observations':obs,'task':copy.deepcopy(self.task)}
 def preview(self,p):
  with self.lock:
   c=self._config(p);snap,key,frame,obs=self._current()
   if p.get('context_id')!=key:raise VisionTaskError('来源或配置已变化，请重新选择目标')
   result=self._measure(c,snap,key,frame,obs);self.task={'mode':c['mode'],'context_id':key,'preview_id':secrets.token_urlsafe(24),'plan_id':None,'phase':'preview','config':c,'result':result,'error':None,'created_at':self.clock()};return self.state()
 def _advance(self,p,token,phase):
  with self.lock:
   if not isinstance(p,dict) or set(p)!={token}:reject('任务凭据字段无效')
   t=self.task
   if not t or not isinstance(p[token],str) or not secrets.compare_digest(p[token],t.get(token) or '') or t['phase'] not in (('preview','checked') if token=='preview_id' else ('checked',)):raise VisionTaskError('任务已失效，请重新预览')
   try:
    snap,key,frame,obs=self._current()
    if key!=t['context_id'] or self.clock()>t['created_at']+300:raise VisionTaskError('来源或配置已变化，请重新预览')
    result=self._measure(t['config'],snap,key,frame,obs)
    if t['mode']=='find' and result['status']!='found':raise VisionTaskError('目标缺失或存在多个匹配，请重新选择')
   except VisionTaskError as e:self._invalidate(str(e));raise
   t.update(result=result,phase=phase,error=None)
   if phase=='checked':t['plan_id']=secrets.token_urlsafe(24)
   return self.state()
 def inspect(self,p):return self._advance(p,'preview_id','checked')
 def run(self,p):return self._advance(p,'plan_id','completed')
 def cancel(self,p):
  with self.lock:
   if not isinstance(p,dict) or len(p)!=1 or next(iter(p)) not in ('preview_id','plan_id'):reject('取消凭据无效')
   k=next(iter(p))
   if not self.task or not isinstance(p[k],str) or not secrets.compare_digest(p[k],self.task.get(k) or ''):raise VisionTaskError('任务已失效')
   self.task.update(phase='cancelled',result=None,preview_id=None,plan_id=None);return self.state()
