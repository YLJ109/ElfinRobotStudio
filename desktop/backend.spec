# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from importlib.util import find_spec
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
hidden = ['backend.main', 'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto',
          'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on']
for package in ('cv2', 'cv2_enumerate_cameras', 'pyrealsense2'):
    if find_spec(package):
        hidden.extend(collect_submodules(package))
a = Analysis([str(root / 'desktop/backend_entry.py')], pathex=[str(root)],
    binaries=[], datas=[(str(root / 'web/dist'), 'web/dist')], hiddenimports=hidden,
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['tkinter', 'PyQt6', 'matplotlib'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='ElfinBackend', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=True,
    icon=str(root / 'desktop/assets/app.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='ElfinBackend')
