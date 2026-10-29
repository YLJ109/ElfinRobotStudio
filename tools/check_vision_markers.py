"""Offline checks: .venv/Scripts/python.exe -B tools/check_vision_markers.py --guard

Guard is the default: all OpenCV, NumPy and camera behavior is pure stub.
--real additionally checks synthetic in-memory markers with existing OpenCV;
missing aruco is reported as SKIP. Never imports main, opens a device or network.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import builtins
import importlib.util
import json
from pathlib import Path
import selectors
import struct
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import zlib

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def forbid(event, args):
    if event in {"socket.connect", "socket.bind", "subprocess.Popen"}:
        raise AssertionError(f"Offline check blocked {event}")


original_import = builtins.__import__
ALLOW_REAL = False


def safe_import(name, *args, **kwargs):
    if not ALLOW_REAL and name in {"cv2", "numpy"}:
        raise AssertionError(f"Guard forbids real dependency import: {name}")
    if name in {"backend.main", "backend.core.cameras", "pyrealsense2"} or name.startswith("backend.core.robot"):
        raise AssertionError(f"Forbidden live import: {name}")
    return original_import(name, *args, **kwargs)


builtins.__import__ = safe_import
spec = importlib.util.spec_from_file_location("isolated_vision_routes", ROOT / "backend/vision_routes.py")
vision = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = vision
spec.loader.exec_module(vision)


class OfflineSelector(selectors.SelectSelector):
    """No sockets even for Windows asyncio's usual internal wakeup pipe."""
    def select(self, timeout=None):
        assert not self.get_map(), "Offline loop must not register I/O"
        time.sleep(min(.005, max(0, timeout if timeout is not None else .005)))
        return []


class OfflineLoop(asyncio.SelectorEventLoop):
    def __init__(self):
        super().__init__(selector=OfflineSelector())

    def _make_self_pipe(self):
        self._ssock = self._csock = None

    def _write_to_self(self):
        pass  # Thread completions are polled by OfflineSelector every 5 ms.

    def _close_self_pipe(self):
        pass


def png(width=40, height=40):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff" * width) * height)) + chunk(b"IEND", b""))


class Points:
    def reshape(self, *args):
        assert args == (4, 2)
        return self

    def tolist(self):
        return [[4.0, 4.0], [24.0, 4.0], [24.0, 24.0], [4.0, 24.0]]


class Cameras:
    def __init__(self):
        self.calls = []
        self.packet = (7, time.time(), png())

    def stream_frame(self, stream, session):
        self.calls.append((stream, session))
        return self.packet if session == "existing" else None

    def __getattr__(self, name):
        raise AssertionError(f"Camera operation forbidden: {name}")


def stubs(legacy=False, empty=False):
    def detect(gray):
        assert gray.shape == (40, 40)
        return ([], None, []) if empty else ([Points()], SimpleNamespace(flatten=lambda: [12]), [])
    aruco = SimpleNamespace(**{name: i for i, name in enumerate(vision.DICTIONARIES)},
                            getPredefinedDictionary=lambda value: value)
    if legacy:
        aruco.DetectorParameters_create = lambda: object()
        aruco.detectMarkers = lambda gray, dictionary, parameters: detect(gray)
    else:
        aruco.DetectorParameters = lambda: object()
        aruco.ArucoDetector = lambda *args: SimpleNamespace(detectMarkers=detect)
    cv = SimpleNamespace(aruco=aruco, IMREAD_GRAYSCALE=0, IMREAD_IGNORE_ORIENTATION=128,
                         imdecode=lambda data, flags: SimpleNamespace(shape=(40, 40)))
    np = SimpleNamespace(uint8=object(), frombuffer=lambda data, dtype: data)
    return cv, np


