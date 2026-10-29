"""多现场文件接口离线自检。"""
from _offline_guard import require_offline_copy
require_offline_copy()

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from backend.main import app
from backend.main import world
from unittest.mock import patch
from types import SimpleNamespace


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
        before = world.objects.list()
        for field in ('busy', 'active', 'held'):
            state = dict(busy=False, active=None, held=None)
            state[field] = True
            with patch.object(world, 'robot_play', SimpleNamespace(**state)):
                denied = client.post('/api/scenes/load', json={'name': name})
                assert denied.json().get('ok') is False
                assert world.objects.list() == before
        removed = client.delete("/api/scenes/" + name)
        assert removed.status_code == 200 and removed.json().get("ok")
    print("scenes: 8/8 passed (including planning/running/held load rejection)")


if __name__ == "__main__":
    main()
