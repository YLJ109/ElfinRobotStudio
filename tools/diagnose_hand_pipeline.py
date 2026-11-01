"""Measure the real detector warm-up latency and steady-state rate.

Uses per-epoch deltas of the public stats counters so that cumulative
counters from earlier sessions cannot mask the cold-start behaviour.

Usage: python tools/diagnose_hand_pipeline.py [--session <camera session>]
"""
from __future__ import annotations
import argparse
import json
import time
import urllib.request

BASE = 'http://127.0.0.1:8710'


def request(path, payload=None, timeout=30):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {'Content-Type': 'application/json'} if data else {}
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method='POST' if data is not None else 'GET')
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode())


def pipeline():
    return request('/api/vision/pipeline', timeout=10)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--session')
    parser.add_argument('--keep', action='store_true')
    args = parser.parse_args()

    active = request('/api/cameras', timeout=10).get('active')
    if not active:
        print('NO_CAMERA active=null')
        return 1
    session = args.session or active['session']
    size = active.get('images', {}).get('color', {})
    print(f'CAMERA session={session} state={active.get("state")} '
          f'actual_fps={active.get("actual_fps")} color={size.get("width")}x{size.get("height")}')

    # Disable and wait so the idle model is retired: the next enable is cold.
    request('/api/vision/pipeline/config',
            {'enabled': False, 'source': 'local', 'session': session}, timeout=30)
    time.sleep(22.0)

    base = pipeline()['stats']
    print(f'COLD_BASE stats={base}')

    t0 = time.monotonic()
    response = request('/api/vision/pipeline/config',
                       {'enabled': True, 'source': 'local', 'session': session}, timeout=30)
    print(f'CONFIG_ACCEPTED after={(time.monotonic() - t0) * 1000:.0f}ms '
          f'enabled={response["enabled"]} error={response.get("error")!r}')

    samples = []
    first_processed = None
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        snap = pipeline()
        stats = snap['stats']
        delta = {key: stats[key] - base[key] for key in stats}
        elapsed = (time.monotonic() - t0) * 1000
        samples.append((elapsed, delta, snap.get('error'), snap.get('busy')))
        if delta['processed'] > 0 and first_processed is None:
            first_processed = elapsed
            print(f'FIRST_PROCESSED after={elapsed:.0f}ms delta={delta} '
                  f'busy={snap.get("busy")} error={snap.get("error")!r}')
            break
        time.sleep(0.25)
    if first_processed is None:
        print(f'NO_PROCESSING_AFTER_45s delta='
              f'{ {k: pipeline()["stats"][k] - base[k] for k in base} } '
              f'error={pipeline().get("error")!r}')
        return 2

    time.sleep(6.0)
    snap = pipeline()
    stats = snap['stats']
    delta = {key: stats[key] - base[key] for key in stats}
    result = snap.get('result') or {}
    window = 6.0
    print(f'STEADY delta_over_{window}s={delta} '
          f'rate={delta["processed"] / window:.1f} inferences/s')
    print(f'RESULT valid={result.get("valid")} control_valid={result.get("control_valid")} '
          f'observations={len(result.get("observations") or [])} error={result.get("error")!r} '
          f'model={(result.get("model") or {}).get("version")!r}')
    print(f'PIPELINE error={snap.get("error")!r} busy={snap.get("busy")} '
          f'pending={snap.get("pending")}')

    if not args.keep:
        request('/api/vision/pipeline/config',
                {'enabled': False, 'source': 'local', 'session': session}, timeout=30)
        after = pipeline()
        print(f'AFTER_DISABLE enabled={after["enabled"]} source={after["source"]!r} '
              f'error={after.get("error")!r}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
