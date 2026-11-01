"""真实相机采集：原始图像、深度测距与 SDK 点云，不生成模拟画面。"""
from __future__ import annotations
import copy
from collections import deque
import hashlib
import json
import math
import os
import secrets
import subprocess
import threading
import time
from importlib.util import find_spec

class CameraManager:
    @staticmethod
    def _sdk_available(name):
        """状态读取只检查安装信息，不在事件循环中加载大型原生 SDK。"""
        try:
            return find_spec(name) is not None
        except (ImportError, ValueError, OSError):
            return False

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
        self._image_seq = {}
        self._raw_color = None
        # 原始采集单调时间由服务端保存，不能用客户端报告的年龄重新计时。
        self._frame_history = {}
        self._frame_times = {}
        self._frame_counts = {}
        self._image_times = {}
        self._capture_costs = deque(maxlen=120)
        self._cloud_requested_until = 0.0
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

    @staticmethod
    def _windows_instance(path):
        """Keep the complete instance, including interface index; never guess by VID/name."""
        value = str(path or "").strip().lower()
        if value.startswith('\\\\?\\'):
            parts = value[4:].split('#')
            if len(parts) >= 3 and parts[0] == 'usb':
                return '\\'.join(parts[:3])
        if value.startswith('usb\\'):
            return value
        return None

    @classmethod
    def _physical_ids(cls, paths):
        """Resolve Windows interface paths to their actual USB composite parent via PnP."""
        instances = {p: cls._windows_instance(p) for p in paths if p}
        if os.name != 'nt' or not any(instances.values()):
            return {}
        script = r'''
$ErrorActionPreference = 'Stop'
$result = @{}
foreach ($id in ([Console]::In.ReadToEnd() | ConvertFrom-Json)) {
    try {
        $current = $id
        for ($i = 0; $i -lt 8; $i++) {
            $device = Get-PnpDevice -InstanceId $current -ErrorAction Stop
            $current = $device.InstanceId
            if ($current -match '^USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}\\') {
                $result[$id] = $current.ToLowerInvariant()
                break
            }
            $current = (Get-PnpDeviceProperty -InstanceId $current -KeyName 'DEVPKEY_Device_Parent' -ErrorAction Stop).Data
            if (-not $current) { break }
        }
    } catch { }
}
$result | ConvertTo-Json -Compress
'''
        try:
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                input=json.dumps(sorted({v for v in instances.values() if v})),
                text=True, capture_output=True, timeout=10,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=True)
            resolved = json.loads(result.stdout.strip().lstrip('\ufeff'))
            return {p: resolved[v] for p, v in instances.items() if v in resolved}
        except (OSError, ValueError, subprocess.SubprocessError):
            return {}

    @classmethod
    def _link_sdk_aliases(cls, devices, physical_ids=None):
        paths = [d.get('physical_port') if d['kind'] == 'realsense' else d.get('path') for d in devices]
        identities = cls._physical_ids(paths) if physical_ids is None else physical_ids
        for device in devices:
            path = device.get('physical_port') if device['kind'] == 'realsense' else device.get('path')
            device['physical_id'] = identities.get(path)
        sdk = [d for d in devices if d['kind'] == 'realsense']
        for device in devices:
            if device['kind'] != 'usb':
                continue
            matches = [d for d in sdk if device['physical_id'] and d['physical_id'] == device['physical_id']]
            if len(matches) == 1:
                parent = matches[0]
                device.update(alias_of=parent['id'], streams=list(parent['streams']),
                    profiles=copy.deepcopy(parent['profiles']), backend='pyrealsense2',
                    alias_note='与 SDK 入口属于同一物理设备；通过 SDK 显式选择流')
            elif matches:
                device['open_error'] = '物理设备对应多个 SDK 标识，无法安全路由，请重新扫描'
            elif device.get('vid') == 0x8086:
                # VID is only a reason to refuse an unsafe fallback, never a match.
                device['open_error'] = '未能唯一核验此 Intel UVC 入口与 SDK physical_port 的物理归属；拒绝以普通彩色流代替深度流'

    @staticmethod
    def _select_profiles(device, wanted, target_fps):
        selected = []
        usb2 = str(device.get('usb') or '').startswith('2')
        for stream in wanted:
            candidates = [p for p in device.get('profiles', []) if p['stream'] == stream]
            formats = {'color': {'bgr8', 'rgb8'}, 'depth': {'z16'}, 'infrared': {'y8'}}
            if stream in formats:
                candidates = [p for p in candidates if p['format'].split('.')[-1].lower() in formats[stream]]
            channels = sorted({p['index'] for p in candidates})
            if not channels:
                raise ValueError(f'{stream} 没有可用的已公布 profile（彩色只接受 RGB/BGR8）')
            for channel in channels:
                rows = [p for p in candidates if p['index'] == channel]
                below = [p for p in rows if p['fps'] <= target_fps]
                rows = below or rows
                # USB2's published 424x240 BGR8@6 is preferred when present;
                # advertisement is not evidence that a stream actually delivers frames.
                known = [p for p in rows if stream == 'color' and p.get('width') == 424
                         and p.get('height') == 240 and p['fps'] == 6 and p['format'].split('.')[-1] == 'bgr8']
                if usb2 and known:
                    chosen = known[0]
                else:
                    chosen = min(rows, key=lambda p: (
                        p['fps'] if usb2 or not below else -p['fps'],
                        p.get('width', 0) * p.get('height', 0),
                        p['format'].split('.')[-1] != 'bgr8'))
                selected.append(dict(chosen))
        return selected

    @staticmethod
    def _configure_usb(handle, cv, fps):
        """Request a fast UVC format/rate without inventing supported profiles.

        OpenCV property acknowledgements and get() values are driver reports,
        not proof that hardware frames arrive at that rate. Keep those fields
        separate from measured counts, and leave exposure/resolution untouched.
        """
        def read(name):
            key = getattr(cv, name, None)
            if key is None:
                return None
            try:
                value = float(handle.get(key))
                return value if math.isfinite(value) and value > 0 else None
            except Exception:
                return None

        def request(name, value):
            key = getattr(cv, name, None)
            if key is None or not hasattr(handle, "set"):
                return False
            try:
                return bool(handle.set(key, value))
            except Exception:
                # Unsupported properties must not remove an otherwise usable
                # camera. The requested/accepted/measured fields expose fallback.
                return False

        mjpg = cv.VideoWriter_fourcc(*"MJPG") if hasattr(cv, "VideoWriter_fourcc") else None
        format_ok = request("CAP_PROP_FOURCC", mjpg) if mjpg is not None else False
        fps_ok = request("CAP_PROP_FPS", fps)
        fallback_fps = None
        if not fps_ok and fps > 30 and request("CAP_PROP_FPS", 30):
            fallback_fps = 30
        # Some DirectShow drivers reset FOURCC while changing frame interval.
        # Reapply compression last; its readback (not set()'s boolean) reveals
        # whether the device really selected MJPG or kept uncompressed YUY2.
        if mjpg is not None and read("CAP_PROP_FOURCC") != mjpg:
            format_ok = request("CAP_PROP_FOURCC", mjpg)
        request("CAP_PROP_BUFFERSIZE", 1)
        code = read("CAP_PROP_FOURCC")
        fourcc = ''.join(chr((int(code) >> (8*i)) & 255) for i in range(4)) if code else None
        if fourcc and not all(32 <= ord(c) < 127 for c in fourcc):
            fourcc = None
        return {"profiles": [], "negotiated_fps": {"color": None},
                "driver_reported_fps": read("CAP_PROP_FPS"),
                "requested_fps": fps, "fps_request_accepted": fps_ok,
                "fallback_fps_request": fallback_fps,
                "requested_format": "MJPG", "format_request_accepted": format_ok,
                "driver_reported_format": fourcc,
                "driver_reported_size": [read("CAP_PROP_FRAME_WIDTH"), read("CAP_PROP_FRAME_HEIGHT")],
                "profile_note": "已向USB驱动请求MJPG和目标帧率；回报不等于实测。未改曝光和分辨率，actual_fps为真实取帧计数"}

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
                            "physical_port": info(rs.camera_info.physical_port),
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
            self._link_sdk_aliases(devices)
            for index, device in enumerate(devices, 1):
                device["number"] = index
            with self._lock:
                self._last_scan = devices
                self._last_error = "；".join(errors)
            return self.snapshot()

    def open(self, camera_id, streams=None, fps=60):
        with self._operations:
            if isinstance(fps, bool) or not isinstance(fps, int) or not 1 <= fps <= 60:
                return {"ok": False, "why": "fps 必须是 1..60 的整数", **self.snapshot()}
            if not self._last_scan:
                self.scan()
            device = next((d for d in self._last_scan if d["id"] == camera_id), None)
            if not device:
                return {"ok": False, "why": "设备已移除，请刷新列表", **self.snapshot()}
            requested_device = device
            if device.get('open_error'):
                return {"ok": False, "why": device['open_error'], **self.snapshot()}
            if device.get('alias_of'):
                device = next((d for d in self._last_scan if d['id'] == device['alias_of']), None)
                if not device:
                    return {"ok": False, "why": "SDK 物理设备已移除，请重新扫描", **self.snapshot()}
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
                    if not rs:
                        raise RuntimeError('RealSense SDK 未安装或加载失败')
                    handle, cfg = rs.pipeline(), rs.config()
                    cfg.enable_device(device["serial"])
                    selected = self._select_profiles(device, wanted, fps)
                    for row in selected:
                        stream, fmt = getattr(rs.stream, row['stream']), getattr(rs.format, row['format'].split('.')[-1])
                        if 'width' in row:
                            cfg.enable_stream(stream, row['index'], row['width'], row['height'], fmt, row['fps'])
                        else:
                            cfg.enable_stream(stream, row['index'], fmt, row['fps'])
                    profile = handle.start(cfg)
                    metadata = {"profiles": []}
                    for p in profile.get_streams():
                        row = {"stream": str(p.stream_type()).split(".")[-1], "fps": p.fps(), "index": p.stream_index(), "format": str(p.format())}
                        if p.is_video_stream_profile():
                            vp = p.as_video_stream_profile()
                            row.update(width=vp.width(), height=vp.height())
                        metadata["profiles"].append(row)
                    if metadata['profiles'] != selected:
                        key = lambda p: (p['stream'], p['index'], p['fps'], p['format'].split('.')[-1], p.get('width'), p.get('height'))
                        if sorted(map(key, metadata['profiles'])) != sorted(map(key, selected)):
                            raise RuntimeError('SDK 协商结果不符合已选 profile，拒绝替换或丢弃请求流')
                    metadata['negotiated_fps'] = {self._stream_key(p): p['fps'] for p in metadata['profiles']}
                    if "depth" in wanted:
                        metadata["depth_scale_m"] = profile.get_device().first_depth_sensor().get_depth_scale()
                else:
                    handle = cv.VideoCapture(device["index"], device["capture_backend"])
                    if not handle.isOpened():
                        raise RuntimeError("无法打开相机：请检查占用程序或 Windows 摄像头隐私权限")
                    metadata = self._configure_usb(handle, cv, fps)
                stop = threading.Event()
                with self._lock:
                    self._stop = stop
                    self._active = {"id": requested_device["id"], "name": requested_device["name"], "kind": device["kind"],
                        "physical_device_id": device['id'], "target_fps": fps, "actual_fps": 0.0,
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

    @staticmethod
    def _stream_key(profile):
        name = profile['stream']
        return 'infrared2' if name == 'infrared' and profile['index'] == 2 else name

    def _record_frames(self, streams, now):
        """Called only for new hardware frame numbers (USB: successful read)."""
        for stream in streams:
            times = self._frame_times.setdefault(stream, deque(maxlen=600))
            times.append(now)
            while len(times) > 2 and times[0] < now - 2:
                times.popleft()
            self._frame_counts[stream] = self._frame_counts.get(stream, 0) + 1

    @staticmethod
    def _check_stream_timeout(expected, received, started, now):
        missing = [s for s in expected if now - received.get(s, started) > 5]
        if missing:
            raise RuntimeError('请求流超过 5 秒没有有效帧：' + ', '.join(missing)
                + '；请检查 USB 带宽/链路、驱动与 profile；未生成替代画面或丢弃请求流')

    def _capture(self, device, handle, wanted, stop):
        try:
            import numpy as np
            cv, rs = self._import_cv2(), self._import_rs()
            cloud = None
            colorizer = rs.colorizer() if device['kind'] == 'realsense' and 'depth' in wanted else None
            last_image, seen, received = {}, {}, {}
            last_cloud, started, lost = 0.0, time.monotonic(), 0
            with self._lock:
                target_fps = self._active['target_fps']
                expected = set(self._active.get('negotiated_fps', {})) or set(wanted)
            def fresh(frame, name):
                if not frame:
                    return False
                token = (frame.get_frame_number(), frame.get_timestamp())
                if seen.get(name) == token:
                    return False
                seen[name] = token
                return True
            while not stop.is_set():
                now = time.monotonic()
                read_started = time.perf_counter()
                encode_ms = 0.0
                images, motion, depth, intrinsics, point_data = {}, {}, None, None, None
                if device["kind"] == "usb":
                    ok, image = handle.read()
                    captured_at = time.time()
                    captured_mono = time.monotonic()
                    if not ok:
                        lost += 1
                        if lost >= 10: raise RuntimeError("相机取帧失败或设备已断开")
                        stop.wait(.05)
                        continue
                    lost = 0
                    frames = {"color": image}
                    now = time.monotonic()
                else:
                    frame_set = handle.poll_for_frames()
                    captured_at = time.time()
                    captured_mono = time.monotonic()
                    if not frame_set:
                        self._check_stream_timeout(expected, received, started, now)
                        stop.wait(.002)
                        continue
                    frames, color_frame, depth_frame = {}, frame_set.get_color_frame(), frame_set.get_depth_frame()
                    for frame in frame_set:
                        if frame.is_motion_frame():
                            name = str(frame.profile.stream_type()).split(".")[-1]
                            if not fresh(frame, name):
                                continue
                            value = frame.as_motion_frame().get_motion_data()
                            motion[name] = {"xyz": [value.x, value.y, value.z],
                                            "unit": "rad/s" if name == "gyro" else "m/s²", "timestamp_ms": frame.get_timestamp()}
                    if fresh(color_frame, 'color'):
                        raw = np.asanyarray(color_frame.get_data())
                        fmt = color_frame.profile.format()
                        frames["color"] = cv.cvtColor(raw, cv.COLOR_RGB2BGR) if fmt == rs.format.rgb8 else raw
                    if fresh(depth_frame, 'depth'):
                        depth = np.asanyarray(depth_frame.get_data()).copy()
                        intrinsics = depth_frame.profile.as_video_stream_profile().get_intrinsics()
                        frames["depth"] = cv.cvtColor(np.asanyarray(colorizer.colorize(depth_frame).get_data()), cv.COLOR_RGB2BGR)
                    for channel in (1, 2):
                        f = frame_set.get_infrared_frame(channel)
                        name = 'infrared' if channel == 1 else 'infrared2'
                        if fresh(f, name): frames[name] = np.asanyarray(f.get_data())
                    with self._lock:
                        want_cloud = now < self._cloud_requested_until
                    if depth is not None and want_cloud and now - last_cloud >= .25:
                        if cloud is None: cloud = rs.pointcloud()
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
                            raw = np.asanyarray(color_frame.get_data())
                            if color_frame.profile.format() == rs.format.rgb8:
                                raw = cv.cvtColor(raw, cv.COLOR_RGB2BGR)
                            x = np.clip((uv[:, 0] * raw.shape[1]).astype(int), 0, raw.shape[1] - 1)
                            y = np.clip((uv[:, 1] * raw.shape[0]).astype(int), 0, raw.shape[0] - 1)
                            colors = (raw[y, x][:, ::-1] / 255.0).round(3).tolist()
                        point_data = {"xyz_m": sampled.round(5).tolist(), "rgb": colors,
                            "coordinate_frame": "camera_optical", "unit": "m", "timestamp": time.time(), "source": "realsense_sdk"}
                        last_cloud = now
                read_ms = (time.perf_counter() - read_started) * 1000
                for name in (*frames, *motion):
                    received[name] = now
                self._check_stream_timeout(expected, received, started, now)
                for stream, image in frames.items():
                    deadline = last_image.get(stream, now)
                    if now < deadline:
                        continue
                    encode_started = time.perf_counter()
                    ok, encoded = cv.imencode(".jpg", image, [int(cv.IMWRITE_JPEG_QUALITY), 80])
                    encode_ms += (time.perf_counter() - encode_started) * 1000
                    if not ok:
                        raise RuntimeError(f'{stream} JPEG 编码失败')
                    images[stream] = (encoded.tobytes(), image.shape[1], image.shape[0])
                    # Advance a deadline instead of resetting an interval on jitter;
                    # resetting can accidentally halve a nominal 60fps input.
                    last_image[stream] = max(deadline + 1 / target_fps, now)
                with self._lock:
                    if stop is not self._stop or stop.is_set(): break
                    self._record_frames((*frames, *motion), now)
                    if frames:
                        self._capture_costs.append((read_ms, encode_ms))
                    if images:
                        self._images.update({k: v[0] for k, v in images.items()})
                        # Host read-return time, not exposure time: include all
                        # preprocessing/JPEG work in downstream observation age.
                        self._image_stamps.update({k: captured_at for k in images})
                        for k, v in images.items():
                            self._image_times.setdefault(k, deque(maxlen=600)).append(now)
                            self._image_seq[k] = self._image_seq.get(k, 0) + 1
                            self._frame_history.setdefault(k, deque(maxlen=512)).append(
                                (self._active['session'], self._image_seq[k], captured_mono))
                            self._active['images'][k] = {'width': v[1], 'height': v[2], 'seq': self._image_seq[k]}
                            if k == 'color':
                                # Reuse this capture for detection; never reopen
                                # the device or decode the preview JPEG.
                                rgb = frames[k][:, :, ::-1].copy()
                                rgb.setflags(write=False)
                                self._raw_color = (self._active['session'], self._image_seq[k], captured_mono, rgb)
                    if depth is not None:
                        self._depth, self._intrinsics = depth, intrinsics
                        self._depth_at = time.time()
                        self._scale = self._active["depth_scale_m"]
                    if point_data is not None: self._cloud = point_data
                    self._active["imu"].update(motion)
                    if images or motion:
                        # IMU arriving alone must not mark missing video as healthy.
                        ready = expected <= received.keys()
                        self._active.update(state='streaming' if ready else 'starting',
                            seq=self._active['seq'] + 1, last_frame_at=time.time())
        except Exception as e:
            with self._lock:
                if stop is self._stop:
                    self._last_error = str(e)
                    if self._active: self._active.update(state="error", error=str(e))
                    self._images.clear()
                    self._image_stamps.clear()
                    self._raw_color = None
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
                    # 线程仍在占用设备时不能继续对外声称 streaming：此时不会再交付
                    # 任何新帧（image/stream_frame 因 stop 已置位返回 None），旧状态
                    # 会让状态查询与预览各自描述不同的现实。
                    with self._lock:
                        if self._active:
                            self._active.update(state="error",
                                error="相机未能在 3 秒内释放，请检查 USB/驱动后重试并关闭预览")
                    return {"ok": False, "why": "设备正在释放，请稍后再试", **self.snapshot()}
            with self._lock:
                self._active = self._thread = self._stop = None
                self._images = {}
                self._image_stamps = {}
                self._image_seq = {}
                self._raw_color = None
                self._frame_history = {}
                self._frame_times = {}
                self._frame_counts = {}
                self._image_times = {}
                self._capture_costs.clear()
                self._cloud_requested_until = 0.0
                self._depth_at = 0
                self._cloud = self._depth = self._intrinsics = self._scale = None
            return {"ok": True, **self.snapshot()}

    def image(self, stream, session):
        with self._lock:
            if not self._active or session != self._active["session"]: return None
            if self._stop and self._stop.is_set(): return None
            if self._active["state"] != "streaming" or time.time() - self._image_stamps.get(stream, 0) > 3: return None
            return self._images.get(stream)

    def stream_frame(self, stream, session):
        """An immutable shared JPEG with its own sequence; consumers never encode."""
        with self._lock:
            data = self.image(stream, session)
            if data is None or (self._stop and self._stop.is_set()):
                return None
            return self._image_seq.get(stream, 0), self._image_stamps[stream], data

    def active_session(self):
        """当前仍在采集的会话号；相机关闭或被换走时返回 None。"""
        with self._lock:
            if not self._active or self._stop and self._stop.is_set(): return None
            if self._active.get("state") not in ("starting","streaming"): return None
            return self._active.get("session")

    def raw_frame_copy(self, session):
        """Read-only RGB copy of the existing capture; no camera operations."""
        with self._lock:
            packet = self._raw_color
            if (not packet or packet[0] != session or not self._active
                    or self._active.get('session') != session
                    or self._active.get('state') != 'streaming'
                    or self._stop and self._stop.is_set()
                    or not 0 <= time.monotonic() - packet[2] <= .75):
                return None
            image = packet[3].copy()
            image.setflags(write=False)
            return {'session': session, 'seq': packet[1], 'captured_at': packet[2], 'image': image}

    def observation_stamp(self, session, frame_seq):
        """返回服务端原始采集单调时间，不让处理耗时重新续期。"""
        with self._lock:
            if (not self._active or self._active.get("session") != session
                    or self._active.get("state") != "streaming"
                    or type(frame_seq) is not int or frame_seq <= 0
                    or (self._stop and self._stop.is_set())):
                return None
            for owner, sequence, captured in reversed(self._frame_history.get("color", ())):
                if owner == session and sequence == frame_seq:
                    return captured if math.isfinite(captured) else None
            return None

    def observation_age_ms(self, session, frame_seq):
        captured = self.observation_stamp(session, frame_seq)
        if captured is None:
            return None
        age = (time.monotonic() - captured) * 1000
        return age if math.isfinite(age) and age >= 0 else None

    def pointcloud(self, session):
        with self._lock:
            if self._active and session == self._active['session'] and self._active['state'] != 'error':
                if 'depth' in self._active.get('streams', []):
                    # Polling renews a short lease; closing the cloud UI stops the work.
                    self._cloud_requested_until = time.monotonic() + 2
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
            active = copy.deepcopy(self._active)
            if active:
                now = time.monotonic()
                rates = {}
                for stream in active.get('negotiated_fps', {}):
                    times = [t for t in self._frame_times.get(stream, ()) if now - t <= 2]
                    rates[stream] = round((len(times) - 1) / (times[-1] - times[0]), 2) if len(times) > 1 and times[-1] > times[0] else 0.0
                if active.get('state') == 'error' or (self._stop and self._stop.is_set()):
                    rates = dict.fromkeys(rates, 0.0)
                active['actual_fps_by_stream'] = rates
                delivered = {}
                for stream, stamps in self._image_times.items():
                    times = [t for t in stamps if now - t <= 2]
                    delivered[stream] = round((len(times)-1)/(times[-1]-times[0]), 2) if len(times)>1 and times[-1]>times[0] else 0.0
                if active.get('state') == 'error' or (self._stop and self._stop.is_set()):
                    delivered = dict.fromkeys(delivered, 0.0)
                active['preview_fps_by_stream'] = delivered
                if self._capture_costs:
                    active['capture_timing_ms'] = {
                        'read_mean': round(sum(row[0] for row in self._capture_costs)/len(self._capture_costs), 2),
                        'encode_mean': round(sum(row[1] for row in self._capture_costs)/len(self._capture_costs), 2)}
                primary = next((s for s in ('color', 'depth', 'infrared', 'gyro', 'accel') if s in rates), None)
                active['actual_fps'] = rates.get(primary, 0.0)
                active['frame_counts'] = dict(self._frame_counts)
            if active and active.get("state") == "starting" and time.time() - active.get("opened_at", time.time()) > 10:
                active.update(state="error", error="相机启动超过 10 秒仍无数据，请关闭后检查 USB 速率、设备占用及驱动")
            return {"devices": copy.deepcopy(self._last_scan), "active": active,
                "sdk": {"realsense": self._sdk_available('pyrealsense2'), "opencv": self._sdk_available('cv2')},
                "error": self._last_error}
