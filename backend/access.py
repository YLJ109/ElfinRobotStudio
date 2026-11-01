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


def remote_readonly_request(request):
    """Anonymous visitor document/assets only; never the controller SPA."""
    if request.method != 'GET' or request.url.scheme != 'https':
        return False
    path=request.url.path
    raw=request.scope.get('raw_path',path.encode('ascii',errors='replace'))
    if (raw != path.encode('ascii',errors='replace') or b'%' in raw or b'\\' in raw
            or any(part in ('','.','..') for part in path.split('/')[1:])):
        return False
    document=path=='/remote-camera.html'
    asset=path.startswith('/assets/remote-camera/')
    if not document and not asset:
        return False
    expected='https://'+request.headers.get('host','').lower()
    origin=request.headers.get('origin')
    if origin and origin.lower()!=expected:
        return False
    site=request.headers.get('sec-fetch-site','')
    if document:
        return site in ('none','same-origin') and request.headers.get('sec-fetch-dest','document')=='document'
    if site=='same-origin':
        return True
    # Browsers without Fetch Metadata may prove a same-origin asset referrer.
    try:
        referer=urlsplit(request.headers.get('referer',''))
        return (not site and referer.scheme=='https' and 'https://'+referer.netloc.lower()==expected
                and not referer.username and not referer.password)
    except ValueError:
        return False
