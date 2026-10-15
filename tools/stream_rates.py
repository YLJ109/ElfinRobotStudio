"""逐端口测量控制器 Data Sheet 推送：周期、payload 类型、是否被服务器关闭。"""

import json
import socket
import struct
import time

ROBOT = "192.168.0.10"
MAGIC = b"LTBR"
PORTS = (10001, 10003, 10004, 10005, 10006, 10014, 10015, 10016)
DOC = {10001: "IF快速命令", 10003: "IF命令", 10004: "JSON 50ms", 10005: "JSON 100ms",
       10006: "JSON 200ms", 10014: "Struct 50ms", 10015: "Struct 100ms", 10016: "Struct 200ms"}


def measure(port, seconds):
    t0 = time.monotonic()
    s = socket.create_connection((ROBOT, port), timeout=2)
    buf, frames, sizes, closed_at, first = b"", 0, [], None, None
    try:
        s.settimeout(0.5)
        while time.monotonic() - t0 < seconds:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                continue
            except OSError as e:
                closed_at = f"error {e}"
                break
            if not chunk:
                closed_at = round(time.monotonic() - t0, 3)
                break
            if first is None:
                first = time.monotonic() - t0
            buf += chunk
            while len(buf) >= 12:
                if buf[:4] != MAGIC:
                    return dict(port, bad_header=buf[:12].hex(), frames=frames)
                total, plen = struct.unpack("<II", buf[4:12])
                if len(buf) < 12 + plen:
                    break
                payload = buf[12:12 + plen]
                buf = buf[12 + plen:]
                frames += 1
                sizes.append(len(payload))
                if frames == 1:
                    kind = "json"
                    try:
                        data = json.loads(payload.decode("utf-8"))
                        tops = list(data)
                    except Exception:
                        kind, tops = "binary", []
                    result = (kind, tops)
    finally:
        s.close()
    span = time.monotonic() - t0 - first if first else 0
    return dict(
        port=port, frames=frames, first_byte_s=round(first, 3) if first else None,
        closed_at=closed_at, span_s=round(span, 2),
        period_ms=round(span * 1000 / frames, 1) if frames and span else None,
        payload_bytes=(min(sizes), max(sizes)) if sizes else None,
        uniform=all(x == sizes[0] for x in sizes) if sizes else None,
        kind=result[0] if frames else None, tops=result[1] if frames and result[0] == "json" else None,
    )


if __name__ == "__main__":
    for p in PORTS:
        r = measure(p, 3.0)
        print(f"{p} ({DOC[p]}): frames={r.get('frames')} period={r.get('period_ms')}ms "
              f"首字节@{r.get('first_byte_s')}s 关闭@{r.get('closed_at')} "
              f"payload={r.get('payload_bytes')} 等长={r.get('uniform')} kind={r.get('kind')}")
        if r.get("tops"):
            print(f"      顶层键({len(r['tops'])})={r['tops']}")
        if r.get("bad_header"):
            print(f"      帧头异常: {r['bad_header']}")
        time.sleep(1.0)
