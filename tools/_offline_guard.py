"""Require the disposable checkout before tests can import stateful backend modules."""
import os
from pathlib import Path


def require_offline_copy():
    root = Path(__file__).resolve().parents[1]
    declared = os.environ.get("HUYAN_OFFLINE_ROOT")
    if not declared or Path(declared).resolve() != root or (root / ".git").exists():
        raise SystemExit("Use tools/run_offline_checks.py. Refusing to change live workstation data.")
    os.environ["HUYAN_NO_REAL"] = "1"
