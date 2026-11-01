"""Isolated geometric diagnostics; this is not whole-path execution acceptance."""
from _offline_guard import require_offline_copy
require_offline_copy()
import json
import time
from backend.core.world import World
from backend.application.drawing_tcp import build_tool
from backend.core.safety import evaluate

w=World();w.settings['mode']='sim';model,_=build_tool(w);lo,hi=w.limits()
deadline=time.monotonic()+150
for roll,pitch,yaw,z,cx in ((90,0,0,600,-425),(90,0,0,650,-425),(-90,0,0,600,-425),(0,110,180,650,-425),(0,120,180,650,-425)):
    width=height=280
    rows=[]
    for x,dz in ((0,0),(-width/2,-height/2),(width/2,-height/2),(width/2,height/2),(-width/2,height/2)):
        target=[cx+x,0,z+dz,roll,pitch,yaw];best=None
        for seed in ([0,-45,90,0,45,0],[0,-70,-90,0,100,0],[0,45,-90,0,90,0],[-90,-45,90,0,45,0],[90,-45,90,0,45,0]):
            result=model.ik(seed,target,lo,hi,iters=180,max_restarts=2,deadline=deadline)
            if best is None or result[1]+result[2]<best[1]+best[2]:best=result
            if result[3]:best=result;break
        safety=evaluate(model,best[0],w.settings,lo,hi)
        rows.append({'target':target,'ok':best[3],'position_error_mm':best[1],'rotation_error_deg':best[2],'q':best[0],'safety':safety['level'],'reasons':safety['reasons'],'collisions':safety['collisions']})
    print('DRAWING_REACH',json.dumps({'center':[cx,0,z],'rpy':[roll,pitch,yaw],'width':width,'height':height,'all_ok':all(r['ok'] and r['safety'] not in ('red','alarm') for r in rows),'points':rows}),flush=True)
