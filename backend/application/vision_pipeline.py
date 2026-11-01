"""One latest-frame detector worker, independent of transport and robot control."""
from __future__ import annotations
import asyncio
import copy
import math
import json
import threading
import time


class VisionPipeline:
    TTL = .3
    MAX_PIXELS = 1280 * 720
    # 关闭检测后模型还留一会儿，期间再开不需要重新加载 worker（实测冷启动
    # 1.1s，但叠上相机开启与 IPC 握手，用户感知是"每点一次开始跟随都要等"）。
    # 两分钟覆盖"停一下再开"的常见间隔；空闲期间不采集、不推理，只占内存。
    IDLE_MODEL_SECONDS = 120.
    # 帧复制一律在工作线程执行：720p 一份 RGB 约 2.7MB，在主事件循环里
    # 每秒抓 50 次会把世界循环、HTTP 和预检全部挤住（"开摄像头就卡"的根因）。
    POLL_INTERVAL = 1 / 30

    def __init__(self, cameras, detector_factory, *, clock=time.monotonic):
        self.cameras, self.detector_factory, self.clock = cameras, detector_factory, clock
        self._condition = threading.Condition()
        self._cancel = threading.Event()
        self._thread = self._poll_task = None
        self._closing = False
        self._busy = False
        self._idle_model_until = None
        self.enabled = False
        self.source = self.session = None
        self.epoch = 0
        self._seq = -1
        self._last_published_seq = -1
        self._pending = self._result = None
        self.error = None
        # 检测器进程的真实生命周期。'enabled' 只表示"用户请求了检测"，
        # 只有 detector_state=='ready' 才表示模型真的能用；'warming' 是
        # 冷启动窗口（MediaPipe 首次加载约 10 秒），'failed' 带上可读原因。
        self._detector_state = 'idle'
        self._detector_error = None
        self._detector_identity = None
        self._seen_color_seq = -1
        self._last_local_frame_at = None
        # 启动即预热：MediaPipe 首次加载要 8~10 秒，分摊到"开程序"这段时间里，
        # 用户点"开始跟随"时模型已经在内存里。预热失败不影响任何功能，只是
        # 回到原来的冷启动路径。
        self._warm_detector = None
        self._warm_started = False
        self.stats = dict(submitted=0, processed=0, inferred=0, replaced=0, discarded=0, failures=0)

    def prewarm(self):
        """后台把检测模型先加载好，等真正要用时直接交付。

        加载一次约 8~10 秒（MediaPipe 首次 import + 模型解析），原来是点
        "开始跟随"的时候才付这笔时间，现场就是"点完开始干等"。放到程序启动
        后立刻做，用户来得及操作时模型早就好了。失败就静默放弃，走原来的
        冷启动路径，不影响功能。
        """
        with self._condition:
            if self._warm_started or self._warm_detector is not None or self._closing:
                return
            self._warm_started = True

        def load():
            detector = None
            try:
                detector = self.detector_factory(cancel_event=self._cancel)
                with self._condition:
                    keep = not self._closing
                    if keep:
                        self._warm_detector = detector
                if not keep:
                    try:
                        detector.close()
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                # 允许"空闲释放之后再次预热"：不重置的话，模型被回收一次就
                # 永远回不到热启动状态，用户隔几分钟再点开始又要等十秒。
                with self._condition:
                    self._warm_started = False

        threading.Thread(target=load, daemon=True, name='vision-prewarm').start()

    def _take_warm(self):
        """取走预热好的模型；没有就返回 None。"""
        with self._condition:
            detector, self._warm_detector = self._warm_detector, None
            return detector

    async def start(self):
        with self._condition:
            if self._closing and self._thread and self._thread.is_alive():
                raise RuntimeError('旧检测worker尚未释放')
            self._closing = False
            self._cancel.clear()
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_local())

    def configure(self, *, enabled, source=None, session=None):
        if type(enabled) is not bool:
            raise ValueError('检测开关需要布尔值')
        if enabled:
            if source not in ('local', 'remote', 'virtual') or not isinstance(session, str) or not 1 <= len(session) <= 128:
                raise ValueError('检测来源或会话无效')
            if source == 'local':
                active = self.cameras.snapshot().get('active')
                if not active or active.get('session') != session or active.get('state') not in ('starting','streaming'):
                    raise ValueError('请先显式打开工作站相机并等待画面')
        with self._condition:
            if self._closing:
                raise ValueError('检测服务正在退出')
            # Revocation below is immediate. Only the already-owned model may
            # remain idle briefly; no capture or inference is permitted disabled.
            if not enabled and self.enabled:
                self._idle_model_until = self.clock() + self.IDLE_MODEL_SECONDS
            elif enabled:
                self._idle_model_until = None
            self.epoch += 1
            self.enabled, self.source, self.session = enabled, source if enabled else None, session if enabled else None
            self._pending = self._result = None
            self._seq, self.error = -1, None
            self._seen_color_seq = -1
            self._last_local_frame_at = None
            self._last_published_seq = -1
            if enabled:
                # 同一来源的保温模型仍然可用；换来源必须重新冷启动。
                if self._detector_state != 'ready' or self._detector_identity != (source, session):
                    self._detector_state, self._detector_error = 'warming', None
            if enabled and (self._thread is None or not self._thread.is_alive()):
                self._thread = threading.Thread(target=self._run, name='vision-detector', daemon=True)
                self._thread.start()
            self._condition.notify_all()
        return self.snapshot()

    def submit(self, image, *, source, session, epoch, seq, captured_at=None, clock_domain='remote', received_at=None):
        import numpy as np
        now = self.clock()
        received = now if received_at is None else received_at
        if (type(received) not in (int,float) or not math.isfinite(received)
                or not 0 <= now-received < self.TTL):
            raise ValueError('接收帧已过期')
        if (not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim != 3
                or image.shape[2] != 3 or min(image.shape[:2]) < 1
                or image.shape[0]*image.shape[1] > self.MAX_PIXELS):
            raise ValueError('检测图像需要不超过1280×720像素的RGB8画面')
        if type(seq) is not int or not 0 <= seq <= 2**53-1:
            raise ValueError('帧序号无效')
        if captured_at is not None and (type(captured_at) not in (int,float) or not math.isfinite(captured_at)):
            raise ValueError('采集时间无效')
        if clock_domain not in ('server', 'remote') or clock_domain == 'server' and source != 'local':
            raise ValueError('来源时钟未经工作站核验')
        if clock_domain == 'server' and (captured_at is None or not 0 <= now-captured_at < self.TTL):
            raise ValueError('采集帧已过期')
        with self._condition:
            if not self.enabled or (source,session,epoch) != (self.source,self.session,self.epoch):
                raise ValueError('检测来源已失效')
            if seq <= self._seq:
                raise ValueError('帧序号已过期或重复')
            if self._pending is not None:
                self.stats['replaced'] += 1
            # 工作站取帧已经返回只读独立副本，可零拷贝接管；上传帧可写，
            # 必须复制后再冻结，避免把调用方的数组改成只读。
            if image.flags.c_contiguous and not image.flags.writeable:
                rgb = image
            else:
                rgb = np.ascontiguousarray(image).copy()
                rgb.setflags(write=False)
            self._pending = dict(source=source,session=session,epoch=epoch,seq=seq,
                captured_at=captured_at,received_at=received,clock_domain=clock_domain,
                width=image.shape[1],height=image.shape[0],
                capture_time_kind='host_read_return' if source=='local' else 'source_reported',
                clock_uncertainty_ms=0. if clock_domain == 'server' else None,image=rgb)
            self._seq = seq
            self.stats['submitted'] += 1
            self._condition.notify_all()
        return {'ok': True, 'epoch': epoch, 'seq': seq}

    def _run(self):
        detector = None
        detector_identity = None
        failed_epoch,failure_message = None,None
        try:
            while True:
                with self._condition:
                    while True:
                        if self._closing:
                            break
                        identity = (self.source,self.session)
                        mismatch = detector is not None and self.enabled and detector_identity != identity
                        idle_left = (self._idle_model_until-self.clock()) if self._idle_model_until is not None else 0.
                        expired = detector is not None and not self.enabled and idle_left <= 0
                        retire = detector if mismatch or expired else None
                        warm = self.enabled and detector is None and failed_epoch != self.epoch
                        warm_epoch, warm_identity = self.epoch, identity
                        if retire is not None:
                            detector = detector_identity = None
                            break
                        if warm:
                            self._busy = True
                            break
                        if self.enabled and self._pending is not None:
                            frame,self._pending = self._pending,None
                            self._busy = True
                            break
                        # An idle model has no pending frame and never polls the
                        # camera. Timeout releases this same owned process.
                        self._condition.wait(timeout=idle_left if detector is not None and not self.enabled else None)
                    if self._closing:
                        break
                if retire is not None:
                    try: retire.close()
                    except Exception as exc:
                        with self._condition: self.error = '检测器释放失败：'+str(exc)[:180]
                    with self._condition:
                        self._detector_state, self._detector_identity = 'idle', None
                    continue
                if warm:
                    try:
                        # 预热好的模型直接用；没有才现加载（省 8~10 秒）。
                        detector=self._take_warm() or self.detector_factory(cancel_event=self._cancel)
                        detector_identity=warm_identity
                    except Exception as exc:
                        detail = str(exc)[:240] or type(exc).__name__
                        failed_epoch,failure_message = warm_epoch,'手掌检测器启动失败：'+detail
                        with self._condition:
                            self._detector_state, self._detector_error = 'failed', failure_message
                            if self.epoch==warm_epoch:self.error=failure_message
                    else:
                        with self._condition:
                            if self.epoch==warm_epoch and self.enabled:
                                self._detector_state, self._detector_error = 'ready', None
                                self._detector_identity = warm_identity
                            else:
                                # 冷启动期间来源已变或已关闭：这份模型立刻退役。
                                with self._condition:
                                    self._detector_state, self._detector_identity = 'idle', None
                                try: detector.close()
                                except Exception as exc:
                                    with self._condition: self.error = '检测器释放失败：'+str(exc)[:180]
                                detector=detector_identity=None
                    finally:
                        with self._condition:self._busy=False
                    continue
                started = self.clock()
                result = {key:value for key,value in frame.items() if key != 'image'}
                result.update(inference_started_at=started,model=None,coords='image_normalized',
                              observations=[],valid=False,control_valid=False,error=None)
                try:
                    if failed_epoch == frame['epoch']:
                        raise RuntimeError(failure_message)
                    if detector is None:
                        detector = self._take_warm() or self.detector_factory(cancel_event=self._cancel)
                        detector_identity=(frame['source'],frame['session'])
                        with self._condition:
                            if self.epoch==frame['epoch']:
                                self._detector_state, self._detector_error = 'ready', None
                                self._detector_identity = detector_identity
                    with self._condition:self.stats['inferred'] += 1
                    output = detector.detect(frame['image'])
                    if not isinstance(output,dict) or not isinstance(output.get('observations'),list) or not isinstance(output.get('model'),dict):
                        raise ValueError('检测器结果契约无效')
                    if len(output['observations']) > 4 or len(json.dumps(output,allow_nan=False)) > 32768:
                        raise ValueError('检测器结果超出预算')
                    result.update(observations=output['observations'],model=output['model'],valid=True)
                except Exception as exc:
                    result['error'] = str(exc)[:240] or type(exc).__name__
                    failed_epoch,failure_message = frame['epoch'],result['error']
                    if detector is not None:
                        try:detector.close()
                        except Exception as close_error:
                            result['error'] += '；释放失败：'+str(close_error)[:100]
                        finally:detector=detector_identity=None
                    with self._condition:
                        self.stats['failures'] += 1
                        self._detector_state, self._detector_error = 'failed', result['error']
                        self._detector_identity = None
                finished = self.clock()
                trusted = frame['clock_domain'] == 'server'
                result.update(inference_finished_at=finished,
                    valid_until=(frame['captured_at'] if trusted else frame['received_at'])+self.TTL)
                result['control_valid'] = bool(result['valid'] and result['observations'] and trusted
                                               and finished < result['valid_until'])
                with self._condition:
                    self._busy = False
                    self.stats['processed'] += 1
                self._publish_result(frame,result)
        finally:
            if detector is not None:
                try: detector.close()
                except Exception as exc:
                    with self._condition: self.error = '检测器释放失败：'+str(exc)[:180]
            with self._condition:
                self._detector_state, self._detector_identity = 'idle', None

    def _publish_result(self, frame, result):
        """Monotonic publication, current source, and original capture TTL.

        Queued frames may supersede one another, but a fresh completed result
        cannot rewind the last published sequence or cross a source epoch.
        This avoids the reproduced 60Hz/20Hz starvation while retaining all
        result-order, source, and expiration gates.
        """
        with self._condition:
            if (not self.enabled or self._closing
                    or (frame['source'],frame['session'],frame['epoch'])
                    != (self.source,self.session,self.epoch)
                    or frame['seq'] <= self._last_published_seq
                    or frame['seq'] > self._seq):
                self.stats['discarded'] += 1
                return False
            if self.clock() >= result['valid_until']:
                self.stats['discarded'] += 1
                self.error = result['error'] or '检测结果已过期，请等待新帧'
                if result['error']:
                    self._result = None
                return False
            self._last_published_seq = frame['seq']
            self._result,self.error = result,result['error']
            return True

    def _local_source_alive(self, session):
        """True / False / None（判不出来）。

        调用方要据此决定是否继续声称"检测已开启"：判不出来**不等于**相机还在。
        原来这里在异常时返回 True，等于"查不到就当活着"，结果后端一直声称检测
        已开、实际一帧也拿不到 —— 与调用点的意图正好相反。
        """
        getter = getattr(self.cameras,'active_session',None)
        if callable(getter):
            try: return getter() == session
            except Exception: return None
        try:
            active = self.cameras.snapshot().get('active')
        except Exception:
            return None
        if not isinstance(active,dict):
            return False
        return active.get('session') == session

    def _local_frame_is_fresh(self):
        """本地源最近是否真的送来过新帧（用来在"判不出来"时做二次确认）。"""
        last = getattr(self,'_last_local_frame_at',None)
        return last is not None and (self.clock() - last) < 3.0

    async def _poll_local(self):
        while True:
            with self._condition:
                identity = (self.source,self.session,self.epoch,self._seq) if self.enabled else None
            if identity and identity[0] == 'local':
                # 相机被关闭或换人接管后，本地检测必须随之失效：否则后端会一直
                # 声称"检测已开启"，而实际一帧也拿不到。
                # "判不出来"时以最近是否真的收到新帧为准：仍在出帧就说明相机活着，
                # 不再出帧说明这条会话已经作废。
                alive = self._local_source_alive(identity[1])
                if alive is None:
                    alive = self._local_frame_is_fresh()
                if not alive:
                    with self._condition:
                        if self.enabled and self.source=='local' and self.session==identity[1]:
                            self._idle_model_until = self.clock() + self.IDLE_MODEL_SECONDS
                            self.epoch += 1
                            self.enabled, self.source, self.session = False, None, None
                            self._pending = self._result = None
                            self._seq, self.error = -1, None
                            self._seen_color_seq = -1
                            self._last_published_seq = -1
                            self._condition.notify_all()
                    await asyncio.sleep(self.POLL_INTERVAL)
                    continue
                # 先用轻量元数据判断有没有新帧：每 33ms 无条件拷一份 720p RGB
                # 是纯浪费，低帧率相机下 3/4 的拷贝都是重复的同一帧。
                hint = -1
                try:
                    meta = self.cameras.stream_frame('color', identity[1])
                    hint = int(meta[0]) if meta else -1
                except Exception:
                    hint = -1
                if hint > self._seen_color_seq:
                    self._seen_color_seq = hint
                    # 复制 720p 帧是纯 CPU 工作，放到线程里做，别占住事件循环。
                    try:
                        frame = await asyncio.to_thread(self.cameras.raw_frame_copy, identity[1])
                    except Exception:
                        frame = None
                    if frame and frame['seq'] > identity[3]:
                        self._last_local_frame_at = self.clock()
                        try:
                            self.submit(frame['image'],source='local',session=identity[1],epoch=identity[2],
                                        seq=frame['seq'],captured_at=frame['captured_at'],clock_domain='server')
                        except ValueError: pass # switched source while copying; no stale work admitted
            await asyncio.sleep(self.POLL_INTERVAL)

    def snapshot(self):
        with self._condition:
            result = copy.deepcopy(self._result)
            if result:
                result['control_valid'] = bool(result['control_valid'] and self.enabled
                                              and self.clock() < result['valid_until'])
            return dict(ok=True,enabled=self.enabled,source=self.source,session=self.session,
                        epoch=self.epoch,result=result,error=self.error,stats=dict(self.stats),
                        server_now=self.clock(),
                        busy=self._busy,pending=self._pending is not None,
                        detector_state=self._detector_state,detector_error=self._detector_error,
                        detector_ready=self._detector_state=='ready',
                        transport='experimental_binary_upload',remote_control_available=False)

    async def close(self):
        self._cancel.set()
        with self._condition:
            self._closing = True
            self.enabled = False
            self.epoch += 1
            self._pending = self._result = None
            self._condition.notify_all()
        if self._poll_task:
            self._poll_task.cancel()
            await asyncio.gather(self._poll_task,return_exceptions=True)
            self._poll_task = None
        if self._thread:
            await asyncio.to_thread(self._thread.join, 4.)
            if self._thread.is_alive():
                raise RuntimeError('检测worker未完成释放')
            # 线程引用保留：调用方（含离线检查）要能确认 worker 已停 —— 线程仍在，
            # 只是不再存活。重新拉起由 start() 复位 _closing 与 _cancel 事件完成。
        # 预热但没用上的模型也要在这里关掉，否则它会以一个孤儿子进程留到进程退出。
        warm = self._take_warm()
        if warm is not None:
            try:
                await asyncio.to_thread(warm.close)
            except Exception:
                pass
        self._cancel.clear()
        with self._condition:
            self._detector_state, self._detector_identity = 'idle', None
