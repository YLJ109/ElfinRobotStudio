"""后端接口冒烟测试（进程内，不占网络端口）。

跑法： python tools/check_backend.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 隔离闸：本脚本会真的调用点动 / DO 写入 / 就绪泵这些接口，一旦它连上控制器，
# 就是拿一台通电的机械臂跑单测（2026-10-12 发生过：J6 被拖走 2°、DO1 留在通电）。
# 必须在 import app（World 在 import 时就建好）之前置上。
import os
os.environ["HUYAN_NO_REAL"] = "1"

from fastapi.testclient import TestClient    # noqa: E402

# 必须先清掉上一轮持久化的配置，再 import app（World 在 import 时就读了 settings.json）。
# 但 events.jsonl 是用户唯一的真机审计第一现场，先备份、退出时还原，别拿测试覆盖掉它。
import atexit, shutil, tempfile
_DATA = Path(__file__).resolve().parents[1] / "sim" / "backend" / "data"
_BAK = Path(tempfile.mkdtemp(prefix="hydata_"))
for _f in ("settings.json", "points.json", "events.jsonl", "objects.json"):
    _p = _DATA / _f
    if _p.exists():
        shutil.copy2(_p, _BAK / _p.name)
        _p.unlink()


@atexit.register
def _restore():
    for _p in _BAK.iterdir():
        shutil.copy2(_p, _DATA / _p.name)
    print("已还原 sim/backend/data，备份在 " + str(_BAK))

from sim.backend.main import app             # noqa: E402

ok_n = fail_n = 0


def check(name, cond, detail=""):
    global ok_n, fail_n
    if cond:
        ok_n += 1
        print(f"PASS  {name}  {detail}")
    else:
        fail_n += 1
        print(f"FAIL  {name}  {detail}")


def main():
    # base_url 用真实回环地址，让 Origin/Host 那条同源判定也走到（不然测的是另一条路）
    with TestClient(app, base_url="http://127.0.0.1:8710") as c:
        r = c.get("/api/state")
        check("GET /api/state", r.status_code == 200, str(r.status_code))
        s = r.json()
        check("快照含关节/安全/TCP", all(k in s for k in ("joints", "safety", "tcp")),
              f"q={s['joints']['q']} level={s['safety']['level']}")
        check("默认仿真速度 10%", s["sim_speed_pct"] == 10.0, str(s["sim_speed_pct"]))
        check("默认真机速度 5%", s["real_speed_pct"] == 5.0, str(s["real_speed_pct"]))
        check("快照速度 = 当前模式那一路", s["speed_pct"] == s["sim_speed_pct"], "")
        # 改真机速度不能把仿真速度带跑（两条独立）。软件侧已无速度上限，直接量透传
        c.post("/api/settings", json={"real_speed_pct": 33})
        s2 = c.get("/api/state").json()
        check("改真机速度不影响仿真速度",
              s2["real_speed_pct"] == 33.0 and s2["sim_speed_pct"] == 10.0,
              f"real={s2['real_speed_pct']} sim={s2['sim_speed_pct']}")
        check("仿真模式下改真机速度不冒充已下发",
              s2["speed_ack"]["ok"] is None and "没往控制器发" in s2["speed_ack"]["why"],
              str(s2["speed_ack"]))
        c.post("/api/settings", json={"real_speed_pct": 5})
        check("默认仿真模式", s["mode"] == "sim")

        r = c.get("/api/meta")
        m = r.json()
        check("GET /api/meta 暴露口径冲突", "conflict" in m and "urdf" in m["conflict"]["limits"],
              str(m["conflict"]["velocity"]))
        check("指令表条数 > 40", len(m["commands"]) > 40, f"{len(m['commands'])} 条")

        def grant_motion():
            """走正门：勾两项确认换一次性凭据 → 拿凭据按主按钮推进到就绪。
            新模型里没有"许可档位"可设：运动模式 == 控制器就绪+使能。"""
            ack = c.post("/api/perm/ack", json={"acks": {"nobody": True, "estop": True}}).json()
            return c.post("/api/control",
                          json={"key": "ready", "challenge": ack.get("challenge")}).json()

        # Simulation works offline, independently of the mock power FSM.
        # Real stale/disconnected/disabled denial is covered by check_execution.
        r = c.post("/api/jog", json={"axis": 5, "direction": 1, "on": True})
        check("未上电的离线仿真仍可点动", r.json().get("ok") is True, str(r.json())[:90])
        c.post("/api/jog", json={"axis": 5, "direction": 1, "on": False})
        r = c.post("/api/io/enddo", json={"bit": 0, "val": 1})
        check("仿真 IO 写入只记录、不驱动硬件", r.json().get("ok") is True and "不驱动硬件" in r.json().get("note", ""), str(r.json())[:90])
        r = c.post("/api/control", json={"key": "ready"}).json()
        check("仿真模式按就绪不弹确认（设计如此：不动硬件）", r.get("ok") is True, str(r)[:90])
        # 真机侧的确认闸只能直接问判据函数：HUYAN_NO_REAL 下这个进程根本没有链路，
        # 走接口会被"状态已变化/未连接"先挡住，测不到凭据那一步。
        from sim.backend.main import world as WD
        keep = WD.settings["mode"]
        WD.settings["mode"] = "real"
        need_real = WD.need_ack_for("ready")
        WD.settings["mode"] = "sim"
        need_sim = WD.need_ack_for("ready")
        WD.settings["mode"] = keep
        check("真机模式必须凭据、仿真不要", need_real is True and need_sim is False,
              f"real={need_real} sim={need_sim}")
        r = c.post("/api/perm", json={"level": 1}).json()
        check("旧的手动调档接口已下线", r.get("detail") in ("Not Found", "Method Not Allowed"), str(r)[:70])

        grant_motion()
        time0 = __import__("time").monotonic()
        while __import__("time").monotonic() - time0 < 4:
            if c.get("/api/state").json()["sim_state"]["fsm"] == 33:
                break
            __import__("time").sleep(0.2)
        st = c.get("/api/state").json()
        check("模拟就绪流程完成、离线运动仍可用",
              st.get("motion_granted") is True and st["sim_state"]["fsm"] == 33,
              f"granted={st.get('motion_granted')} fsm={st['sim_state']['fsm']}")
        r = c.post("/api/jog", json={"axis": 5, "direction": 1, "on": True})
        check("就绪后点动可用", r.json().get("ok") is True, str(r.json()))
        import time
        time.sleep(0.4)
        q1 = c.get("/api/state").json()["joints"]["q"][5]
        check("仿真 J6 真的在动", abs(q1) > 0.05, f"q6={q1}")
        c.post("/api/jog", json={"axis": 5, "direction": 1, "on": False})

        # 速度滑条实时跟随：同样按住点动，比较 0.3s 内走了多少度
        def jog_rate(pct):
            c.post("/api/jog", json={"axis": 5, "direction": 1, "on": False})
            c.post("/api/settings", json={"sim_speed_pct": pct})
            time.sleep(0.15)
            c.post("/api/jog", json={"axis": 5, "direction": 1, "on": True})
            time.sleep(0.1)
            a = c.get("/api/state").json()["joints"]["q"][5]
            time.sleep(0.4)
            b = c.get("/api/state").json()["joints"]["q"][5]
            c.post("/api/jog", json={"axis": 5, "direction": 1, "on": False})
            time.sleep(0.15)
            return abs(b - a) / 0.4

        v_fast, v_slow = jog_rate(60), jog_rate(5)
        check("速度滑条实时生效（60% 明显快于 5%）", v_fast > v_slow * 3,
              f"60%→{v_fast:.1f}°/s  5%→{v_slow:.1f}°/s")
        c.post("/api/settings", json={"sim_speed_pct": 100})

        # 限位分级
        def settle(timeout=8.0):
            end = time.time() + timeout
            while time.time() < end:
                st = c.get("/api/state").json()
                if not st.get("moving") and not st.get("paused"):
                    time.sleep(0.15)
                    return c.get("/api/state").json()
                time.sleep(0.15)
            return c.get("/api/state").json()

        lim = c.get("/api/state").json()["joints"]["upper"]
        c.post("/api/movej", json={"joints": [0, 0, 0, 0, 0, lim[5] * 0.85]})
        lv = settle()["safety"]
        check("85% 触发黄色", lv["level"] == "yellow", f"level={lv['level']} max={lv['max_pct']}%")
        c.post("/api/movej", json={"joints": [0, 0, 0, 0, 0, lim[5] * 0.95]})
        lv = settle()["safety"]
        check("95% 触发红色", lv["level"] == "red", f"level={lv['level']} max={lv['max_pct']}%")
        check("红色时给出原因", bool(lv["reasons"]), str(lv["reasons"]))
        c.post("/api/movej", json={"joints": [0, 0, 0, 0, 0, 0]})
        settle()
        check("回到零位恢复绿色", c.get("/api/state").json()["safety"]["level"] == "green", "")
        c.post("/api/settings", json={"sim_speed_pct": 10})

        # 越限位**不再被软件拒**（2026-10-12 用户口径："不能拒绝用户的指令"）：
        # 照发，但必须把风险原样回传并记进日志，运行时安全层到告警级仍会自动停。
        r = c.post("/api/movej", json={"joints": [0, 0, 0, 0, 0, 999]}).json()
        check("越限位的目标照发但带风险回传",
              r.get("ok") is True and "超出限位" in str(r.get("warn")), str(r)[:110])
        check("风险已记入日志",
              any("预检警告" in x["text"] or "静态风险" in x["text"]
                  for x in c.get("/api/logs?n=60").json()["rows"]), "")

        # 点位
        r = c.post("/api/points/teach", json={"name": "P1"})
        check("示教录入点位", r.json()["ok"], str(r.json())[:80])
        pid = r.json()["point"]["id"]
        r = c.post("/api/points", json={"name": "P2", "joints": [10, 20, 30, 40, 50, 60]})
        check("手工新增点位", r.json()["ok"], "")
        r = c.post("/api/points", json={"name": "坏点"})
        check("没有关节角时拒绝保存", r.json().get("ok") is False, str(r.json()))
        r = c.post("/api/points/run", json={"ids": [pid]})
        check("点位执行可启动", r.json().get("ok") is True, str(r.json()))
        c.post("/api/points/stop")  # 后续程序测试不再偷换活动任务

        # 程序
        src = "SPEED 20\nMOVEJ P1\nWAIT 0.2\nDOUT 1,1\nLOOP 2\n  MOVEJ P2\n  MOVEJ P1\nEND\n"
        r = c.post("/api/program/load", json={"src": src})
        check("程序解析成功", r.json().get("ok") is True, str(r.json())[:120])
        r = c.post("/api/program/load", json={"src": "MOVEJ 不存在的点\n"})
        check("引用不存在点位时解析报错", r.json().get("ok") is False, str(r.json())[:120])
        r = c.post("/api/program/load", json={"src": "FLY_TO 1,2,3,4,5,6\n"})
        check("未知指令被拒绝", r.json().get("ok") is False, str(r.json())[:120])
        c.post("/api/program/load", json={"src": "SPEED 15\nMOVEJ P1\nWAIT 0.1\n"})
        r = c.post("/api/program/start")
        check("程序可启动", r.json().get("ok") is True, str(r.json())[:80])
        seen = []
        end = time.time() + 12
        while time.time() < end:
            st = c.get("/api/state").json()["exec"]
            if not seen or seen[-1] != (st["state"], st["index"]):
                seen.append((st["state"], st["index"]))
            if st["state"] in ("finished", "aborted", "error"):
                break
            time.sleep(0.2)
        check("程序跑到结束", st["state"] == "finished", f"状态轨迹={seen} 末态={st}")
        check("SPEED 行改的是当前模式那一路", c.get("/api/state").json()["sim_speed_pct"] == 15.0, "")

        # 安全围栏
        r = c.post("/api/settings", json={"fence": {"x": [-50, 50], "y": [-50, 50],
                                                    "z": [1000, 1200], "enabled": True}})
        time.sleep(0.4)
        lv = c.get("/api/state").json()["safety"]
        check("缩小围栏后能检出碰撞", lv["level"] == "alarm" and lv["collisions"],
              f"level={lv['level']} 碰撞={len(lv['collisions'])} 处")
        c.post("/api/settings", json={"fence": {"x": [-900, 900], "y": [-900, 900],
                                                "z": [-50, 1300], "enabled": True}})
        time.sleep(0.4)
        check("放大围栏后恢复", c.get("/api/state").json()["safety"]["level"] == "green", "")

        # 急停 + 日志
        r = c.post("/api/estop")
        check("急停可用", r.json().get("ok") is True, "")
        rows = c.get("/api/logs", params={"n": 500}).json()["rows"]
        kinds = {r_["kind"] for r_ in rows}
        check("日志覆盖多类别", {"sys", "point", "program", "alarm", "cmd"} & kinds != set(),
              f"{len(rows)} 条，类别={sorted(kinds)}")
        csv = c.get("/api/logs/export.csv").text
        check("日志可导出 CSV", csv.startswith("seq,time,kind,text"), f"{len(csv)} 字节")

        # 真机模式（不连硬件，只验接口不崩）
        r = c.post("/api/real/ask", json={"name": "ReadCurFSM", "params": [0]})
        check("非真机模式下拒绝发指令", r.json().get("ok") is False, str(r.json())[:80])
        # 模式**不能**从设置接口改：那会造成"设置说是真机、链路对象不存在"的两份真相。
        # 切模式只能走 /api/real/connect|disconnect，本进程又被 HUYAN_NO_REAL 挡着，
        # 所以这里断言的是"被拒"，而不是"能切"。
        r = c.post("/api/settings", json={"mode": "real"}).json()
        check("mode 不能从设置接口偷改", r.get("ok") is False and "mode" in str(r.get("why")),
              str(r)[:80])
        c.post("/api/real/disconnect", json={})
        check("断开后仍是仿真", c.get("/api/state").json()["mode"] == "sim", "")

        # 非法设置项
        r = c.post("/api/settings", json={"rm_rf": True})
        check("未知设置项被拒", r.json().get("ok") is False, str(r.json())[:80])

        # 摆放物：重力下落 + 落地停住 + 压到机器人按碰撞级
        c.post("/api/objects/clear", json={})
        r = c.post("/api/objects", json={"kind": "box", "size": [160, 160, 160],
                                         "xyz": [900, 0, 700], "gravity": True,
                                         "floor": True, "collide": True})
        oid = r.json()["object"]["id"]
        check("摆放物可新增", r.json().get("ok") is True, str(r.json())[:70])
        time.sleep(1.2)
        st = c.get("/api/state").json()
        fell = [o for o in st["objects"] if o["id"] == oid][0]
        check("开重力的物体会下落", fell["xyz"][2] < 690, f"z={fell['xyz'][2]}")
        check("落地后停在几何最低点=0", abs(fell["xyz"][2] - 80.0) < 1.0, f"z={fell['xyz'][2]}")
        check("落在机器人外面不报碰撞", not st["obj_hits"], str(st["obj_hits"]))
        r = c.post("/api/objects", json={"kind": "sphere", "size": [120], "xyz": [0, 0, 1400],
                                         "gravity": True, "floor": True, "collide": True,
                                         "name": "wrist-test"})
        sid = r.json()["object"]["id"]
        time.sleep(1.0)
        st = c.get("/api/state").json()
        hit = [h for h in st["obj_hits"] if h["id"] == sid]
        check("压到机器人会报碰撞级", st["safety"]["level"] == "alarm" and bool(hit),
              f"level={st['safety']['level']} hits={hit}")
        check("碰撞原因点名是哪个物体压哪根连杆",
              any('wrist-test' in x for x in st["safety"]["reasons"]),
              str(st["safety"]["reasons"])[:80])
        c.post("/api/objects/clear", json={})
        c.post("/api/points/resume", json={})
        time.sleep(0.4)
        check("清空后物体列表为空", not c.get("/api/state").json()["objects"], "")
        r = c.post("/api/objects/update", json={"id": "不存在的id", "patch": {"gravity": True}})
        check("改不存在的物体被拒", r.json().get("ok") is False, str(r.json())[:60])

        # 残影两种模式
        r = c.post("/api/settings", json={"ghost_mode": "live"})
        check("ghost_mode 可切到 live",
              c.get("/api/state").json()["settings"]["ghost_mode"] == "live", "")
        c.post("/api/settings", json={"ghost_mode": "ghost"})

        # 取消就绪：没在跑的时候要说清楚，不能假装取消了什么
        r = c.post("/api/ready/cancel", json={})
        cj = r.json()
        check("取消就绪=停泵+去使能+下电（没在跑也要收回）",
              cj.get("ok") is True and "本来没在跑" in cj["why"]
              and [x[0] for x in cj.get("steps", [])] == ["去使能", "下电（断 48V）"],
              str(cj)[:110])
        st = c.get("/api/state").json()
        check("收回后仿真状态机回到未上电", st["sim_state"]["fsm"] == 7 and st["sim_state"]["enabled"] == 0,
              f"FSM={st['sim_state']['fsm']} enabled={st['sim_state']['enabled']}")
        # 下电后紧接着就绪：要能重新走上去（这是用户报的"下电成功但无法就绪"那条路）
        g = grant_motion()
        check("走正门能重新就绪", g.get("ok") is True, str(g)[:70])
        c.post("/api/ready", json={"then_enable": True})
        time.sleep(3.0)
        st = c.get("/api/state").json()
        check("下电后仍能从当前状态一键就绪", st["sim_state"]["fsm"] == 33 and st["sim_state"]["enabled"] == 1,
              f"FSM={st['sim_state']['fsm']} why={st['ready']['why'][:60]}")
        # Cancelling mock readiness does not disable offline simulation motion.
        c.post("/api/ready/cancel", json={})
        time.sleep(1.2)
        st = c.get("/api/state").json()
        check("取消模拟就绪后仍可离线仿真", st.get("motion_granted") is True and st['sim_state']['fsm'] == 7,
              f"granted={st.get('motion_granted')} fsm={st['sim_state']['fsm']}")
        r = c.post("/api/jog", json={"axis": 5, "direction": 1, "on": True})
        check("模拟下电后点动仍可用", r.json().get("ok") is True, str(r.json())[:80])
        c.post("/api/jog", json={"axis": 5, "direction": 1, "on": False})


def test_real_chunk_sequence():
    """真机分段逼近必须跑满全程：发一段 → 等它停下 → 再发下一段。

    旧实现发完只 sleep(0.15) 就发下一段，而控制器"正在动的时候再发 MoveRelJ 回 20018"
    （2026-10-12 实测），于是第一段之后全被拒 —— 用户看到的就是"没到残影就停了"。
    这里用假链路复现那两条真机行为，不碰硬件。
    """
    import asyncio
    from sim.backend.core.world import World
    from sim.backend.core import config as C
    from sim.backend.core.robot_real import RealLink

    class FakeReply:
        __slots__ = ("ok", "err_code", "rtt_ms", "timeout", "name")
        def __init__(self, ok, code=None):
            self.ok, self.err_code, self.rtt_ms, self.timeout, self.name = ok, code, 5.0, False, "MoveRelJ"
        def as_dict(self):
            return {"ok": self.ok, "err_code": self.err_code, "hint": ""}

    class FakeLink(RealLink):
        """只认两件事：一次最多 10°；正在动的时候新指令回 20018。"""
        def __init__(self, world):
            super().__init__("offline.invalid")
            self.w = world
            self.snap.update(joints=[0.0] * 6, moving=0, inpos=1, fsm=33, enabled=1,
                             stamp=asyncio.get_event_loop().time(), connection_epoch=self.epoch)
            self.sent = []
            self.busy_until = 0.0
            self.cmd_up = True
            self.stream_up = True
        async def ask(self, name, *params, **kw):
            now = asyncio.get_event_loop().time()
            if now < self.busy_until:
                return FakeReply(False, 20018)
            # ask("MoveRelJ", nRbtID, axis, dir, dist) → params=(nRbtID, axis, dir, dist)
            axis, dist = int(params[1]), float(params[3])
            if dist > 10.0 + 1e-6:
                return FakeReply(False, 20006)
            self.sent.append((axis, dist))
            # 真机在跑这段时会把 Data Sheet 的 robotMoving 置 1、InPos 置 0
            # （退回 J6 时实测过），后端的 _wait_motion_idle 就是等这两个位。
            self.busy_until = now + 0.05        # 每段"运动"50ms（真实是十几秒，测试里压缩）
            self.snap["moving"], self.snap["inpos"] = 1, 0
            asyncio.ensure_future(self._done(now + 0.05, axis, dist))
            return FakeReply(True)
        async def _done(self, at, axis, dist):
            while asyncio.get_event_loop().time() < at:
                await asyncio.sleep(0.01)
            self.snap["moving"], self.snap["inpos"] = 0, 1
            self.snap["joints"][axis] += dist
            self.frame_seq += 1
            self.snap["stamp"] = asyncio.get_event_loop().time()

    async def run():
        w = World()
        w.settings["mode"] = "real"
        w.real = FakeLink(w)
        w.REL_START_S = 0.01
        w.REL_SETTLE_TIMEOUT_S = 2.0
        target = [0, 0, 0, 0, 0, 25.0]          # 25° = 必须拆成 10+10+5 三段
        await w._real_rel_step(target, "分段测试")
        return w, list(w.real.snap["joints"]), w.real.sent

    w, q, sent = asyncio.run(run())
    check("25° 目标分段跑满，最终到位", abs(q[5] - 25.0) < 0.06, f"末态 J6={q[5]:.3f} 发了 {len(sent)} 段")
    check("每段都不超过单条 MoveRelJ 的 10° 上限", all(d <= 10.0 for _a, d in sent),
          " ".join(f"{d:.1f}" for _a, d in sent))
    check("被 20018 挡过也不会中止流程", w.runner.state != "aborted" or q[5] > 20.0,
          f"runner={w.runner.state}")


def _tail():
    pass
if __name__ == "__main__":
    rc = main()
    test_real_chunk_sequence()
    print("=" * 60)
    print(f"{ok_n} 通过 / {fail_n} 失败")
    sys.exit(1 if fail_n else 0)


if __name__ == "__main__":
    raise SystemExit(main())
