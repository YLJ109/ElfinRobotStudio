"""Offline-only pixel fixture shared by the migrated baseline checks."""
from _offline_guard import require_offline_copy
require_offline_copy()
import time

def pixel_vision(pieces):
    """Synthetic pixels through the real HSV/calibrate/map/track pipeline."""
    import cv2 as cv
    import numpy as np
    from backend.core.vision_workspace import VisionWorkspace, settings
    engine=VisionWorkspace(None)
    engine.virtual_session='synthetic'
    engine.config=settings({'input':'virtual','mapping':True,'height_mm':24,
                            'roi':[0,0,1,1],'colors':['red','green','blue']})
    image=np.full((640,640,3),120,dtype=np.uint8)
    expected={}
    bgr={'red':(0,0,255),'green':(0,255,0),'blue':(255,0,0)}
    for i,p in enumerate(pieces):
        x=round((p['xyz'][0]-290)*2);y=round((p['xyz'][1]+510)*2)
        color=('blue','red','green')[i%3];expected[p['id']]=color
        cv.rectangle(image,(x-72,y-72),(x+72,y+72),bgr[color],-1)
    ok,encoded=cv.imencode('.png',image);assert ok
    data=encoded.tobytes();key='virtual:synthetic:fixed-view'
    initial=engine.analyze(data,engine.config,None,key)
    initial.update(source='virtual',source_key=key,captured_at=time.time(),frame_seq=1)
    engine.latest=initial
    rows=[[x,y,290+x*.5,-510+y*.5] for x,y in [(0,0),(639,0),(639,639),(0,639),(200,200),(440,440)]]
    engine.calibrate({'points':rows,'plane_z':4,'max_error_mm':2})
    result=engine.analyze(data,engine.config,engine.calibration,key)
    now=time.time();result['objects']=engine.track(result['objects'],now)
    result.update(source='virtual',source_key=key,captured_at=now,frame_seq=2)
    engine.latest=result;engine.source_key=key
    return engine,expected
