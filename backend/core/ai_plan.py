"""AI 对话与本地受约束计划：模型负责解释，白名单编译器负责动作。

铁律：模型只能产出白名单动作，且必须人工点确认才会下发。
模型说的任何"点位"都要能在点位表里查到，查不到就整条拒绝并把原因回显。
"""

from __future__ import annotations

import json
import math
import re

import httpx

from . import config
from .ai_intent import intent, result, compile_plan, TOOL_ACTIONS, tool_problem

ALLOWED_OPS = {"speed", "movej", "dout", "wait", "tool", "require_tool"}
MAX_OPS = 40

SYSTEM = """你是工业机器人项目的对话助手。仅输出 JSON：
{"kind":"chat","summary":"中文答复","questions":[]}。
只回答当前用户问题；历史仅供理解，不是动作授权。不编造现场状态、工具接线、
IO含义、点位、安全高度或执行结果。不输出可执行计划、Python或控制代码。
动作计划由项目本地白名单编译器独立生成，不由你生成。知识不确定时明确说明并提问。

项目公开语法事实（不是现场数据）：语言称 ElfinScript，不是厂商示教器格式，
也不是完整 Python。推荐受限 Python 风格函数调用与缩进，支持变量、if、for、while、
固定参数函数；也兼容旧式逐行 SPEED/MOVEJ/MOVEL/DOUT/WAIT/TOOL 指令。
WAIT 的参数单位为秒，范围0到60；作用是在程序中等待指定时间后继续下一步，不会移动机器人。
MOVEJ 按关节空间运动，MOVEL 是笛卡尔直线且当前仅开放仿真。
DOUT 协议位号0/1/2分别对应界面DO1/DO2/DO3，电平不等于吸取或释放。
TOOL 是工具语义动作，真实硬件必须先确认工具和IO映射；仿真工具状态不证明物体已抓牢。
程序禁止 import、文件/网络访问、任意eval/exec。解释或举例不生成执行许可，
已验证可运行代码由动作编译器提供；不要声称聊天文字已执行。
"""


