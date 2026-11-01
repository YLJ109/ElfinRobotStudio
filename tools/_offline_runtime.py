"""Startup barriers for disposable offline checks, installed as sitecustomize."""
import builtins
import os
from pathlib import Path
import socket
import sys
import threading

root = Path(os.environ["HUYAN_OFFLINE_ROOT"]).resolve()
data = Path(os.environ["ELFIN_DATA_DIR"]).resolve()
if not data.is_relative_to(root) or data == root:
    raise RuntimeError("Offline data directory must be inside disposable checkout")
os.environ["HUYAN_NO_REAL"] = "1"


def deny_network(*args, **kwargs):
    raise RuntimeError("Offline checks: socket connections and datagrams are forbidden")


original_connect = socket.socket.connect
original_socketpair = socket.socketpair
internal = threading.local()


def guarded_connect(wire, address):
    # Windows asyncio builds its wakeup pipe with socketpair's loopback shim.
    if getattr(internal, 'socketpair', False) and address[0] in ('127.0.0.1', '::1'):
        return original_connect(wire, address)
    return deny_network()


def guarded_socketpair(*args, **kwargs):
    internal.socketpair = True
    try:
        return original_socketpair(*args, **kwargs)
    finally:
        internal.socketpair = False


socket.socketpair = guarded_socketpair
socket.socket.connect = guarded_connect
socket.socket.connect_ex = deny_network
socket.socket.sendto = deny_network
socket.create_connection = deny_network
original_import = builtins.__import__


def guarded_import(name, *args, **kwargs):
    module = original_import(name, *args, **kwargs)
    cv = sys.modules.get("cv2")
    if cv is not None and not getattr(cv, "_offline_capture_guarded", False):
        cv.VideoCapture = deny_network
        cv._offline_capture_guarded = True
    return module


builtins.__import__ = guarded_import
os.environ['HUYAN_OFFLINE_RUNTIME_PID'] = str(os.getpid())
