"""T3 冒烟测试：命令通道能否真正收发。

只用只读指令，不改机器人任何状态。验证三件事：
1. 命令口(10001/10003)能否发送 ASCII 指令并拿到回执
2. 回执与 ~50ms 的 LTBR 推送帧混在一条流里，能否正确分流
3. 串行发送（协议 p12：一次发多条只处理第一条）是否必须等回执
"""

import socket
import struct
import sys
import time

ROBOT = "192.168.0.10"
MAGIC = b"LTBR"

# (指令原文, 期望回执前缀) —— 全部只读，格式取自协议文档对应页
CMDS = [
    ("IsSimulation,;", "IsSimulation,"),            # p25
    ("ReadControllerState,;", "ReadControllerState,"),  # p26
    ("PackageVersion,;", "PackageVersion,"),        # p29
    ("ReadRobotModel,;", "ReadRobotModel,"),        # p28
    ("ReadCurFSM,0,;", "ReadCurFSM,"),              # p97
    ("ReadOverride,0,;", "ReadOverride,"),          # p87
    ("ReadBrakeStatus,0,;", "ReadBrakeStatus,"),    # p40
    ("ReadActPos,0,;", "ReadActPos,"),              # p102
]


class Channel:
    """一条 TCP：把 LTBR 二进制帧剥掉，只留 ASCII 文本流。"""

    def __init__(self, port):
        self.sock = socket.create_connection((ROBOT, port), timeout=3)
        self.sock.settimeout(0.2)
        self.text = b""
        self.frames = 0

    def pump(self, until=None, timeout=1.5):
        """收到 until(prefix, text) 返回 True 为止；否则收到超时为止。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if until and until("", self.text):
                return True
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                time.sleep(0.01)
                continue
            self._feed(chunk)
        return until("", self.text) if until else False

    def _feed(self, data):
        buf = self.text + data
        out = b""
        i = 0
        while i < len(buf):
            j = buf.find(MAGIC, i)
            if j < 0:
                out += buf[i:]
                break
            out += buf[i:j]
            if len(buf) < j + 12:
                out += buf[j:]
                break
            _, plen = struct.unpack("<II", buf[j + 4:j + 12])
            if len(buf) < j + 12 + plen:
                out += buf[j:]
                break
            self.frames += 1
            i = j + 12 + plen
        self.text = out

    def take_reply(self, prefix, timeout=2.0):
        def done(_, buf):
            t = buf.decode("latin1")
            p = t.find(prefix)
            return p >= 0 and t.find(",;", p) >= 0

        if not self.pump(until=done, timeout=timeout):
            return None
        txt = self.text.decode("latin1")
        pos = txt.find(prefix)
        end = txt.find(",;", pos)
        self.text = txt[end + 2:].encode("latin1")
        return txt[pos:end + 2]

    def close(self):
        self.sock.close()


def first_connect(port, attempts=4):
    for i in range(attempts):
        try:
            ch = Channel(port)
        except OSError as e:
            print(f"  连接 {port} 失败: {e}，重试 {i + 1}/{attempts}")
            time.sleep(1.0)
            continue
        # 命令口是否推流取决于机器人状态（enabled=0 时实测不推），不作为可发送的前提
        ch.pump(timeout=0.3)
        return ch
    raise ConnectionError(f"无法连接端口 {port}")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 10001
    print(f"== 命令通道冒烟测试 port={port} @ {ROBOT} ==")
    ch = first_connect(port)
    print(f"初连已收到 LTBR 帧 {ch.frames} 个（说明命令口确实在推流）\n")

    ok = 0
    for send, prefix in CMDS:
        t0 = time.monotonic()
        ch.sock.sendall(send.encode("ascii"))
        reply = ch.take_reply(prefix)
        ms = (time.monotonic() - t0) * 1000
        if reply is None:
            print(f"  {send:<28} -> 无回执 ({ms:.0f}ms) 残留文本={ch.text[:80]!r}")
        else:
            good = ",OK," in reply
            ok += good
            print(f"  {send:<28} -> {reply}   [{ms:.0f}ms]")
        time.sleep(0.12)

    print(f"\n通过 {ok}/{len(CMDS)}  累计剥离推送帧 {ch.frames} 个")
    ch.close()
