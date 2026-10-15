"""AI 执行：智谱 glm-4-flash 生成「受约束的动作计划」。

铁律：模型只能产出白名单动作，且必须人工点确认才会下发。
模型说的任何"点位"都要能在点位表里查到，查不到就整条拒绝并把原因回显。
"""

from __future__ import annotations

import json
import math
import re

import httpx

from . import config

ALLOWED_OPS = {"speed", "movej", "dout", "wait"}
MAX_OPS = 40

SYSTEM = """你是工业机器人（华沿 Elfin E05，六轴）的动作规划器。
你只能输出一个 JSON 对象，禁止输出解释文字、禁止 markdown 代码块。
JSON 结构固定为：
{"plan":[{"op":"speed","pct":10},{"op":"movej","point":"P1"},{"op":"movej","joints":[0,0,0,0,0,0]},
{"op":"dout","bit":0,"val":1},{"op":"wait","sec":0.5}],
 "summary":"一句话说明", "warnings":["需要人确认的风险"]}

规则：
1. 只允许 speed / movej / dout / wait 四种动作，最多 40 步。
2. speed 的 pct 只能是 1~100 的数字，代表全局速度百分比。
3. movej 只能用给定点位表里存在的 point 名称，或者明确给出 6 个关节角 joints（单位度）。
   不许自己编造点位名，不许给笛卡尔坐标。
4. dout 的 bit 是**从 0 开始**的协议位号：用户说"DO1"要写 bit=0，"DO2"写 bit=1，"DO3"写 bit=2。
   只能取 0/1/2，val 只能 0/1。凡涉及 dout，必须在 warnings 里回一句"你说的 DOn 我映射成 bit=n-1"，
   让人核对 —— 现场接线未知，位号错了就是开错阀门。
5. wait 的 sec 只能是 0~60。
6. 现场接线（哪个阀接哪一路）你不知道，涉及 dout 时必须写进 warnings。
7. 如果用户的要求超出以上能力，输出 {"plan":[], "summary":"无法执行：原因", "warnings":[...]}
"""


