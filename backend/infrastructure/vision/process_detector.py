"""Bounded RGB IPC to the dedicated inference interpreter; no capture here."""
import json
import os
from pathlib import Path
import queue
import struct
import subprocess
import sys
import threading
import time
from ...paths import PROJECT_ROOT, WEB_ROOT, WEB_DIST
WORKER_PATH = Path(__file__).with_name('hand_worker.py')


class ProcessHandDetector:
    def __init__(self, *, python=None, model_path=None, timeout=3., cancel_event=None):
        executable = os.environ.get('ELFIN_VISION_WORKER')
        python = python or os.environ.get('ELFIN_VISION_PYTHON')
        source_python = PROJECT_ROOT/'backend/.venv-vision/Scripts/python.exe'
        if not executable and not python and not getattr(sys,'frozen',False) and source_python.is_file():
            python = str(source_python)
        if not executable and not python and getattr(sys,'frozen',False):
            raise RuntimeError('桌面检测需要独立视觉worker或ELFIN_VISION_PYTHON')
        python = python or sys.executable
        public = WEB_ROOT/'public/vision/hand_landmarker.task'
        model_path = Path(model_path) if model_path else public if public.is_file() else WEB_DIST/'vision/hand_landmarker.task'
        if not model_path.is_file():
            raise RuntimeError('手掌模型不存在')
        if executable:
            if not Path(executable).is_file():
                raise RuntimeError('独立视觉worker不存在')
            command = [executable,str(model_path)]
        else:
            worker_path = WORKER_PATH
            if not worker_path.is_file():
                raise RuntimeError('手掌检测worker未随程序部署')
            command = [python,'-u',str(worker_path),str(model_path)]
        self.timeout = timeout
        self._cancel = cancel_event or threading.Event()
        self._close_lock = threading.Lock()
        self._closed = False
        self._responses = queue.Queue(maxsize=2)
        self.process = subprocess.Popen(command,
            cwd=PROJECT_ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self._reader = threading.Thread(target=self._read,daemon=True,name='vision-ipc-reader')
        self._reader.start()
        try:
            ready = self._response(timeout=20.)
            if ready.get('ready') is not True:
                raise RuntimeError(ready.get('error') or '手掌检测worker不可用')
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                try: value = json.loads(line)
                except (ValueError,UnicodeError): value = {'error':'检测worker输出无效'}
                try: self._responses.put(value,timeout=.1)
                except queue.Full: break
        finally:
            try: self._responses.put_nowait({'error':'检测worker已退出'})
            except queue.Full: pass

    def _response(self, *, timeout=None):
        deadline=time.monotonic()+(self.timeout if timeout is None else timeout)
        while True:
            if self._cancel.is_set():
                self.close()
                raise RuntimeError('手掌检测已取消')
            remaining=deadline-time.monotonic()
            if remaining <= 0:
                self.close()
                raise RuntimeError('手掌检测超时')
            try:
                value=self._responses.get(timeout=min(.1,remaining))
                break
            except queue.Empty:continue
        if value.get('error'):
            raise RuntimeError(value['error'])
        return value

    def detect(self, image):
        if self.process.poll() is not None:
            raise RuntimeError('手掌检测worker已退出')
        header = json.dumps({'width':image.shape[1],'height':image.shape[0],'bytes':image.nbytes}).encode()
        try:
            self.process.stdin.write(struct.pack('!I',len(header))+header+image.tobytes())
            self.process.stdin.flush()
        except (BrokenPipeError,OSError):
            raise RuntimeError('手掌检测worker通信失败') from None
        return self._response()

    def close(self):
        with self._close_lock:
            if self._closed:return
            self._closed=True
            self._close_process()

    def _close_process(self):
        process = self.process
        if process.stdin:
            try: process.stdin.close()
            except OSError: pass
        try: process.wait(timeout=1.)
        except subprocess.TimeoutExpired:
            # This handle is exclusively the child created above, never a
            # discovered workstation/user process.
            process.terminate()
            try: process.wait(timeout=1.)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.)
        if process.stdout:
            self._reader.join(timeout=.5)
            process.stdout.close()
