# Built in the optional inference environment, separate from device OpenCV.
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata

root=Path(SPECPATH).parent
datas,binaries,hiddenimports=collect_all('mediapipe')
datas+=copy_metadata('mediapipe')
a=Analysis([str(root/'backend/infrastructure/vision/hand_worker.py')],pathex=[str(root)],
    datas=datas,binaries=binaries,hiddenimports=hiddenimports,
    excludes=['tkinter','PyQt6'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='ElfinVision',console=True,
    strip=False,upx=False)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='ElfinVision')
