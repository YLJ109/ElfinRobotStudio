"""项目路径的唯一入口；不依赖启动时的工作目录。"""
from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = PROJECT_ROOT / "backend"
WEB_ROOT = PROJECT_ROOT / "web"
DATA_DIR = Path(os.environ['ELFIN_DATA_DIR']).resolve() if os.environ.get('ELFIN_DATA_DIR') else BACKEND_ROOT / "data"
_PUBLIC = WEB_ROOT / "public" if (WEB_ROOT / "public").is_dir() else WEB_ROOT / "dist"
MODEL_DIR = _PUBLIC / "models"
TOOL_CATALOG = _PUBLIC / "tools" / "catalog.json"
WEB_DIST = WEB_ROOT / "dist"