async def call(router, path="detect", value=None, chunks=None, content_type="application/json"):
    # In-process ASGI, no TestClient, HTTP transport, server or robot world.
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    body = json.dumps(value).encode() if chunks is None else b""
    queue = list(chunks) if chunks is not None else [body]
    messages = []

    async def receive():
        data = queue.pop(0)
        return {"type": "http.request", "body": data, "more_body": bool(queue)}

    async def send(message):
        messages.append(message)

    await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
               "method": "GET" if path == "capabilities" else "POST", "scheme": "http",
               "path": "/api/vision/markers/" + path, "raw_path": b"/", "query_string": b"",
               "headers": [(b"content-type", content_type.encode())], "server": ("stub", 80), "client": ("stub", 1)}, receive, send)
    return messages[0]["status"], json.loads(b"".join(m.get("body", b"") for m in messages[1:]))


async def guard_checks():
    sys.addaudithook(forbid)
    camera = Cameras()
    router = vision.create_vision_router(world=object(), cameras=camera)
    upload = dict(source="upload", image_base64=base64.b64encode(png()).decode())
    with patch.object(vision, "_load_opencv", return_value=stubs()):
        status, cap = await call(router, "capabilities")
        assert status == 200 and cap["available"] and not camera.calls
        status, result = await call(router, value=upload)
        assert status == 200 and result["markers"] == [{"id": 12, "corners": Points().tolist(), "centroid": [14., 14.]}]
        assert result["coordinate_space"] == "image_pixels" and not result["calibrated"] and result["read_only"]
        assert not camera.calls
        status, result = await call(router, value=dict(source="camera", session="existing", stream="color"))
        assert status == 200 and result["frame"]["seq"] == 7 and camera.calls == [("color", "existing")]
        camera.packet = (7, time.time() - 10, png())
        assert (await call(router, value=dict(source="camera", session="existing")))[0] == 409
        assert (await call(router, value=dict(source="camera", session="wrong")))[0] == 409
        assert (await call(vision.create_vision_router(), value=dict(source="camera", session="existing")))[0] == 409
        cases = [(None, 400), ([], 400), ({"source": []}, 400),
                 ({**upload, "url": "http://127.0.0.1"}, 400), ({**upload, "path": "C:/private"}, 400),
                 ({**upload, "dictionary": "DICT_UNKNOWN"}, 400),
                 ({**upload, "image_base64": "%%%"}, 400),
                 ({**upload, "image_base64": base64.b64encode(b"not image").decode()}, 415),
                 ({**upload, "image_base64": base64.b64encode(png(2049, 1)).decode()}, 413),
                 ({**upload, "image_base64": base64.b64encode(png(1500, 1500)).decode()}, 413),
                 ({**upload, "image_base64": "a" * (vision.MAX_BODY_BYTES + 1)}, 413),
                 ({"source": "camera", "session": "existing", "stream": "depth"}, 400)]
        for value, expected in cases:
            code, result = await call(router, value=value)
            assert code == expected, (value and str(value)[:80], code, result)
        assert (await call(router, value=upload, content_type="image/png"))[0] == 415
        assert (await call(router, chunks=[b"{broken"]))[0] == 400
        assert (await call(router, chunks=[b" " * vision.MAX_BODY_BYTES, b"x"]))[0] == 413
        with patch.object(vision, "_load_opencv", return_value=stubs(legacy=True)):
            assert (await call(router, value=upload))[1]["markers"][0]["id"] == 12
        with patch.object(vision, "_load_opencv", return_value=stubs(empty=True)):
            assert (await call(router, value=upload))[1]["status"] == "empty"
        cv, np = stubs()
        cv.imdecode = lambda *args: None
        with patch.object(vision, "_load_opencv", return_value=(cv, np)):
            assert (await call(router, value=upload))[0] == 422
        cv.imdecode = lambda *args: (_ for _ in ()).throw(AssertionError("Oversized header must not reach decoder"))
        with patch.object(vision, "_load_opencv", return_value=(cv, np)):
            oversized = {**upload, "image_base64": base64.b64encode(png(2049, 1)).decode()}
            assert (await call(router, value=oversized))[0] == 413
        cv.imdecode = lambda *args: (_ for _ in ()).throw(RuntimeError("native failure"))
        with patch.object(vision, "_load_opencv", return_value=(cv, np)):
            assert (await call(router, value=upload))[1]["status"] == "detection_error"
        assert (await call(router, value=upload))[0] == 200  # Failure released slot.
    # Verify actual optional-import behavior without importing OpenCV or NumPy.
    def no_cv(name, *args, **kwargs):
        if name in {"cv2", "numpy"}:
            raise ImportError("stub missing dependency")
        return safe_import(name, *args, **kwargs)
    with patch("builtins.__import__", side_effect=no_cv):
        code, result = await call(router, "capabilities")
        assert code == 503 and result["status"] == "capability_unavailable" and result["available"] is False
    with patch("builtins.__import__", side_effect=lambda name, *a, **kw: SimpleNamespace() if name in {"cv2", "numpy"} else safe_import(name, *a, **kw)):
        assert (await call(router, "capabilities"))[0] == 503
    # Cancellation must not allow a second native task to overlap.
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    loop_thread = threading.get_ident()

    def blocking(*args):
        assert threading.get_ident() != loop_thread
        started.set()
        try:
            assert release.wait(5)
            return dict(ok=True)
        finally:
            finished.set()

    with patch.object(vision, "_work", side_effect=blocking):
        first = asyncio.create_task(call(router, "capabilities"))
        try:
            for _ in range(200):
                if started.is_set():
                    break
                await asyncio.sleep(.005)
            assert started.is_set()
            assert (await call(vision.create_vision_router(), "capabilities"))[0] == 429
            first.cancel()
            try:
                await first
            except asyncio.CancelledError:
                pass
            assert (await call(router, "capabilities"))[0] == 429
        finally:
            release.set()
        for _ in range(200):
            if finished.is_set() and not vision._SLOT.locked():
                break
            await asyncio.sleep(.005)
        assert finished.is_set() and not vision._SLOT.locked()
    # Metadata is stripped while encoded pixels remain intact.
    jpeg = b"\xff\xd8\xff\xe1\x00\x08Exif00\xff\xc0\x00\x0b\x08\x00\x28\x00\x28\x01\x01\x11\x00\xff\xda\x00\x02payload\xff\xd9"
    assert vision._dimensions(jpeg) == (40, 40, "image/jpeg")
    assert b"Exif" not in vision._preview_bytes(jpeg, "image/jpeg")
    assert vision._dimensions(png(2048, 1)) == (2048, 1, "image/png")
    print("PASS guard: routes, capability unavailable, modern/legacy API, geometry, empty/error, upload limits, no URL/path, existing/stale camera, worker thread, concurrency and cancellation")


