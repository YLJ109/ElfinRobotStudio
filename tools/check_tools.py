"""末端工具选择与动作的离线验收；HUYAN_NO_REAL=1 时绝不触碰控制器。"""
from fastapi.testclient import TestClient

from sim.backend.main import app
from sim.backend.core import program


def check(condition, label):
    if not condition:
        raise AssertionError(label)
    print("PASS ", label)


client = TestClient(app)
initial = client.get("/api/tools")
check(initial.status_code == 200, "工具状态可读取")
check(initial.json()["mounted"]["state"] == "unknown", "默认工具状态保持未知")
base_pose = client.post("/api/fk", json={"joints": [0, 0, 0, 0, 0, 0]}).json()["xyz_mm"]

selected = client.post("/api/tools/select", json={"tool_id": "gripper-template"})
check(selected.json().get("ok") is True, "可人工确认夹爪模板")
tool_pose = client.post("/api/fk", json={"joints": [0, 0, 0, 0, 0, 0]}).json()["xyz_mm"]
check(abs(tool_pose[2] - base_pose[2] - 100) < 0.01, "确认工具后 FK 使用工具 TCP 偏移")
closed = client.post("/api/tools/action", json={"action": "close"})
check(closed.json().get("ok") is True and closed.json().get("sim") is True, "仿真关闭动作不发送硬件指令")
check(closed.json()["mounted"]["action_state"] == "active", "关闭动作更新虚拟工具状态")
opened = client.post("/api/tools/action", json={"action": "open"})
check(opened.json().get("ok") is True and opened.json()["mounted"]["action_state"] == "idle", "打开动作恢复空闲状态")

cleared = client.post("/api/tools/clear", json={})
check(cleared.json().get("ok") is True, "可确认法兰无工具")
rejected = client.post("/api/tools/action", json={"action": "close"})
check(rejected.json().get("ok") is False, "无工具时拒绝末端动作")
steps = program.parse("REQUIRE_TOOL gripper-template\nTOOL CLOSE\nMOVEL 420,0,1195,0,0,0\nTOOL OPEN", lambda _: None)
check([s["kind"] for s in steps] == ["require_tool", "tool_action", "movel", "tool_action"], "程序支持工具、MOVEL指令")
print("工具动作检查完成")
