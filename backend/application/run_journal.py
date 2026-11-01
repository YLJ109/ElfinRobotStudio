"""Bounded read-only execution evidence. Observes Runner, never replays commands."""
import asyncio
import copy
import hashlib
import json
import math
import re
import threading
import time
from pathlib import Path
from ..core.storage import write_json

MAX_RECORDS=64
MAX_BYTES=2*1024*1024
ACTIVE={'running','waiting','paused'}
TERMINAL={'finished','aborted','error','interrupted','replaced'}


def safe_reason(value):
    value=str(value or '')[:2000]
    value=re.sub(r'(?i)([\w-]*(?:api[_-]?key|token|password|secret)|authorization)\s*[:=]\s*[\"\']?[^\s,;\"\'}]+',r'\1=[已隐藏]',value)
    value=re.sub(r'(?i)Bearer\s+\S+','Bearer [已隐藏]',value)
    value=re.sub(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])','[地址已隐藏]',value)
    return ''.join(c for c in value if ord(c)>=32)[:300]


def _hash(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()


def world_context(world):
    play=getattr(world,'robot_play',None);plan=getattr(play,'active',None) or {}
    spec=world.runner.script_spec
    source=spec.get('source','') if spec else '\n'.join(step.get('src','') for step in world.runner.steps)
    context=dict(session_id=world.session_id,mode=world.settings.get('mode'),config_revision=world._cfg_rev,
                 scene_revision=_hash(world.objects.list()),program_hash=_hash(source),
                 tool_revision=_hash(world.tools.list()),
                 executor_state=world.executor.state)
    for name in ('id','group','action','challenge_id'):
        if plan.get(name) is not None:context[{'id':'plan_id'}.get(name,name)]=plan[name]
    return context


class RunJournal:
    def __init__(self,runner,*,path,context,clock=time.monotonic):
        self.runner,self.path,self.context,self.clock=runner,Path(path),context,clock
        self.lock=threading.RLock();self.condition=threading.Condition(self.lock)
        self.records=[];self.dirty=False;self.stopping=False;self.thread=None
        self.error=None;self.writable=True;self._started={}
        self._load()
        if runner.event_sink is not None:raise ValueError('执行记录观察者已绑定')
        runner.event_sink=self.observe

    def _load(self):
        if not self.path.exists():return
        try:
            if self.path.stat().st_size>MAX_BYTES:raise ValueError()
            raw=json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(raw,dict) or set(raw)!={'version','runs'} or raw['version']!=1 or not isinstance(raw['runs'],list) or len(raw['runs'])>MAX_RECORDS:raise ValueError()
            seen=set()
            for row in raw['runs']:
                if not isinstance(row,dict) or not re.fullmatch('[a-f0-9]{32}',row.get('run_id','')) or row['run_id'] in seen:raise ValueError()
                seen.add(row['run_id'])
                allowed={'run_id','phase','context','started_at','updated_at','ended_at','elapsed_s','confirmed_steps','last_confirmed','current_step','reason','partial','analysis_only'}
                if set(row)!=allowed or row['phase'] not in ACTIVE|TERMINAL or row['analysis_only'] is not True:raise ValueError()
                if (not isinstance(row['context'],dict) or set(row['context'])-{'session_id','mode','config_revision','scene_revision','program_hash','tool_revision','plan_id','group','action','challenge_id','executor_state'}
                        or type(row['confirmed_steps']) is not int or not 0<=row['confirmed_steps']<=100000
                        or type(row['partial']) is not bool or not isinstance(row['reason'],str)
                        or safe_reason(row['reason'])!=row['reason']):raise ValueError()
                for key in ('started_at','updated_at','elapsed_s'):
                    if type(row[key]) not in (int,float) or not math.isfinite(row[key]) or row[key]<0:raise ValueError()
                if row['ended_at'] is not None and (type(row['ended_at']) not in (int,float) or not math.isfinite(row['ended_at']) or row['ended_at']<0):raise ValueError()
                for key,value in row['context'].items():
                    if key=='config_revision':
                        if type(value) is not int or value<0:raise ValueError()
                    elif not isinstance(value,str) or len(value)>128 or safe_reason(value)!=value:raise ValueError()
                for key in ('last_confirmed','current_step'):
                    step=row[key]
                    if step is not None and (not isinstance(step,dict) or set(step)!={'index','line','kind'}
                            or type(step['index']) is not int or not 0<=step['index']<=100000
                            or step['line'] is not None and (type(step['line']) is not int or not 1<=step['line']<=1000000)
                            or not isinstance(step['kind'],str) or not re.fullmatch('[a-z_]{1,32}',step['kind'])):raise ValueError()
                if row['phase'] in ACTIVE:
                    row.update(phase='interrupted',reason='上次服务结束前未确认终态；仅供离线分析',ended_at=row['updated_at'],partial=row['confirmed_steps']>0)
                    self.dirty=True
                self.records.append(row)
        except (ValueError,TypeError,OSError,KeyError):
            self.records=[];self.writable=False;self.error='运行记录损坏，已保留原文件；未覆盖或重放'

    def _step(self,runner,index):
        if type(index) is not int or not 0<=index<len(runner.steps):return None
        step=runner.steps[index]
        return dict(index=index,line=step.get('line') if type(step.get('line')) is int else None,
                    kind=str(step.get('kind','unknown'))[:32])

    def _context(self):
        raw=self.context();out={}
        for key in ('session_id','mode','config_revision','scene_revision','program_hash','tool_revision','plan_id','group','action','challenge_id','executor_state'):
            value=raw.get(key)
            if key=='config_revision':
                if type(value) is int and value>=0:out[key]=value
            elif isinstance(value,str) and len(value)<=128:out[key]=safe_reason(value)
        return out

    def observe(self,event,runner,**data):
        try:self._observe(event,runner,**data)
        except Exception as exc:
            with self.lock:self.error='运行记录采集失败：'+type(exc).__name__

    def _observe(self,event,runner,**data):
        run_id=runner.run_id
        if not isinstance(run_id,str) or not re.fullmatch('[a-f0-9]{32}',run_id):return
        now=self.clock();wall=time.time()
        with self.condition:
            row=next((r for r in self.records if r['run_id']==run_id),None)
            if event=='started':
                if row is not None:return
                for previous in self.records:
                    if previous['phase'] in ACTIVE:previous.update(phase='interrupted',reason='新的执行已开始，上次未确认终态',ended_at=wall)
                context=self._context()
                row=dict(run_id=run_id,phase='running',context=context,started_at=wall,updated_at=wall,ended_at=None,
                         elapsed_s=0.,confirmed_steps=0,last_confirmed=None,current_step=None,reason='',partial=False,analysis_only=True)
                self.records.append(row);self.records=self.records[-MAX_RECORDS:]
                self._started={run_id:now}
            elif row is None or row['phase'] not in ACTIVE:return
            if event in ('dispatch','confirmed'):
                if runner.state in ACTIVE:row['phase']=runner.state
                step=self._step(runner,data.get('step_index'))
                if event=='confirmed' and step is not None:
                    row['last_confirmed']=step;row['confirmed_steps']+=1
                elif event=='dispatch':row['current_step']=step
            if event=='unloaded':row.update(phase='replaced',reason='程序已替换，原执行未确认完成',ended_at=wall)
            elif event=='terminal':
                row.update(phase='error' if data.get('reason_kind')=='failed' else runner.state if runner.state in TERMINAL else 'aborted',
                           reason=safe_reason(runner.error),ended_at=wall)
            elif event=='phase':row['phase']=runner.state if runner.state in ACTIVE else row['phase']
            row['updated_at']=wall
            row['elapsed_s']=round(max(0,now-self._started.get(run_id,now)),3)
            row['partial']=row['confirmed_steps']>0 and row['phase']!='finished'
            self.dirty=True;self.condition.notify_all()

    def list(self):
        with self.lock:return dict(ok=True,runs=copy.deepcopy(self.records[::-1]),error=self.error,analysis_only=True)

    def get(self,run_id):
        with self.lock:
            value=next((r for r in self.records if r['run_id']==run_id),None)
            if value is None:raise ValueError('未找到运行记录')
            return copy.deepcopy(value)

    def flush(self):
        with self.lock:
            if not self.dirty or not self.writable:return
            payload={'version':1,'runs':copy.deepcopy(self.records)};self.dirty=False
        try:
            if len(json.dumps(payload,ensure_ascii=False).encode())>MAX_BYTES:raise ValueError('记录超过预算')
            write_json(self.path,payload)
        except (OSError,ValueError) as exc:
            with self.lock:self.dirty=True;self.error='运行记录保存失败：'+safe_reason(exc)
        else:
            with self.lock:self.error=None

    def _write_loop(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda:self.dirty or self.stopping)
                if self.stopping:break
            self.flush()
            # Coalesce bursts, and bound retries on an unavailable disk.
            with self.condition:self.condition.wait(timeout=.2)
        self.flush()

    async def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():return
            self.stopping=False
            self.thread=threading.Thread(target=self._write_loop,name='execution-journal',daemon=True);self.thread.start()

    async def close(self):
        with self.condition:
            for row in self.records:
                if row['phase'] in ACTIVE:row.update(phase='interrupted',reason='服务退出前未确认完成',ended_at=time.time());self.dirty=True
            self.stopping=True;self.condition.notify_all()
        if self.thread:
            await asyncio.to_thread(self.thread.join,3.)
            if self.thread.is_alive():raise RuntimeError('运行记录写入尚未完成')
            self.thread=None
        else:await asyncio.to_thread(self.flush)