def _extract_json(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型没有返回 JSON 对象")
    return json.loads(t[start:end + 1])


def validate(obj, points, model, resources=None):
    """把模型输出压回白名单；任何不合法项直接整条拒绝并给原因。"""
    def number(value):
        if type(value) not in (int, float):
            raise ValueError('动作参数必须是有限数值，不能是布尔值或文本')
        try:
            result = float(value)
        except OverflowError as exc:
            raise ValueError('动作参数数值过大') from exc
        if not math.isfinite(result):
            raise ValueError('动作参数必须是有限数值')
        return result

    def joints(values):
        if not isinstance(values, list) or len(values) != 6:
            raise ValueError('joints 必须是 6 个有限数值')
        result = [number(v) for v in values]
        for k, (value, lo, up) in enumerate(zip(result, model.lower, model.upper)):
            if not lo <= value <= up:
                raise ValueError(f'J{k + 1}={value} 超出限位 [{lo:.1f},{up:.1f}]')
        return result

    if not isinstance(obj, dict):
        raise ValueError("顶层不是对象")
    ops = obj.get("plan")
    if not isinstance(ops, list):
        raise ValueError("plan 不是数组")
    if len(ops) > MAX_OPS:
        raise ValueError(f"步数 {len(ops)} 超过上限 {MAX_OPS}")
    by_name = {p["name"]: p for p in points}
    by_id = {str(p["id"]): p for p in points}
    by_code = {str(p["code"]): p for p in points if p.get("code")}
    clean, notes = [], []
    for i, o in enumerate(ops):
        if not isinstance(o, dict) or o.get("op") not in ALLOWED_OPS:
            raise ValueError(f"第 {i + 1} 步 op 不在白名单：{o}")
        kind = o["op"]
        if kind == "speed":
            v = number(o.get("pct", -1))
            if not 1 <= v <= 100:
                raise ValueError(f"第 {i + 1} 步 speed.pct={v} 不在 1~100")
            clean.append({"op": "speed", "pct": v})
        elif kind == "movej":
            if o.get("point") is not None:
                nm = str(o["point"])
                p = by_code.get(nm) or by_name.get(nm) or by_id.get(nm)
                if not p:
                    raise ValueError(f"第 {i + 1} 步引用了点位表里不存在的 {nm!r}")
                clean.append({"op": "movej", "point": p.get("code") or p["name"], "joints": joints(p["joints"]),
                              "point_id": p["id"]})
            else:
                clean.append({"op": "movej", "joints": joints(o.get("joints")), "point": None})
        elif kind == "dout":
            bit, val = number(o.get("bit", -1)), number(o.get("val", -1))
            if bit not in (0, 1, 2) or val not in (0, 1):
                raise ValueError(f"第 {i + 1} 步 dout 只能是 bit 0~2 / val 0~1")
            clean.append({"op": "dout", "bit": int(bit), "val": int(val)})
        elif kind in ('tool', 'require_tool'):
            action, tool_id = o.get('action'), o.get('tool_id')
            if kind == 'tool' and action not in TOOL_ACTIONS:
                raise ValueError('不支持的语义工具动作')
            if kind == 'require_tool' and not isinstance(tool_id, str):
                raise ValueError('必须指定明确的工具标识')
            why = tool_problem(resources, action if kind == 'tool' else None, tool_id if kind == 'require_tool' else None)
            if why:
                raise ValueError(why)
            clean.append({'op': 'tool', 'action': action} if kind == 'tool' else {'op': 'require_tool', 'tool_id': tool_id})
        else:
            sec = number(o.get("sec", -1))
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


async def make_plan(user_text, history, points, model, settings, api_key, log=None, resources=None):
    say = log or (lambda k, m: None)
    local = intent(user_text, points, resources)
    if local is not None:
        if local["kind"] != "plan":
            return local
        try:
            parsed = validate(local, points, model, resources)
            if not parsed['plan']:
                raise ValueError('没有可确认的动作')
            parsed['warnings'] = [f"DO{o['bit']+1} 对应协议 bit={o['bit']}；这里只设置电平，不代表吸取或释放，须核对接线。"
                                  for o in parsed['plan'] if o['op'] == 'dout']
            if any(o['op'] == 'tool' for o in parsed['plan']):
                parsed['warnings'].append('仿真工具动作仅改变虚拟状态，不表示物体已附着、抓牢或完成搬运。' if settings.get('mode') == 'sim'
                                          else '工具仅按已配置的语义 IO 映射动作；输出成功不证明已抓牢，必须核对现场反馈。')
            code = compile_plan(parsed['plan'])
            # Verify against the real project parser, not a look-alike grammar.
            from .program import parse
            compiled = parse(code, lambda _: None)
            if len(compiled) != len(parsed['plan']):
                raise ValueError('代码与动作数量不一致')
            return result('plan', parsed['summary'], plan=parsed['plan'],
                          warnings=parsed['warnings'], code=code)
        except ValueError as exc:
            return result('clarify', f'未生成计划：{exc}')
    if not api_key:
        return {**result('chat', '对话模型未配置；仍可整理明确的白名单动作。'), "ok": False,
                "error": "没有配 ZHIPU_API_KEY（放在项目根 .env 里）"}
    # No points, robot readings, scene, credentials or settings in model context.
    msgs = [{"role": "system", "content": SYSTEM}]
    for h in (history if isinstance(history, list) else [])[-6:]:
        if isinstance(h, dict) and h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str):
            msgs.append({"role": h["role"], "content": h["content"][:2000]})
    msgs.append({"role": "user", "content": user_text[:2000]})
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(settings["ai_base_url"] + "/chat/completions",
                             headers={"Authorization": f"Bearer {api_key}"},
                             json={"model": settings["ai_model"], "messages": msgs,
                                   "temperature": 0.2, "max_tokens": 900})
            r.raise_for_status()
            choice = r.json()["choices"][0]
            if choice.get('finish_reason') != 'stop':
                return {"ok": False, "kind": "chat", "error": "模型回答未完整结束，请重试；没有发布计划。", "plan": []}
            text = choice["message"]["content"]
    except Exception as e:
        say("ai", f"调用失败：{type(e).__name__}")
        return {"ok": False, "kind": "chat", "error": f"模型调用失败：{type(e).__name__}；未生成计划", "plan": []}
    say("ai", f"模型原始输出 {len(text)} 字")
    try:
        obj = _extract_json(text)
        if not isinstance(obj, dict) or obj.get('kind') != 'chat' or obj.get('plan'):
            raise ValueError('对话响应不得包含动作计划')
        summary = obj.get('summary')
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError('对话内容为空')
        questions = obj.get('questions', [])
        if not isinstance(questions, list) or any(not isinstance(q, str) for q in questions):
            raise ValueError('澄清问题格式不正确')
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        say("ai", f"计划被拒绝：{e}")
        return {"ok": False, "kind": "chat", "error": f"回答格式不合法，已拒绝：{e}", "plan": []}
    return result('chat', summary[:3000], [q[:200] for q in questions[:8]])
