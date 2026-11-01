"""Prove which step of the hand detection chain actually breaks.

Three controlled scenarios against a live backend and a real camera:

  A  enable detection and let it run        -> expect processed to climb
  B  enable then disable inside the warm-up -> expect the user's frozen
                                                 submitted/replaced/0-processed
  C  enable detection then close the camera -> expect the pipeline to notice
                                                 that its source is gone

Usage: python tools/probe_hand_lifecycle.py
"""
from __future__ import annotations
import json
import time
import urllib.request

BASE = 'http://127.0.0.1:8710'


def request(path, payload=None, timeout=40):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {'Content-Type': 'application/json'} if data else {}
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method='POST' if data is not None else 'GET')
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode())


def pipeline():
    return request('/api/vision/pipeline', timeout=10)


def cameras():
    return request('/api/cameras', timeout=10)


def show(label, snap, base=None):
    stats = snap['stats']
    if base:
        stats = {k: stats[k] - base[k] for k in stats}
    print(f'  {label:<22} stats={stats} enabled={snap["enabled"]} '
          f'error={snap.get("error")!r} busy={snap.get("busy")} pending={snap.get("pending")} '
          f'result_valid={(snap.get("result") or {}).get("valid")}')


def scenario_a(session):
    print('A  enable and hold (expect processed to climb)')
    base = pipeline()['stats']
    request('/api/vision/pipeline/config',
            {'enabled': True, 'source': 'local', 'session': session}, timeout=30)
    for _ in range(4):
        time.sleep(1.5)
        show('tick', pipeline(), base)
    request('/api/vision/pipeline/config',
            {'enabled': False, 'source': 'local', 'session': session}, timeout=30)


def scenario_b(session):
    print('B  enable then disable during warm-up (expect the frozen 0-processed state)')
    request('/api/vision/pipeline/config',
            {'enabled': False, 'source': 'local', 'session': session}, timeout=30)
    time.sleep(21)  # let the idle model retire so the next enable is a cold start
    base = pipeline()['stats']
    request('/api/vision/pipeline/config',
            {'enabled': True, 'source': 'local', 'session': session}, timeout=30)
    time.sleep(0.7)  # inside the detector warm-up window
    request('/api/vision/pipeline/config',
            {'enabled': False, 'source': 'local', 'session': session}, timeout=30)
    time.sleep(0.3)
    snap = pipeline()
    show('after disable', snap, base)
    print(f'  -> submitted={snap["stats"]["submitted"] - base["submitted"]} '
          f'replaced={snap["stats"]["replaced"] - base["replaced"]} '
          f'processed={snap["stats"]["processed"] - base["processed"]} '
          f'failures={snap["stats"]["failures"] - base["failures"]} '
          f'error={snap.get("error")!r}')


def scenario_c(session):
    print('C  enable detection then close the camera (expect enabled to drop)')
    request('/api/vision/pipeline/config',
            {'enabled': True, 'source': 'local', 'session': session}, timeout=30)
    time.sleep(2.0)
    show('before close', pipeline())
    request('/api/cameras/close', {'session': session}, timeout=30)
    time.sleep(2.0)
    snap = pipeline()
    show('after camera close', snap)
    print(f'  -> pipeline still enabled={snap["enabled"]} source={snap["source"]!r} '
          f'error={snap.get("error")!r}')


def main():
    active = cameras().get('active')
    if not active:
        print('NO_CAMERA')
        return 1
    session = active['session']
    print(f'CAMERA session={session} state={active.get("state")} fps={active.get("actual_fps")}')
    scenario_a(session)
    scenario_b(session)
    scenario_c(session)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
