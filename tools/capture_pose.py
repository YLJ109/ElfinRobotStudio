"""从 10004 抓取机器人坐标，写 JSONL。只读，不发送任何指令。

用法：python tools/capture_pose.py [采样秒数] [输出文件]
"""

import json
import socket
import struct
import sys
import time

ROBOT = "192.168.0.10"
MAGIC = b"LTBR"


def frames(sock, seconds, buf=b""):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while len(buf) >= 12:
            if buf[:4] != MAGIC:
                raise ValueError(f"帧头异常: {buf[:12].hex()}")
            _, plen = struct.unpack("<II", buf[4:12])
            if len(buf) < 12 + plen:
                break
            yield json.loads(buf[12:12 + plen].decode("utf-8"))
            buf = buf[12 + plen:]
        sock.settimeout(0.5)
        try:
            buf += sock.recv(65536)
        except socket.timeout:
            continue


def connect_retry(attempts=4):
    last = None
    for i in range(attempts):
        s = socket.create_connection((ROBOT, 10004), timeout=2)
        try:
            s.settimeout(1.5)
            head = s.recv(16)
        except socket.timeout:
            s.close()
            last = "首帧超时(端口可能被其他客户端占用)"
            time.sleep(1.5)
            continue
        if not head.startswith(MAGIC):
            s.close()
            last = f"首帧非法: {head[:12]!r}"
            time.sleep(1.5)
            continue
        return s, head
    raise ConnectionError(last or "无法建立可用连接")


if __name__ == "__main__":
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
    out = sys.argv[2] if len(sys.argv) > 2 else "tools/pose_capture.jsonl"

    sock, head = connect_retry()
    rows, buf = [], head
    gen = frames(sock, seconds, buf)
    for d in gen:
        pv = d["PosAndVel"]
        st = d["StateAndError"]
        rows.append({
            "t_host": round(time.monotonic(), 6),
            "stamp": d["MsgTitle"]["Stamp"],
            "joints_deg": [float(x) for x in pv["Actual_Position"][:6]],
            "pos_last6": [float(x) for x in pv["Actual_Position"][6:]],
            "pcs_tcp": [float(x) for x in pv["Actual_PCS_TCP"]],
            "pcs_base": [float(x) for x in pv["Actual_PCS_Base"]],
            "override": float(pv["Actual_Override"]),
            "vel_deg_s": [float(x) for x in pv["Actual_Joint_Velocity"]],
            "cur_a": [float(x) for x in pv["Actual_Joint_Current"]],
            "temp_c": [float(x) for x in d["HardLoad"]["Joint_temperature"]],
            "moving": st["robotMoving"],
            "inpos": st["InPos"],
            "enabled": st["robotEnabled"],
        })
    sock.close()

    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    ts = [r["t_host"] for r in rows]
    gaps = sorted((ts[i + 1] - ts[i]) * 1000 for i in range(len(ts) - 1))
    print(f"帧数={len(rows)} 时长={ts[-1] - ts[0]:.2f}s 间隔中位数={gaps[len(gaps) // 2]:.1f}ms")
    print(f"移动中={rows[0]['moving']} 到位={rows[0]['inpos']} 使能={rows[0]['enabled']} "
          f"速度比={rows[0]['override']}")
    j0, j1 = rows[0]["joints_deg"], rows[-1]["joints_deg"]
    print(f"关节角 首帧={j0}")
    print(f"关节角 末帧={j1}")
    print(f"  最大变化={max(abs(a - b) for a, b in zip(j0, j1)):.4f} deg")
    print(f"PCS_TCP  末帧={rows[-1]['pcs_tcp']}")
    print(f"PCS_Base 末帧={rows[-1]['pcs_base']}")
    print(f"Actual_Position后6位 末帧={rows[-1]['pos_last6']}")
    print(f"与PCS_Base逐元素差={[round(abs(a - b), 3) for a, b in zip(rows[-1]['pos_last6'], rows[-1]['pcs_base'])]}")
    print(f"与PCS_TCP逐元素差 ={[round(abs(a - b), 3) for a, b in zip(rows[-1]['pos_last6'], rows[-1]['pcs_tcp'])]}")
    print(f"电流={[r['cur_a'] for r in rows][-1]} 温度={[r['temp_c'] for r in rows][-1]}")
    print(f"已写入 {out}")
