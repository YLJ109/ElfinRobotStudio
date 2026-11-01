"""Dedicated CPU hand inference process. Reads RGB bytes, never opens cameras."""
import json
import struct
import sys
import hashlib
import time


def send(value):
    print(json.dumps(value,allow_nan=False),flush=True)


def read_exact(stream, length):
    chunks = bytearray()
    while len(chunks) < length:
        chunk = stream.read(length-len(chunks))
        if not chunk:
            raise EOFError()
        chunks.extend(chunk)
    return bytes(chunks)


def main():
    detector = None
    try:
        import numpy as np
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
        options = vision.HandLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path=sys.argv[1],delegate=python.BaseOptions.Delegate.CPU),
            running_mode=vision.RunningMode.VIDEO,num_hands=1,
            min_hand_detection_confidence=.5,min_hand_presence_confidence=.5,min_tracking_confidence=.5)
        detector = vision.HandLandmarker.create_from_options(options)
        with open(sys.argv[1],'rb') as asset:
            model_hash = hashlib.sha256(asset.read()).hexdigest()
        model = {'name':'mediapipe-hand-landmarker','version':mp.__version__,'sha256':model_hash,'running_mode':'video'}
        send({'ready':True,'model':model})
        last_timestamp_ms=-1
        while True:
            length = struct.unpack('!I',read_exact(sys.stdin.buffer,4))[0]
            if not 1 <= length <= 2048:
                raise ValueError('IPC header too large')
            meta = json.loads(read_exact(sys.stdin.buffer,length))
            width,height,size = meta['width'],meta['height'],meta['bytes']
            if (type(width) is not int or type(height) is not int or type(size) is not int
                    or min(width,height)<1 or width*height>1280*720 or size != width*height*3):
                raise ValueError('IPC image dimensions invalid')
            pixels = np.frombuffer(read_exact(sys.stdin.buffer,size),dtype=np.uint8).reshape(height,width,3)
            # Tracking needs a strictly increasing VIDEO clock even when two
            # frames are processed within one millisecond. Capture freshness
            # remains independently enforced by the parent pipeline.
            timestamp_ms=max(time.monotonic_ns()//1_000_000,last_timestamp_ms+1)
            last_timestamp_ms=timestamp_ms
            result = detector.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,data=pixels),timestamp_ms)
            observations=[]
            for index,landmarks in enumerate(result.hand_landmarks):
                handedness=result.handedness[index][0] if result.handedness[index] else None
                observations.append({'kind':'hand','landmarks':[
                    {'x':p.x,'y':p.y,'z':p.z} for p in landmarks],
                    'handedness':handedness.category_name if handedness else None,
                    'confidence':handedness.score if handedness else None})
            send({'observations':observations,'model':model})
    except EOFError:
        return
    except Exception as exc:
        send({'ready':False,'error':str(exc)[:240] or type(exc).__name__})
    finally:
        if detector is not None:
            detector.close()


if __name__ == '__main__': main()
