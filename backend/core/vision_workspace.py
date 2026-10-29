"""CPU工件视觉；只读相机、平面估测、临时跟踪，不引用机器人或持久场景。"""
from __future__ import annotations
import asyncio
import base64
import copy
import hashlib
import json
import math
import threading
import time
import secrets
from .storage import write_json

COLORS = ('red', 'yellow', 'green', 'cyan', 'blue', 'purple', 'white', 'black')
DEFAULTS = {'enabled': True, 'input': 'camera', 'colors': ['red', 'yellow', 'green', 'blue'],
            'min_area': 200, 'roi': [0.02, 0.02, 0.96, 0.96], 'height_mm': 20, 'mapping': False,
            'virtual':{'offset_mm':[-58.6,0,-6],'direction':[0,0,1000],'fov':55,'width':640,'fps':15}}


def finite(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'数值必须在 {low} 到 {high} 范围内')
    return float(value)


def settings(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError('视觉参数字段无效')
    result = copy.deepcopy(DEFAULTS)
    result.update(value)
    for key in ('enabled', 'mapping'):
        if type(result[key]) is not bool: raise ValueError(key + '需要布尔值')
    if result['input'] not in ('camera', 'image', 'virtual'): raise ValueError('图像来源无效')
    virtual=result['virtual']
    if not isinstance(virtual,dict) or set(virtual)!=set(DEFAULTS['virtual']): raise ValueError('仿真相机参数无效')
    virtual=copy.deepcopy(virtual)
    for key,bound in (('offset_mm',1000),('direction',10000)):
        if not isinstance(virtual[key],list) or len(virtual[key])!=3: raise ValueError('安装与朝向需三项')
        virtual[key]=[finite(value,-bound,bound) for value in virtual[key]]
    if math.hypot(*virtual['direction'])<1e-6: raise ValueError('相机朝向不能为零')
    virtual['fov']=finite(virtual['fov'],20,100)
    if type(virtual['width']) is not int or virtual['width'] not in (320,640,960): raise ValueError('仿真图像宽度无效')
    if type(virtual['fps']) is not int or not 1<=virtual['fps']<=30: raise ValueError('仿真采样帧率需为1—30')
    result['virtual']=virtual
    colors = result['colors']
    if not isinstance(colors, list) or not 1 <= len(colors) <= 8 or any(not isinstance(c, str) or c not in COLORS for c in colors):
        raise ValueError('请选择1—8种有效颜色')
    result['colors'] = list(dict.fromkeys(colors))
    result['min_area'] = finite(result['min_area'], 25, 1_000_000)
    result['height_mm'] = finite(result['height_mm'], 1, 1000)
    roi = result['roi']
    if not isinstance(roi, list) or len(roi) != 4: raise ValueError('ROI需要X/Y/宽/高四项比例')
    roi = [finite(v, 0, 1) for v in roi]
    if roi[2] < .05 or roi[3] < .05 or roi[0]+roi[2] > 1.000001 or roi[1]+roi[3] > 1.000001:
        raise ValueError('ROI需在画面内，宽高至少5%')
    result['roi'] = roi
    return result


class VisionWorkspace:
    def __init__(self, cameras, path=None):
        self.cameras, self.path = cameras, path
        self.lock = threading.RLock()
        self.operation = threading.Lock()
        self.config = copy.deepcopy(DEFAULTS)
        self.calibration = None
        self.latest = None
        self.tracks, self.next_id = {}, 0
        self.source_key, self.last_token = None, None
        self.revision, self.generation = 0, 0
        self.error, self.task = '', None
        self.image_data = None
        self.virtual_data = None
        self.virtual_seq = 0
        self.virtual_session = secrets.token_hex(8)
        self.instance_id = secrets.token_hex(16)
        if path and path.exists():
            try:
                if path.stat().st_size > 65536: raise ValueError('视觉配置过大')
                raw = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(raw,dict) or not isinstance(raw.get('config',{}),dict):raise ValueError('视觉配置需要对象')
                # 仅迁移旧版原始默认安装值；用户自定义偏移不得被覆盖。
                old_virtual=raw.get('config',{}).get('virtual',{})
                old_mounts=(([100,0,50],[400,0,-1000]),([0,0,25],[0,0,1000]),([69.6,0,-24.25],[0,1000,0]),([0,115.1,-24],[1000,0,0]),([72.1,0,-24.25],[1000,0,0]))
                if isinstance(old_virtual,dict) and raw.get('camera_model_version',1)<6 and any(old_virtual.get('offset_mm')==offset and old_virtual.get('direction')==direction for offset,direction in old_mounts):
                    old_virtual.update(offset_mm=[-58.6,0,-6],direction=[0,0,1000])
                self.config = settings(raw.get('config', {}))
                # 标定仅限当前进程及相机会话，重启后要求重新确认，不能静默恢复运动坐标。
            except (ValueError, OSError, TypeError) as exc:
                self.error = '视觉配置未加载：' + str(exc)

    def configure(self, patch):
        with self.lock:
            value = settings({**self.config, **patch})
            if value['input']=='virtual' and value['enabled'] and self.cameras.snapshot().get('active'):
                raise ValueError('真实摄像头尚未关闭，请先关闭后切换仿真相机')
            if self.path: write_json(self.path, {'config': value,'camera_model_version':6})
            if value['input']!=self.config['input'] or value['virtual']!=self.config['virtual']:
                self.virtual_session=secrets.token_hex(8);self.virtual_data=None
            self.config = value
            self.generation += 1
            self.last_token = None
            self.latest = None
            self.tracks.clear()
            self.revision += 1
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.latest)
            age = time.time() - result['captured_at'] if result else None
            stale = age is not None and (age < 0 or age > 1.5) and result['source'] in ('camera','virtual')
            if stale:
                for obj in result['objects']: obj['status'] = 'expired'
            return {'ok': True, 'instance_id':self.instance_id,'revision': self.revision, 'virtual_session':self.virtual_session,'config': copy.deepcopy(self.config),
                    'calibration': copy.deepcopy(self.calibration), 'result': result,
                    'error': self.error, 'stale': stale, 'read_only': True}

    def invalidate(self, message):
        with self.lock:
            if self.latest is not None or self.error != message:
                self.latest = None; self.tracks.clear(); self.revision += 1
            self.error = message

    def _input(self):
        if self.config['input']=='virtual':
            return self.virtual_data if self.virtual_data and 0<=time.time()-self.virtual_data[3]<=1.5 else None
        if self.config['input'] == 'image':
            return self.image_data
        active = self.cameras.snapshot().get('active')
        if not active or active.get('state') != 'streaming': return None
        packet = self.cameras.stream_frame('color', active['session'])
        if not packet: return None
        seq, stamp, data = packet
        if type(stamp) not in (int, float) or not math.isfinite(stamp) or not 0 <= time.time()-stamp <= .75:
            return None
        # 绑定相机会话：拔插、移动相机或重启后不可自动套用旧标定。
        key = 'camera:' + active['session']
        return data, key, seq, stamp, 'camera'

    def process(self):
        if not self.operation.acquire(blocking=False): return
        try:
            with self.lock:
                if not self.config['enabled']: return
                source = self._input()
                if source is None:
                    self.invalidate('等待已开启的彩色相机或导入图片'); return
                data, key, seq, stamp, kind = source
                token = (key, seq, self.generation)
                if token == self.last_token: return
                generation, config, calibration = self.generation, copy.deepcopy(self.config), copy.deepcopy(self.calibration)
            started = time.perf_counter()
            result = self.analyze(data, config, calibration, key)
            with self.lock:
                if generation != self.generation: return
                if kind == 'camera':
                    active = self.cameras.snapshot().get('active')
                    if not active or active.get('state')!='streaming' or 'camera:'+active['session'] != key or time.time()-stamp > 1.5:
                        self.invalidate('相机会话或帧已失效'); return
                if kind=='virtual' and (not self.virtual_data or self.virtual_data[1]!=key or time.time()-stamp>1.5):
                    self.invalidate('仿真相机视角或帧已失效');return
                if self.source_key != key or (self.latest and
                    [self.latest['image']['width'],self.latest['image']['height']] != [result['image']['width'],result['image']['height']]):
                    self.tracks.clear(); self.source_key = key
                result['objects'] = self.track(result['objects'], stamp)
                result.update(source=kind, source_key=key, frame_seq=seq, captured_at=stamp,
                              elapsed_ms=round((time.perf_counter()-started)*1000, 2))
                self.latest = result; self.last_token = token; self.error = ''; self.revision += 1
        finally:
            self.operation.release()

    @staticmethod
    def analyze(data, config, calibration, key):
        import cv2 as cv
        import numpy as np
        from ..vision_routes import _dimensions
        width, height, _ = _dimensions(data)
        image = cv.imdecode(np.frombuffer(data, np.uint8), cv.IMREAD_COLOR | cv.IMREAD_IGNORE_ORIENTATION)
        if image is None or image.shape[:2] != (height, width): raise ValueError('图像无法解码')
        scale = min(1., 640 / max(width, height))
        small = cv.resize(image, (max(1, round(width*scale)), max(1, round(height*scale)))) if scale < 1 else image
        hsv = cv.cvtColor(small, cv.COLOR_BGR2HSV)
        rx, ry, rw, rh = config['roi']
        x0, y0 = round(rx*small.shape[1]), round(ry*small.shape[0])
        x1, y1 = round((rx+rw)*small.shape[1]), round((ry+rh)*small.shape[0])
        ranges = {'red': [(0,10),(170,179)], 'yellow': [(15,38)], 'green': [(39,85)],
                  'cyan': [(86,100)], 'blue': [(101,130)], 'purple': [(131,169)]}
        detected = []
        kernel = np.ones((3,3), np.uint8)
        for color in config['colors']:
            if color == 'white': mask = cv.inRange(hsv, (0,0,185), (179,40,255))
            elif color == 'black': mask = cv.inRange(hsv, (0,0,0), (179,255,49))
            else:
                mask = np.zeros(hsv.shape[:2], np.uint8)
                for low, high in ranges[color]: mask |= cv.inRange(hsv, (low,80,50), (high,255,255))
            mask[:y0] = 0; mask[y1:] = 0; mask[:,:x0] = 0; mask[:,x1:] = 0
            mask = cv.morphologyEx(mask, cv.MORPH_OPEN, kernel)
            contours, _ = cv.findContours(mask, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
            if len(contours) > 5000: raise ValueError('轮廓过多，请收紧ROI或颜色范围')
            for contour in sorted(contours, key=cv.contourArea, reverse=True)[:100]:
                area = cv.contourArea(contour)
                if area < config['min_area']*scale*scale or area > .65*small.shape[0]*small.shape[1]: continue
                bx, by, bw, bh = cv.boundingRect(contour)
                if bx <= x0 or by <= y0 or bx+bw >= x1 or by+bh >= y1: continue
                perimeter = cv.arcLength(contour, True)
                if perimeter <= 0: continue
                approx = cv.approxPolyDP(contour, .025*perimeter, True)
                circularity = 4*math.pi*area/(perimeter*perimeter)
                shape = 'circle' if len(approx) >= 6 and circularity > .78 else 'triangle' if len(approx)==3 else 'rectangle' if len(approx)==4 and cv.isContourConvex(approx) else 'irregular'
                rect = cv.minAreaRect(contour)
                corners = cv.boxPoints(rect).astype(np.float64) / scale
                center = [v/scale for v in rect[0]]
                polygon = approx.reshape(-1,2).astype(np.float64)/scale
                inner = np.zeros(mask.shape, np.uint8); cv.drawContours(inner, [contour], -1, 255, -1)
                bgr = cv.mean(small, mask=inner)[:3]
                value = {'color': color, 'rgb': '#'+''.join(f'{round(v):02x}' for v in reversed(bgr)),
                         'shape': shape, 'center_px': center, 'corners': corners.tolist(),
                         'contour': polygon[:32].tolist(), 'size_px': [v/scale for v in rect[1]],
                         'area_px': area/(scale*scale), 'mapped': None}
                if calibration and calibration['source_key']==key and calibration['image_size']==[width,height]:
                    hull = np.array(calibration['pixel_hull'], np.float32)
                    if all(cv.pointPolygonTest(hull, tuple(map(float,point)), False) >= 0 for point in corners):
                        mapped = cv.perspectiveTransform(corners[None], np.array(calibration['matrix'], np.float64))[0]
                        midpoint = cv.perspectiveTransform(np.array([[center]], np.float64), np.array(calibration['matrix'], np.float64))[0,0]
                        if np.isfinite(mapped).all() and np.isfinite(midpoint).all():
                            sides = [float(np.linalg.norm(mapped[(i+1)%4]-mapped[i])) for i in (0,1)]
                            angle = math.degrees(math.atan2(*(mapped[1]-mapped[0])[::-1]))
                            if min(sides)>0 and max(sides)<10000:
                                value['mapped'] = {'xyz_mm': [float(midpoint[0]),float(midpoint[1]),calibration['plane_z']+config['height_mm']/2],
                                    'size_mm': [*sides,config['height_mm']], 'yaw_deg': angle,
                                    'height_estimated': True, 'coordinate_space':'base_xy_planar_estimate'}
                detected.append(value)
        detected.sort(key=lambda value:value['area_px'], reverse=True)
        # 固定尺寸预览与本次轮廓来自同一帧，避免在实时画面上叠加旧检测框。
        preview = small
        ok, encoded = cv.imencode('.jpg', preview, [cv.IMWRITE_JPEG_QUALITY,75])
        if not ok: raise ValueError('预览编码失败')
        return {'objects':detected[:50], 'image':{'width':width,'height':height,'mime':'image/jpeg',
                'base64':base64.b64encode(encoded).decode('ascii')}, 'calibrated':bool(calibration and calibration['source_key']==key and calibration['image_size']==[width,height])}

    def track(self, values, stamp):
        previous = {key:value for key,value in self.tracks.items() if 0 <= stamp-value['last_seen'] <= .65}
        pairs = []
        for index, value in enumerate(values):
            for track_id, old in previous.items():
                distance = math.dist(value['center_px'], old['center_px'])
                if value['color']==old['color'] and value['shape']==old['shape'] and distance < 80:
                    pairs.append((distance,index,track_id))
        assigned, used = {}, set()
        for _, index, track_id in sorted(pairs):
            if index not in assigned and track_id not in used: assigned[index]=track_id; used.add(track_id)
        tracks = {}
        for index, value in enumerate(values):
            track_id = assigned.get(index)
            if track_id is None: self.next_id+=1; track_id='V'+str(self.next_id)
            tracks[track_id]={**value,'id':track_id,'last_seen':stamp,'status':'observed'}
        for track_id, old in previous.items():
            if track_id not in tracks and len(tracks)<50:
                tracks[track_id]={**old,'status':'holding'}
        self.tracks=tracks
        return list(tracks.values())

    def calibrate(self, payload):
        import cv2 as cv
        import numpy as np
        if not isinstance(payload,dict) or set(payload)-{'points','plane_z','max_error_mm'}: raise ValueError('标定字段无效')
        points=payload.get('points')
        if not isinstance(points,list) or len(points)!=6: raise ValueError('需要4个标定点和2个独立复核点')
        rows=[]
        for row in points:
            if not isinstance(row,list) or len(row)!=4: raise ValueError('每个点需要像素XY及基座XY')
            rows.append([finite(v,-10000,10000) for v in row])
        plane_z=finite(payload.get('plane_z'),0,3000)
        maximum=finite(payload.get('max_error_mm',2),.1,10)
        with self.lock:
            if not self.latest: raise ValueError('先采集或导入图像，再进行标定')
            size=[self.latest['image']['width'],self.latest['image']['height']]
            pixels=np.array([row[:2] for row in rows],np.float64)
            world=np.array([row[2:] for row in rows],np.float64)
            if any(np.linalg.norm(pixels[i]-pixels[j])<2 for i in range(6) for j in range(i)):
                raise ValueError('标定点与独立复核点不能重复或过近')
            if any(not 0<=x<size[0] or not 0<=y<size[1] for x,y in pixels): raise ValueError('像素点超出图像')
            for array in (pixels[:4],world[:4]):
                hull=cv.convexHull(array.astype(np.float32))
                if len(hull)!=4 or abs(cv.contourArea(hull))<1: raise ValueError('前4个点必须构成不退化的四边形')
            matrix,_=cv.findHomography(pixels[:4],world[:4],0)
            if matrix is None or not np.isfinite(matrix).all() or np.linalg.cond(matrix)>1e10: raise ValueError('标定矩阵退化')
            denominators=np.c_[pixels[:4],np.ones(4)] @ matrix[2]
            if np.min(np.abs(denominators))<1e-8 or np.min(denominators)*np.max(denominators)<=0:
                raise ValueError('点位对应顺序错误，投影穿过无穷平面')
            hull=cv.convexHull(pixels[:4].astype(np.float32))
            if any(cv.pointPolygonTest(hull,tuple(point),False)<0 for point in pixels[4:]): raise ValueError('复核点应位于标定四边形内')
            check=cv.perspectiveTransform(pixels[4:][None],matrix)[0]
            errors=np.linalg.norm(check-world[4:],axis=1)
            if not np.isfinite(errors).all() or max(errors)>maximum: raise ValueError('独立复核误差超限，请核对点位')
            value={'source_key':self.latest['source_key'],'image_size':size,'matrix':matrix.tolist(),
                'pixel_hull':hull.tolist(),'plane_z':plane_z,'check_errors_mm':errors.tolist(),
                'points':rows,'created_at':time.time(),'scope':'本次相机会话、原分辨率、固定相机与同一水平面；不含畸变校正/手眼标定'}
            self.calibration=value; self.generation+=1; self.last_token=None; self.tracks.clear();self.latest=None;self.revision+=1
            return {'ok':True,'calibration':copy.deepcopy(value)}

    def upload(self, data):
        from ..vision_routes import _dimensions
        _dimensions(data)
        with self.lock:
            # 导入是显式操作；暂停实时源，防止下一帧覆盖正在标定的图片。
            self.config=settings({**self.config,'input':'image','enabled':True})
            key='image:'+hashlib.sha256(data).hexdigest()[:24]
            self.image_data=(data,key,1,time.time(),'image')
            self.generation+=1;self.last_token=None;self.latest=None;self.tracks.clear();self.revision+=1
        self.process()
        return self.snapshot()

    def virtual_frame(self,payload):
        from ..vision_routes import _dimensions, MAX_IMAGE_BYTES
        if not isinstance(payload,dict) or set(payload)!={'session','image_base64','view_key','captured_at'}: raise ValueError('仿真帧字段无效')
        key=payload['view_key'];encoded=payload['image_base64'];stamp=finite(payload['captured_at'],0,1e12)
        if not isinstance(key,str) or not 1<=len(key)<=512: raise ValueError('仿真位姿标识无效')
        if not isinstance(encoded,str) or len(encoded)>4*((MAX_IMAGE_BYTES+2)//3): raise ValueError('仿真图像过大')
        data=base64.b64decode(encoded,validate=True);_dimensions(data)
        if not 0<=time.time()-stamp<=1.5: raise ValueError('仿真帧过期或时钟无效')
        with self.lock:
            if payload['session']!=self.virtual_session or self.config['input']!='virtual' or not self.config['enabled']:
                raise ValueError('仿真相机会话已变化')
            self.virtual_seq+=1
            source_key='virtual:'+self.virtual_session+':'+hashlib.sha256(key.encode()).hexdigest()[:16]
            self.virtual_data=(data,source_key,self.virtual_seq,stamp,'virtual')
        return {'ok':True,'frame_seq':self.virtual_seq}

    async def start(self):
        if self.task: return
        async def run():
            while True:
                try: await asyncio.to_thread(self.process)
                except asyncio.CancelledError: raise
                except Exception as exc: self.invalidate('检测未完成：'+str(exc)[:200])
                await asyncio.sleep(.01 if self.latest and self.latest['source'] in ('camera','virtual') else .15)
        self.task=asyncio.create_task(run())

    async def close(self):
        if self.task:
            task=self.task;self.task=None;task.cancel()
            try: await task
            except asyncio.CancelledError: pass
        # to_thread取消不会终止原生计算；屏障在其真正退出后才释放。
        await asyncio.to_thread(self.operation.acquire)
        self.operation.release()