def _extract_json(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型没有返回 JSON 对象")
    return json.loads(t[start:end + 1])


def validate(obj, points, model):
    """把模型输出压回白名单；任何不合法项直接整条拒绝并给原因。"""
    if not isinstance(obj, dict):
        raise ValueError("顶层不是对象")
    ops = obj.get("plan")
    if not isinstance(ops, list):
        raise ValueError("plan 不是数组")
    if len(ops) > MAX_OPS:
        raise ValueError(f"步数 {len(ops)} 超过上限 {MAX_OPS}")
    by_name = {p["name"]: p for p in points}
    by_id = {str(p["id"]): p for p in points}
    clean, notes = [], []
    for i, o in enumerate(ops):
        if not isinstance(o, dict) or o.get("op") not in ALLOWED_OPS:
            raise ValueError(f"第 {i + 1} 步 op 不在白名单：{o}")
        kind = o["op"]
        if kind == "speed":
            v = float(o.get("pct", -1))
            if not 1 <= v <= 100:
                raise ValueError(f"第 {i + 1} 步 speed.pct={v} 不在 1~100")
            clean.append({"op": "speed", "pct": v})
        elif kind == "movej":
            if o.get("point") is not None:
                nm = str(o["point"])
                p = by_name.get(nm) or by_id.get(nm)
                if not p:
                    raise ValueError(f"第 {i + 1} 步引用了点位表里不存在的 {nm!r}")
                clean.append({"op": "movej", "point": p["name"], "joints": p["joints"],
                              "point_id": p["id"]})
            else:
                j = o.get("joints")
                if not isinstance(j, list) or len(j) != 6 or any(
                        not isinstance(v, (int, float)) or math.isnan(float(v)) for v in j):
                    raise ValueError(f"第 {i + 1} 步 joints 必须是 6 个数值")
                for k, (t, lo, up) in enumerate(zip(j, model.lower, model.upper)):
                    if not (lo <= float(t) <= up):
                        raise ValueError(f"第 {i + 1} 步 J{k + 1}={t} 超出限位 [{lo:.1f},{up:.1f}]")
                clean.append({"op": "movej", "joints": [float(v) for v in j], "point": None})
        elif kind == "dout":
            bit, val = int(o.get("bit", -1)), int(o.get("val", -1))
            if bit not in (0, 1, 2) or val not in (0, 1):
                raise ValueError(f"第 {i + 1} 步 dout 只能是 bit 0~2 / val 0~1")
            clean.append({"op": "dout", "bit": bit, "val": val})
        else:
            sec = float(o.get("sec", -1))
            if not 0 <= sec <= 60:
                raise ValueError(f"第 {i + 1} 步 wait.sec={sec} 不在 0~60")
            clean.append({"op": "wait", "sec": sec})
    return {"plan": clean,
            "summary": str(obj.get("summary", ""))[:400],
            "warnings": [str(w)[:200] for w in (obj.get("warnings") or [])][:8] + notes}


EXPLAIN_SYSTEM = """你在协助一名工业机器人现场运维人员读故障。机型：华沿 Elfin E05（六轴）。
你会拿到一份**控制器原始状态读数**（JSON）。只依据这份读数推理，不许编造没给出的数值。
输出一个 JSON 对象，不要 markdown 代码块：
{"likely":[{"cause":"最可能原因","check":"怎么确认（一步一步、可执行）","fix":"处置"}],
 "risk":"在处理之前必须先做的一件事（比如先确认无人、先拍急停）",
 "need_vendor":"什么情况下必须找厂家/不要自己动"}
最多 4 条 likely，按可能性从高到低。语言：中文，短句，不要客套。
读数里没有把握的，直接说"无法从现有读数判断"，不要硬凑。
"""


def _clean(v, n=200):
    """把来自控制器的文本压成安全长度：错误说明是外部输入，会进 prompt，
    所以要限长、去掉换行与控制字符，免得一条怪回执把指令区改掉。"""
    s = str(v if v is not None else "")
    s = "".join(ch for ch in s if ch == " " or (32 <= ord(ch) < 127) or ord(ch) > 0x2E80)
    return s[:n]


async def explain(ctx, api_key, log=None):
    """故障码解说：输出必须与控制器原始回执并列，标清哪部分是 AI 建议。"""
    say = log or (lambda k, m: None)
    code = ctx.get("code")
    base = {"ok": True, "code": code, "ctx": ctx, "advice": "",
            "disclaimer": "AI 推测，不是官方诊断。控制器原始回执见同屏的「原始读数」。"}
    if not api_key:
        return {**base, "why": "没有配 ZHIPU_API_KEY（放项目根 .env），只能看原始读数",
                "likely": []}
    safe = {k: _clean(v, 120) for k, v in ctx.items() if not isinstance(v, (list, dict))}
    payload = {"读数": safe,
               "axis_err": _clean(ctx.get("axis_err"), 80),
               "temps": _clean(ctx.get("temps"), 120),
               "controller_str": _clean(ctx.get("controller_str"), 200)}
    msgs = [{"role": "system", "content": EXPLAIN_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    try:
        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.post(config.get_settings()["ai_base_url"] + "/chat/completions",
                             headers={"Authorization": f"Bearer {api_key}"},
                             json={"model": config.get_settings()["ai_model"], "messages": msgs,
                                   "temperature": 0.2, "max_tokens": 700})
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        say("ai", f"故障解说调用失败：{type(e).__name__} {e}")
        return {**base, "why": f"模型调用失败：{type(e).__name__}，先看原始读数", "likely": []}
    try:
        obj = _extract_json(text)
        rows = obj.get("likely") or []
        out = []
        for x in rows[:4]:
            if not isinstance(x, dict):
                continue
            out.append({"cause": _clean(x.get("cause"), 160),
                        "check": _clean(x.get("check"), 300),
                        "fix": _clean(x.get("fix"), 300)})
        say("ai", f"故障 {code} 解说：给了 {len(out)} 条推测")
        return {**base, "likely": out, "risk": _clean(obj.get("risk"), 200),
                "need_vendor": _clean(obj.get("need_vendor"), 200), "raw_model": text[:800]}
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        say("ai", f"故障解说输出不是合法 JSON：{e}")
        return {**base, "why": f"模型输出无法解析（{e}），这条不作为依据", "likely": [],
                "raw_model": text[:800]}


async def make_plan(user_text, history, points, model, settings, api_key, log=None):
    say = log or (lambda k, m: None)
    if not api_key:
        return {"ok": False, "error": "没有配 ZHIPU_API_KEY（放在项目根 .env 里）"}
    ctx = {
        "点位表": [{"name": p["name"], "joints": p["joints"]} for p in points],
        "当前关节角": None,
        "限位": [[round(a, 1), round(b, 1)] for a, b in zip(model.lower, model.upper)],
        "当前速度百分比": settings[config.speed_key(settings.get("mode", "sim"))],
    }
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": "可用资源：" + json.dumps(ctx, ensure_ascii=False)}]
    for h in (history or [])[-6:]:
        if h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str):
            msgs.append({"role": h["role"], "content": h["content"][:2000]})
    msgs.append({"role": "user", "content": user_text[:2000]})
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(settings["ai_base_url"] + "/chat/completions",
                             headers={"Authorization": f"Bearer {api_key}"},
                             json={"model": settings["ai_model"], "messages": msgs,
                                   "temperature": 0.2, "max_tokens": 900})
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        say("ai", f"调用失败：{type(e).__name__} {e}")
        return {"ok": False, "error": f"模型调用失败：{type(e).__name__} {e}"}
    say("ai", f"模型原始输出 {len(text)} 字")
    try:
        parsed = validate(_extract_json(text), points, model)
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        say("ai", f"计划被拒绝：{e}")
        return {"ok": False, "error": f"计划不合法，已拒绝：{e}", "raw": text[:1500]}
    say("ai", f"生成 {len(parsed['plan'])} 步计划：{parsed['summary']}")
    return {"ok": True, **parsed, "raw": text[:1500]}
