"""HTTP 与 WebSocket 共用的 Host 边界，防止本机服务被 DNS 重绑定访问。"""
import ipaddress
import os
from urllib.parse import urlsplit


def trusted_host(authority, peer=""):
    try:
        parsed = urlsplit("http://" + authority)
        hostname = parsed.hostname
        port = parsed.port
        if (not hostname or parsed.username or parsed.password or parsed.path
                or parsed.query or parsed.fragment or (port is not None and port <= 0)):
            return False
    except (ValueError, TypeError):
        return False
    hostname = hostname.lower().rstrip(".")
    allowed = {"localhost", "127.0.0.1", "::1"}
    allowed.update(host.strip().lower().rstrip(".") for host in
                   os.environ.get("HUYAN_ALLOWED_HOSTS", "").split(",") if host.strip())
    # TestClient 无网络对端，不把测试域名开放给网络请求。
    return hostname in allowed or (hostname == "testserver" and peer == "testclient")


def local_peer(peer):
    if peer == "testclient":
        return True
    try:
        return ipaddress.ip_address(peer).is_loopback
    except ValueError:
        return False
