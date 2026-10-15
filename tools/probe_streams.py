"""只读探测华沿 Elfin 控制器：10003 是否吐/需握手，10004~10006 是否按 LTBR 帧推送 JSON。

不发送任何字节（无 sendall / write），仅 connect + recv + close。
"""

import json
import socket
import struct
import time

ROBOT = "192.168.0.10"
MAGIC = b"LTBR"


def recv_exact(sock, n, deadline):
    buf = b""
    while len(buf) < n:
        if time.monotonic() > deadline:
            return None
        sock.settimeout(max(0.05, deadline - time.monotonic()))
        try:
            chunk = sock.recv(n - len(buf))
        except socket.timeout:
            continue
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


def read_frames(port, seconds):
    """返回 (帧列表, 每帧到达时刻, 错误)；帧 = (字节序, 总长, 净数据长度, payload)。"""
    s = socket.create_connection((ROBOT, port), timeout=2)
    frames, stamps, err = [], [], None
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            mark = time.monotonic()
            head = recv_exact(s, 12, end)
            if head is None:
                err = "读取超时/连接关闭"
                break
            if head[:4] != MAGIC:
                err = f"帧头不符: {head[:4]!r}"
                break
            total_le, plen_le = struct.unpack("<II", head[4:12])
            total_be, plen_be = struct.unpack(">II", head[4:12])
            for endian, total, plen in (("<", total_le, plen_le), (">", total_be, plen_be)):
                if plen <= 1024 * 1024:
                    break
            payload = recv_exact(s, plen, end)
            if payload is None:
                err = f"净数据不完整 (plen={plen}, endian={endian})"
                break
            frames.append((endian, total, plen, payload))
            stamps.append(mark)
    finally:
        s.close()
    return frames, stamps, err


def probe_silent(port, seconds):
    """连接后只听不发，报告控制器主动推送的字节数。"""
    s = socket.create_connection((ROBOT, port), timeout=2)
    got = b""
    closed = False
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            s.settimeout(max(0.05, end - time.monotonic()))
            try:
                chunk = s.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                closed = True
                break
            got += chunk
    finally:
        s.close()
    return len(got), got[:120], not closed


if __name__ == "__main__":
    n, sample, closed = probe_silent(10003, 1.5)
    print(f"[10003 IF命令口] 控制器主动推送字节={n} 被服务器断开={closed} 样本={sample!r}")

    for port, nominal in ((10004, 50), (10005, 100), (10006, 200)):
        frames, stamps, err = read_frames(port, 2.0)
        if not frames:
            print(f"[{port}] 无有效帧 err={err}")
            continue
        gaps = [(stamps[i + 1] - stamps[i]) * 1000 for i in range(len(stamps) - 1)]
        gaps.sort()
        med = gaps[len(gaps) // 2] if gaps else float("nan")
        endian, total, plen, payload = frames[0]
        try:
            data = json.loads(payload.decode("utf-8"))
            keys, ok = list(data), "JSON可解析"
        except Exception as e:
            keys, ok = [], f"JSON解析失败 {e}"
        print(f"[{port}] 帧数={len(frames)} 标称周期={nominal}ms 实测帧间隔中位数={med:.1f}ms "
              f"字节序={endian} 总长字段={total} 净数据长度字段={plen} 实收payload={len(payload)} {ok}")
        print(f"        顶层键({len(keys)})={keys}")
        if "PosAndVel" in keys:
            pv = data["PosAndVel"]
            print(f"        Actual_Position={pv.get('Actual_Position')}")
            print(f"        Actual_Override={pv.get('Actual_Override')}")
