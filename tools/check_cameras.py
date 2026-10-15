"""摄像头接口离线自检：不要求机器上真的接摄像头，也不打开机器人链路。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from sim.backend.main import app


def main():
    with TestClient(app) as client:
        state = client.get("/api/cameras")
        scan = client.post("/api/cameras/scan", json={})
        missing = client.post("/api/cameras/open", json={"camera_id": "camera:missing", "streams": ["color"]})
        bad = client.post("/api/cameras/open", json={"camera_id": "camera:missing", "streams": "color"})
        assert state.status_code == 200 and {"devices", "active", "sdk", "error"} <= state.json().keys()
        assert scan.status_code == 200 and isinstance(scan.json().get("devices"), list)
        assert missing.status_code == 200 and missing.json().get("ok") is False
        assert bad.status_code == 200 and bad.json().get("ok") is False
    print("cameras: 4/4 passed")


if __name__ == "__main__":
    main()