def real_checks():
    global ALLOW_REAL
    ALLOW_REAL = True
    try:
        cv, np = vision._load_opencv()
    except vision.VisionError as exc:
        print("SKIP real:", exc.why)
        return
    dictionary = cv.aruco.getPredefinedDictionary(cv.aruco.DICT_4X4_50)
    generate = getattr(cv.aruco, "generateImageMarker", None) or getattr(cv.aruco, "drawMarker", None)
    if generate is None:
        print("SKIP real: no synthetic marker generator")
        return
    image = np.full((240, 240), 255, dtype=np.uint8)
    image[40:200, 40:200] = generate(dictionary, 12, 160)
    for extension in (".png", ".jpg"):
        ok, encoded = cv.imencode(extension, image)
        assert ok
        result = vision._work(dict(source="upload", image_base64=base64.b64encode(encoded).decode()), None)
        assert [m["id"] for m in result["markers"]] == [12]
        assert all(abs(v - 119.5) < 2 for v in result["markers"][0]["centroid"])
    print("PASS real: synthetic PNG/JPEG marker ID and centroid; no hardware")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guard", action="store_true", help="Pure stub mode (default)")
    parser.add_argument("--real", action="store_true", help="Also exercise existing OpenCV on synthetic memory images")
    options = parser.parse_args()
    if options.guard and options.real:
        parser.error("--guard and --real are mutually exclusive")
    with asyncio.Runner(loop_factory=OfflineLoop) as runner:
        runner.run(guard_checks())
    if options.real:
        real_checks()
