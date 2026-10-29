"""Explicit one-step live acceptance. Never runs a full sequence on its own."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.hy import get, post, Guard

parser=argparse.ArgumentParser()
parser.add_argument('action',choices=['state','electrify','start_master','enable','disable','blackout','reset','estop','ready','cancel_ready','j6plus','j6minus'])
parser.add_argument('--confirmed',action='store_true',help='Operator has explicitly confirmed clear area and physical E-stop access')
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]

def compact(s):
    r=s.get('real') or {}
    return {'session':s.get('session_id'),'mode':s.get('mode'),
            'real':{k:r.get(k) for k in ('stream_up','cmd_up','quality','frame_seq','connection_epoch','lag_ms','fsm','enabled','moving','inpos','err_code','override','joints','last_command')},
            'ready':s.get('ready'),'exec':s.get('exec'),'ctrl':s.get('ctrl')}

before=get('/api/state');record={'at':time.strftime('%Y-%m-%d %H:%M:%S'),'action':args.action,'before':compact(before)}
r=before.get('real') or {}
if args.action!='state':
    if (before.get('mode')!='real' or not r.get('stream_up') or not r.get('cmd_up')
            or r.get('quality')!='valid' or r.get('moving')!=0 or r.get('inpos')!=1):
        raise SystemExit('Not a fresh stationary real controller; no command sent')
    if args.action not in ('reset','estop','disable','blackout') and r.get('err_code')!=0:
        raise SystemExit('Controller fault; no command sent')
    sources={'electrify':7,'start_master':14,'enable':24}
    if args.action in sources and (r.get('fsm')!=sources[args.action] or r.get('enabled')!=0):
        raise SystemExit('Wrong source state; no command sent')
    if args.action=='blackout' and r.get('enabled')!=0:
        raise SystemExit('Disable must be confirmed before BlackOut')
    if args.action in ('j6plus','j6minus'):
        reply=Guard().move_j6(2. if args.action=='j6plus' else -2.)
    elif args.action=='ready':
        challenge=None
        if (before.get('ctrl') or {}).get('need_ack'):
            if not args.confirmed:raise SystemExit('Explicit current operator confirmation required')
            ack=post('/api/perm/ack',{'acks':{'nobody':True,'estop':True}})
            if not ack.get('ok'):raise SystemExit('Operator acknowledgement rejected')
            challenge=ack['challenge']
        reply=post('/api/control',{'key':'ready','challenge':challenge})
    elif args.action=='cancel_ready':reply=post('/api/control',{'key':'cancel_ready'})
    elif args.action=='estop':reply=post('/api/estop',{})
    else:reply=post('/api/power',{'action':args.action})
    record['reply']=reply
    # ACK is not completion. Save a bounded observation of a new state frame.
    deadline=time.monotonic()+20
    want={'electrify':14,'start_master':24,'enable':33,'disable':24,'blackout':7,'cancel_ready':24,'ready':33}.get(args.action)
    after=get('/api/state')
    while time.monotonic()<deadline:
        a=after.get('real') or {}
        fresh=(a.get('quality')=='valid' and a.get('frame_seq',0)>r.get('frame_seq',0)
               and after.get('session_id')==before.get('session_id'))
        complete=fresh and (want is None or a.get('fsm')==want)
        if args.action=='ready':
            rs=after.get('ready') or {}
            complete=complete and rs.get('done') and not rs.get('running') and not rs.get('why')
        if args.action in ('j6plus','j6minus'):
            target=list(r['joints']);target[5]+=2 if args.action=='j6plus' else -2
            execution=(after.get('exec') or {}).get('execution') or {}
            q=a.get('joints') or []
            complete=(fresh and not execution.get('busy') and execution.get('state')=='completed'
                      and len(q)==6 and max(abs(x-y) for x,y in zip(q,target))<=.05
                      and a.get('moving')==0 and a.get('inpos')==1)
            if a.get('override') is None or a['override']>.05 or a.get('err_code')!=0:
                record['abort']=post('/api/estop',{});break
            if not execution.get('busy') and execution.get('state') in ('failed','cancelled'):
                break
        if complete:break
        if not reply.get('ok'):break
        time.sleep(.1);after=get('/api/state')
    record['after']=compact(after)
else:record['after']=record['before']
out=root/'output/checks/real-ready-20261029.jsonl';out.parent.mkdir(parents=True,exist_ok=True)
with out.open('a',encoding='utf-8') as f:f.write(json.dumps(record,ensure_ascii=False)+'\n')
print(json.dumps(record,ensure_ascii=False,indent=2))
