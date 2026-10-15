"""多现场文件接口离线自检。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from sim.backend.main import app


def main():
    name = "离线检查现场"
    with TestClient(app) as client:
        put = client.post("/api/scenes", json={"name": name, "objects": [
            {"name": "箱体", "kind": "box", "xyz": [10, 20, 30], "size": [40, 40, 40]}
        ]})
        assert put.status_code == 200 and put.json().get("ok")
        listed = client.get("/api/scenes")
        assert listed.status_code == 200 and any(x["name"] == name for x in listed.json()["items"])
        exported = client.get("/api/scenes/export/" + name)
        assert exported.status_code == 200 and len(exported.json().get("objects", [])) == 1
        loaded = client.post("/api/scenes/load", json={"name": name})
        assert loaded.status_code == 200 and loaded.json().get("ok")
        removed = client.delete("/api/scenes/" + name)
        assert removed.status_code == 200 and removed.json().get("ok")
    print("scenes: 5/5 passed")


if __name__ == "__main__":
    main()
