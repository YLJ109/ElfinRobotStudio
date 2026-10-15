"""Opt-in real PC camera validation. No robot imports, no image files saved."""
import sys
import time
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sim.backend.core.cameras import CameraManager

def main():
    manager = CameraManager()
    try:
        state = manager.scan()
        print(json.dumps({'sdk':state['sdk'], 'devices':[{k:d.get(k) for k in ('number','name','id','kind','streams')} for d in state['devices']]}, ensure_ascii=False))
        if '--open' not in sys.argv: return
        successes = 0
        for device in state['devices']:
            result = manager.open(device['id'], ['color'] if 'color' in device['streams'] else ['depth'])
            if not result.get('ok'): print(json.dumps({'name':device['name'], 'open_error':result.get('why')}, ensure_ascii=False)); continue
            for _ in range(80):
                time.sleep(.1)
                active = manager.snapshot()['active']
                if active['state'] in ('streaming','error'): break
            session = active['session']
            frame = manager.image('color' if device['kind']=='usb' else next(iter(active['images']), 'depth'), session)
            if frame:
                import cv2
                import numpy as np
                image = cv2.imdecode(np.frombuffer(frame, dtype=np.uint8), cv2.IMREAD_COLOR)
                assert image is not None and image.shape[0] > 0 and image.shape[1] > 0
                assert manager.image('color', 'wrong-session') is None
                oldseq = active['seq']; time.sleep(.3); assert manager.snapshot()['active']['seq'] > oldseq
                successes += 1
                print(json.dumps({'name':device['name'], 'state':active['state'], 'shape':list(image.shape), 'jpeg_bytes':len(frame), 'seq_advances':True}, ensure_ascii=False))
            else: print(json.dumps({'name':device['name'], 'error':active.get('error', '未收到图像')}, ensure_ascii=False))
            assert manager.close()['ok']
            assert manager.image('color', session) is None
        print(f'real-camera frames verified: {successes}/{len(state["devices"])}')
    finally: manager.close()
if __name__ == '__main__': main()
