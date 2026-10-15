"""只读：抓 10004 的原始 JSON，把 IO/末端/摄像头相关字段结构打印出来（不发送任何指令）。"""
import json, socket, struct, sys

MAGIC = b"LTBR"
host = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.10"
s = socket.create_connection((host, 10004), timeout=5)
s.settimeout(5)
buf = b""
frames = []
while len(frames) < 2:
    try:
        d = s.recv(65536)
    except socket.timeout:
        break
    if not d:
        break
    buf += d
    while True:
        if len(buf) < 12 or buf[:4] != MAGIC:
            break
        total, net = struct.unpack("<II", buf[4:12])
        if len(buf) < total:
            break
        frames.append(buf[12:12 + net])
        buf = buf[total:]
s.close()
if not frames:
    sys.exit("没抓到帧")
j = json.loads(frames[-1].decode("utf-8"))
print("顶层键：", list(j.keys()))
for k in list(j.keys()):
    if "IO" in k or "CAM" in k.upper() or "Vision" in k or "Sensor" in k:
        print("\n===", k)
        print(json.dumps(j[k], ensure_ascii=False, indent=2))
print("\n== EndIO 全文")
print(json.dumps(j.get("EndIO", {}), ensure_ascii=False, indent=2))
print("\n== ElectricBoxIO 全文")
print(json.dumps(j.get("ElectricBoxIO", {}), ensure_ascii=False, indent=2))
json.dump(j, open("tools/datasheet_full.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("\n完整帧已存 tools/datasheet_full.json")
