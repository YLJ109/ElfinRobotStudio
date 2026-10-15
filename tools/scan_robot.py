"""按华沿(V6)控制器已知端口扫描网段，找出机器人。"""

import argparse
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor

# 端口来自 docs/huayan-pdf/HuayanRobotV6控制通信协议接口_V1.0.19.1.pdf 3.2 服务端口表
ROBOT_PORTS = {
    80: "Web 示教器 (http://IP/dist)",
    10001: "IF 快速命令",
    10003: "IF 命令 (SDK 连接口)",
    10004: "Data Sheet JSON 50ms",
    10005: "Data Sheet JSON 100ms",
    10006: "Data Sheet JSON 200ms",
    10502: "Modbus TCP",
    1883: "MQTT",
}


def probe(host, port, timeout):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return port
    except OSError:
        return None


def scan(cidr, timeout, workers):
    nets = [ipaddress.ip_network(c) for c in cidr]
    hosts = [str(h) for n in nets for h in n.hosts()]
    found = {}
    with ThreadPoolExecutor(workers) as ex:
        for host in hosts:
            hits = list(ex.map(lambda p: probe(host, p, timeout), ROBOT_PORTS))
            open_ports = [p for p in hits if p]
            if open_ports:
                found[host] = open_ports
                for p in open_ports:
                    print(f"{host}:{p:<5} 开  {ROBOT_PORTS[p]}", flush=True)
    if not found:
        print(f"扫描 {len(hosts)} 个地址，未发现开放上述端口的设备。")
    return found


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cidr", nargs="*", default=["192.168.0.0/24"])
    ap.add_argument("--timeout", type=float, default=0.25)
    ap.add_argument("--workers", type=int, default=256)
    a = ap.parse_args()
    scan(a.cidr, a.timeout, a.workers)
