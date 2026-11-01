"""Require the disposable checkout before tests can import stateful backend modules."""
import os
from pathlib import Path


def require_offline_copy():
    root = Path(__file__).resolve().parents[1]
    declared = os.environ.get("HUYAN_OFFLINE_ROOT")
    if not declared or Path(declared).resolve() != root or (root / ".git").exists():
        raise SystemExit("Use tools/run_offline_checks.py. Refusing to change live workstation data.")
    data = os.environ.get("ELFIN_DATA_DIR")
    if not data or not Path(data).resolve().is_relative_to(root) or Path(data).resolve() == root:
        raise SystemExit("Offline ELFIN_DATA_DIR must be inside the disposable checkout.")
    if os.environ.get('HUYAN_OFFLINE_RUNTIME_PID') != str(os.getpid()):
        raise SystemExit("Offline network/camera startup barriers were not installed in this process.")
    os.environ["HUYAN_NO_REAL"] = "1"
