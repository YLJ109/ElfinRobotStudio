"""Fine-grained cold-start probe for the hand detection pipeline.

Retires the idle model, re-enables detection, and samples the public
snapshot every 200 ms so the warm-up window and the detector lifecycle
become visible as numbers instead of guesses.

Usage: python tools/probe_hand_warmup.py [--session <session>] [--seconds 60]
"""
from __future__ import annotations
import argparse
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--session')
    parser.add_argument('--seconds', type=float, default=60.)
    parser.add_argument('--retire-wait', type=float, default=22.)
    args = parser.parse_args()

    active = request('/api/cameras', timeout=10).get('active')
    if not active:
        print('NO_CAMERA active=null')
        return 1
    session = args.session or active['session']
    print(f'CAMERA session={session} state={active.get("state")} fps={active.get("actual_fps")}')

    request('/api/vision/pipeline/config',
            {'enabled': False, 'source': 'local', 'session': session}, timeout=30)
    print(f'RETIRE_WAIT {args.retire_wait}s (idle model holds for IDLE_MODEL_SECONDS)')
    time.sleep(args.retire_wait)

    base = request('/api/vision/pipeline', timeout=10)['stats']
    t0 = time.monotonic()
    request('/api/vision/pipeline/config',
            {'enabled': True, 'source': 'local', 'session': session}, timeout=30)
    print(f'ENABLED at t=0  base_stats={base}')

    last = None
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        snap = request('/api/vision/pipeline', timeout=10)
        stats = snap['stats']
        delta = {k: stats[k] - base[k] for k in stats}
        row = (round((time.monotonic() - t0) * 1000), delta['submitted'], delta['processed'],
               delta['inferred'], delta['replaced'], delta['failures'], delta['discarded'],
               snap.get('error'), snap.get('busy'), (snap.get('result') or {}).get('valid'))
        if row != last:
            print(f't={row[0]:>6}ms submitted={row[1]:>4} processed={row[2]:>4} inferred={row[3]:>4} '
                  f'replaced={row[4]:>4} failures={row[5]:>3} discarded={row[6]:>3} '
                  f'error={row[7]!r} busy={row[8]} result_valid={row[9]}')
            last = row
        time.sleep(0.2)

    request('/api/vision/pipeline/config',
            {'enabled': False, 'source': 'local', 'session': session}, timeout=30)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
