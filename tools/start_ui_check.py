"""在临时源码副本上托管 UI 验证服务，不访问项目数据或机器人。Ctrl+C 退出。"""
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
COPY = Path(tempfile.mkdtemp(prefix="huayan-ui-check-"))
for rel in ("sim/backend", "sim/web/dist", "sim/web/public/models", "huayan_panel/core"):
    shutil.copytree(ROOT / rel, COPY / rel, ignore=shutil.ignore_patterns("data", "__pycache__"))
os.environ["HUYAN_NO_REAL"] = "1"
os.environ["HUYAN_TOKEN"] = ""
sys.path.insert(0, str(COPY))
os.chdir(COPY)

if __name__ == "__main__":
    import uvicorn
    print("UI validation copy:", COPY, flush=True)
    uvicorn.run("sim.backend.main:app", host="127.0.0.1", port=8711)
