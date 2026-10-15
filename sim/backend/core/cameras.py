"""真实相机采集：原始图像、深度测距与 SDK 点云，不生成模拟画面。"""
from __future__ import annotations
import copy
import hashlib
import os
import secrets
import threading
import time

class CameraManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._operations = threading.RLock()
        self._active = None
        self._last_scan = []
        self._last_error = ""
        self._thread = None
        self._stop = None
        self._images = {}
        self._image_stamps = {}
        self._depth_at = 0
        self._cloud = None
        self._depth = None
        self._intrinsics = None
        self._scale = None

    @staticmethod
    def _import_rs():
        try:
            import pyrealsense2 as rs
            return rs
        except (ImportError, OSError):
            return None

    @staticmethod
    def _import_cv2():
        try:
            os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
            import cv2
            return cv2
        except (ImportError, OSError):
            return None

    def scan(self):
        with self._operations:
            devices, errors = [], []
            rs, cv = self._import_rs(), self._import_cv2()
            if rs:
                try:
                    for dev in rs.context().query_devices():
                        info = lambda key: dev.get_info(key) if dev.supports(key) else None
                        streams, profiles = set(), []
                        for sensor in dev.query_sensors():
                            for p in sensor.get_stream_profiles():
                                kind = str(p.stream_type()).split(".")[-1].lower()
                                streams.add(kind)
                                row = {"stream": kind, "index": p.stream_index(), "fps": p.fps(), "format": str(p.format())}
                                if p.is_video_stream_profile():
                                    vp = p.as_video_stream_profile()
                                    row.update(width=vp.width(), height=vp.height())
                                profiles.append(row)
                        serial = info(rs.camera_info.serial_number)
                        devices.append({"id": "realsense:" + serial, "kind": "realsense",
                            "name": info(rs.camera_info.name), "serial": serial,
                            "firmware": info(rs.camera_info.firmware_version),
                            "usb": info(rs.camera_info.usb_type_descriptor),
                            "streams": sorted(streams), "profiles": profiles, "backend": "pyrealsense2"})
                except Exception as e:
                    errors.append("RealSense 扫描失败：" + str(e))
            else:
                errors.append("RealSense SDK 未安装")
            if cv:
                try:
                    from cv2_enumerate_cameras import enumerate_cameras
                    # 同一后端的设备路径与索引直接匹配，不依赖扫描 0..7 的猜测。
                    backend = cv.CAP_DSHOW if os.name == "nt" else cv.CAP_ANY
                    seen = set()
                    for c in enumerate_cameras(backend):
                        identity = c.path or str(c.index)
                        if identity in seen:
                            continue
                        seen.add(identity)
                        devices.append({"id": "usb:" + hashlib.sha256(identity.encode()).hexdigest()[:16],
                            "kind": "usb", "name": c.name or '设备未提供名称', "index": c.index, "path": c.path,
                            "vid": c.vid, "pid": c.pid, "capture_backend": c.backend,
                            "streams": ["color"], "backend": "opencv"})
                except Exception as e:
                    errors.append("系统相机枚举失败：" + str(e))
            else:
                errors.append("OpenCV 未安装")
            for index, device in enumerate(devices, 1):
                device["number"] = index
            with self._lock:
                self._last_scan = devices
                self._last_error = "；".join(errors)
            return self.snapshot()

    def open(self, camera_id, streams=None):
        with self._operations:
            if not self._last_scan:
                self.scan()
            device = next((d for d in self._last_scan if d["id"] == camera_id), None)
            if not device:
                return {"ok": False, "why": "设备已移除，请刷新列表", **self.snapshot()}
            wanted = list(dict.fromkeys(streams if streams is not None else [s for s in ('color','depth') if s in device['streams']]))
            if not wanted or any(s not in device["streams"] for s in wanted):
                return {"ok": False, "why": "所选设备不支持这些流", **self.snapshot()}
            closed = self.close()
            if not closed["ok"]:
                return closed
            handle = None
            try:
                cv = self._import_cv2()
                if not cv:
                    raise RuntimeError("OpenCV 未安装，无法编码真实图像")
                if device["kind"] == "realsense":
                    rs = self._import_rs()
                    handle, cfg = rs.pipeline(), rs.config()
                    cfg.enable_device(device["serial"])
                    for stream in wanted:
                        if stream == "infrared":
                            for channel in sorted({p["index"] for p in device["profiles"] if p["stream"] == "infrared"}):
                                cfg.enable_stream(rs.stream.infrared, channel)
                        else:
                            cfg.enable_stream(getattr(rs.stream, stream))
                    profile = handle.start(cfg)
                    metadata = {"profiles": []}
                    for p in profile.get_streams():
                        row = {"stream": str(p.stream_type()).split(".")[-1], "fps": p.fps(), "index": p.stream_index()}
                        if p.is_video_stream_profile():
                            vp = p.as_video_stream_profile()
                            row.update(width=vp.width(), height=vp.height())
                        metadata["profiles"].append(row)
                    if "depth" in wanted:
                        metadata["depth_scale_m"] = profile.get_device().first_depth_sensor().get_depth_scale()
                else:
                    handle = cv.VideoCapture(device["index"], device["capture_backend"])
                    if not handle.isOpened():
                        raise RuntimeError("无法打开相机：请检查占用程序或 Windows 摄像头隐私权限")
                    metadata = {}
                stop = threading.Event()
                with self._lock:
                    self._stop = stop
                    self._active = {"id": device["id"], "name": device["name"], "kind": device["kind"],
                        "number": device["number"], "session": secrets.token_hex(8), "streams": wanted,
                        "opened_at": time.time(), "state": "starting", "seq": 0, "last_frame_at": None,
                        "images": {}, "imu": {}, **metadata}
                    self._last_error = ""
                self._thread = threading.Thread(target=self._capture, args=(device, handle, wanted, stop), daemon=True)
                self._thread.start()
                return {"ok": True, **self.snapshot()}
            except Exception as e:
                if handle is not None:
                    try:
                        handle.stop() if device["kind"] == "realsense" else handle.release()
                    except Exception:
                        pass
                self._last_error = str(e)
                return {"ok": False, "why": self._last_error, **self.snapshot()}

    def _capture(self, device, handle, wanted, stop):
        try:
            import numpy as np
            cv, rs = self._import_cv2(), self._import_rs()
            cloud = rs.pointcloud() if device["kind"] == "realsense" and "depth" in wanted else None
            colorizer = rs.colorizer() if cloud else None
            last_image = last_cloud = 0.0
            last_received, lost = time.monotonic(), 0
            while not stop.is_set():
                now = time.monotonic()
                images, motion, depth, intrinsics, point_data = {}, {}, None, None, None
                if device["kind"] == "usb":
                    ok, image = handle.read()
                    if not ok:
                        lost += 1
                        if lost >= 10: raise RuntimeError("相机取帧失败或设备已断开")
                        stop.wait(.05)
                        continue
                    lost = 0
                    frames = {"color": image}
                else:
                    frame_set = handle.poll_for_frames()
                    if not frame_set:
                        if now - last_received > 5: raise RuntimeError("RealSense 超过 5 秒没有有效帧")
                        stop.wait(.005)
                        continue
                    last_received = now
                    frames, color_frame, depth_frame = {}, frame_set.get_color_frame(), frame_set.get_depth_frame()
                    for frame in frame_set:
                        if frame.is_motion_frame():
                            value = frame.as_motion_frame().get_motion_data()
                            name = str(frame.profile.stream_type()).split(".")[-1]
                            motion[name] = {"xyz": [value.x, value.y, value.z],
                                            "unit": "rad/s" if name == "gyro" else "m/s²", "timestamp_ms": frame.get_timestamp()}
                    if color_frame:
                        raw = np.asanyarray(color_frame.get_data())
                        fmt = color_frame.profile.format()
                        frames["color"] = cv.cvtColor(raw, cv.COLOR_RGB2BGR) if fmt == rs.format.rgb8 else raw
                    if depth_frame:
                        depth = np.asanyarray(depth_frame.get_data()).copy()
                        intrinsics = depth_frame.profile.as_video_stream_profile().get_intrinsics()
                        frames["depth"] = cv.cvtColor(np.asanyarray(colorizer.colorize(depth_frame).get_data()), cv.COLOR_RGB2BGR)
                    for channel in (1, 2):
                        f = frame_set.get_infrared_frame(channel)
                        if f: frames["infrared" if channel == 1 else "infrared2"] = np.asanyarray(f.get_data())
                    if depth_frame and now - last_cloud >= .25:
                        if color_frame: cloud.map_to(color_frame)
                        points = cloud.calculate(depth_frame)
                        vertices = np.asanyarray(points.get_vertices()).view(np.float32).reshape(-1, 3)
                        stride = max(1, (len(vertices) + 5999) // 6000)
                        indices = np.arange(0, len(vertices), stride)
                        sampled = vertices[indices]
                        valid = np.isfinite(sampled).all(axis=1) & (sampled[:, 2] > 0)
                        sampled, indices = sampled[valid], indices[valid]
                        colors = None
                        if color_frame and len(sampled):
                            uv = np.asanyarray(points.get_texture_coordinates()).view(np.float32).reshape(-1, 2)[indices]
                            raw = frames["color"]
                            x = np.clip((uv[:, 0] * raw.shape[1]).astype(int), 0, raw.shape[1] - 1)
                            y = np.clip((uv[:, 1] * raw.shape[0]).astype(int), 0, raw.shape[0] - 1)
                            colors = (raw[y, x][:, ::-1] / 255.0).round(3).tolist()
                        point_data = {"xyz_m": sampled.round(5).tolist(), "rgb": colors,
                            "coordinate_frame": "camera_optical", "unit": "m", "timestamp": time.time(), "source": "realsense_sdk"}
                        last_cloud = now
                if now - last_image >= .1:
                    for stream, image in frames.items():
                        ok, encoded = cv.imencode(".jpg", image, [int(cv.IMWRITE_JPEG_QUALITY), 80])
                        if ok: images[stream] = (encoded.tobytes(), image.shape[1], image.shape[0])
                    last_image = now
                with self._lock:
                    if stop is not self._stop or stop.is_set(): break
                    if images:
                        self._images.update({k: v[0] for k, v in images.items()})
                        self._image_stamps.update({k: time.time() for k in images})
                        self._active["images"] = {k: {"width": v[1], "height": v[2]} for k, v in images.items()}
                    if depth is not None:
                        self._depth, self._intrinsics = depth, intrinsics
                        self._depth_at = time.time()
                        self._scale = self._active["depth_scale_m"]
                    if point_data is not None: self._cloud = point_data
                    self._active["imu"].update(motion)
                    if images or motion:
                        self._active.update(state="streaming", seq=self._active["seq"] + 1, last_frame_at=time.time())
                if device["kind"] == "usb": stop.wait(.01)
        except Exception as e:
            with self._lock:
                if stop is self._stop:
                    self._last_error = str(e)
                    if self._active: self._active.update(state="error", error=str(e))
                    self._images.clear()
                    self._cloud = self._depth = None
        finally:
            try: handle.stop() if device["kind"] == "realsense" else handle.release()
            except Exception: pass

    def close(self):
        with self._operations:
            if self._stop: self._stop.set()
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=3)
                if self._thread.is_alive():
                    return {"ok": False, "why": "设备正在释放，请稍后再试", **self.snapshot()}
            with self._lock:
                self._active = self._thread = self._stop = None
                self._images = {}
                self._image_stamps = {}
                self._depth_at = 0
                self._cloud = self._depth = self._intrinsics = self._scale = None
            return {"ok": True, **self.snapshot()}

    def image(self, stream, session):
        with self._lock:
            if not self._active or session != self._active["session"]: return None
            if self._active["state"] != "streaming" or time.time() - self._image_stamps.get(stream, 0) > 3: return None
            return self._images.get(stream)

    def pointcloud(self, session):
        with self._lock:
            if not self._active or session != self._active["session"] or not self._cloud:
                return {"ok": False, "why": "没有有效 RealSense 深度点云"}
            if time.time() - self._cloud["timestamp"] > 3: return {"ok": False, "why": "深度点云已过期"}
            return {"ok": True, **copy.deepcopy(self._cloud)}

    def measure(self, x, y, session):
        with self._lock:
            if not self._active or self._active["session"] != session or self._depth is None:
                return {"ok": False, "why": "没有有效深度帧"}
            if time.time() - self._depth_at > 3:
                return {"ok": False, "why": "深度数据已过期"}
            h, w = self._depth.shape
            if not (0 <= x < w and 0 <= y < h): return {"ok": False, "why": "像素坐标超出图像"}
            distance = float(self._depth[y, x]) * self._scale
            if distance <= 0: return {"ok": False, "why": "此像素没有有效深度"}
            xyz = self._import_rs().rs2_deproject_pixel_to_point(self._intrinsics, [x, y], distance)
            return {"ok": True, "pixel": [x, y], "distance_mm": round(distance * 1000, 2),
                    "xyz_mm": [round(v * 1000, 2) for v in xyz], "frame": "camera_optical"}

    def snapshot(self):
        with self._lock:
            return {"devices": copy.deepcopy(self._last_scan), "active": copy.deepcopy(self._active),
                "sdk": {"realsense": self._import_rs() is not None, "opencv": self._import_cv2() is not None},
                "error": self._last_error}
