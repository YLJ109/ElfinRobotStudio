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
PLAN_SYSTEM = """仅理解本轮完整要求，不执行动作。只输出JSON。
能完整表达时输出{"kind":"plan","summary":"中文摘要","current_request":"逐字复制本轮原文","unhandled":[],"plan":[{"op":"movej","point":"原文明示P编号"}]}。
其他白名单动作：speed/pct、wait/sec、dout/bit(0到2)/val(0或1)、tool/action、require_tool/tool_id。
全部点位、数值、操作必须来自本轮，不从历史补足。不输出joints、新坐标或代码。
仿真支持有限绘图：{"op":"drawing","shape":"heart|circle|square|rectangle"}。
可单独画图，或先运动到原文明示P点再画；drawing必须是最后一步，不能用MOVEJ冒充绘图。
不能省略条件或后缀。写字、分拣、棋局和跟随尚未接入AI组合。
不能完整表达、问句或否定时输出{"kind":"chat","summary":"明确说明未支持或缺失部分","questions":[]}。
模型仅整理意图，动作仍需本地校验及用户执行。DO电平不代表吸放，不猜测工艺或工具映射。
"""

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
        fields={'speed':{'op','pct'},'movej':{'op','point','joints','point_id'},'dout':{'op','bit','val'},'wait':{'op','sec'},'tool':{'op','action'},'require_tool':{'op','tool_id'}}[kind]
        if set(o)-fields:raise ValueError(f'第{i+1}步含未消费字段')
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


def skill_proposal(ops,points,model,settings,resources=None,*,expected=None):
    """Validate a full finite task; expanded motion is checked before any move."""
    if settings.get('mode')!='sim':raise ValueError('AI自动绘制组合仅用于仿真')
    if not isinstance(ops,list) or len(ops) not in (1,2):raise ValueError('绘制任务仅支持可选起点加一个图案')
    drawing=ops[-1]
    if not isinstance(drawing,dict) or set(drawing)!={'op','shape'} or drawing.get('op')!='drawing' or drawing.get('shape') not in ('heart','circle','square','rectangle'):
        raise ValueError('绘制任务图案无效')
    if len(ops)==2 and (not isinstance(ops[0],dict) or ops[0].get('op')!='movej' or not ops[0].get('point')):raise ValueError('绘制前只能指定一个已保存起点')
    if expected is not None:
        expected_ops=expected['plan']
        if len(ops)!=len(expected_ops) or drawing!=expected_ops[-1] or (len(ops)==2 and ops[0].get('point')!=expected_ops[0].get('point')):
            raise ValueError('模型任务没有完整匹配本轮起点和图案')
    clean=validate({'plan':ops[:-1]},points,model,resources)['plan']+[dict(drawing)]
    return result('plan','点击执行后机器人完成'+('起点移动和' if len(clean)==2 else '')+'绘制。',plan=clean,
                  skill_plan={'kind':'drawing','shape':drawing['shape']},code='')


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
    base = {"ok": False, "code": code, "ctx": ctx, "advice": "",
            "disclaimer": "AI 推测，不是官方诊断。控制器原始回执见同屏的「原始读数」。"}
    if not api_key:
        return {**base, "why": "AI 助手未配置 API Key（助手标题栏「配置」里填写），只能看原始读数",
                "likely": []}
    safe = {k: _clean(v, 120) for k, v in ctx.items() if not isinstance(v, (list, dict))}
    payload = {"读数": safe,
               "axis_err": _clean(ctx.get("axis_err"), 80),
               "temps": _clean(ctx.get("temps"), 120),
               "controller_str": _clean(ctx.get("controller_str"), 200)}
    msgs = [{"role": "system", "content": EXPLAIN_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    base_url, model = config.ai_endpoint()
    try:
        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.post(base_url + "/chat/completions",
                             headers={"Authorization": f"Bearer {api_key}"},
                             json={"model": model, "messages": msgs,
                                   "temperature": 0.2, "max_tokens": 700})
            r.raise_for_status()
            choice = r.json()["choices"][0]
            if choice.get('finish_reason') != 'stop':
                raise ValueError('模型回答未完整结束')
            text = choice["message"]["content"]
            if not isinstance(text, str) or len(text) > 8000:
                raise ValueError('模型回答类型或长度不正确')
    except Exception as e:
        say("ai", f"故障解说调用失败：{type(e).__name__} {e}")
        return {**base, "why": f"模型调用失败：{type(e).__name__}，先看原始读数", "likely": []}
    try:
        obj = _extract_json(text)
        if not isinstance(obj, dict):
            raise ValueError('顶层必须是对象')
        rows = obj.get("likely")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 4:
            raise ValueError('likely 必须包含1至4条建议')
        def field(value, limit, label):
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f'{label} 必须是非空且不超长的文本')
            cleaned = _clean(value, limit)
            if not cleaned.strip():
                raise ValueError(f'{label} 为空')
            return cleaned
        out = []
        for x in rows:
            if not isinstance(x, dict):
                raise ValueError('likely 中每条建议必须是对象')
            out.append({key: field(x.get(key), limit, key)
                        for key,limit in (('cause',160),('check',300),('fix',300))})
        risk = field(obj.get('risk'), 200, 'risk')
        need_vendor = field(obj.get('need_vendor'), 200, 'need_vendor')
        say("ai", f"故障 {code} 解说：给了 {len(out)} 条推测")
        return {**base, "ok": True, "likely": out, "risk": risk,
                "need_vendor": need_vendor, "raw_model": text[:800]}
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        say("ai", f"故障解说输出不是合法 JSON：{e}")
        return {**base, "why": f"模型输出无法解析（{e}），这条不作为依据", "likely": [],
                "raw_model": text[:800]}


async def make_plan(user_text, history, points, model, settings, api_key, log=None, resources=None, *, allow_model=False):
    say = log or (lambda k, m: None)
    def provenance(value, source, status=None, attempted=False):
        value['source_kind']=source
        if status is not None:
            value['model_request']={'attempted':attempted,'status':status,'model':settings.get('ai_model','')}
        return value
    local = intent(user_text, points, resources)
    interpret=bool(local and local.get('kind') in ('clarify','skill_plan') and isinstance(user_text,str) and 0<len(user_text)<=2000 and allow_model is True and api_key)
    if local and local.get('kind')=='skill_plan' and settings.get('mode')!='sim':
        return provenance(result('clarify','AI自动绘制组合仅用于仿真，真机不会自动换工具或执行。'),'local_rules')
    if local and local.get('kind')=='skill_plan' and not interpret:
        try:return provenance(skill_proposal(local['plan'],points,model,settings,resources),'local_rules')
        except ValueError as exc:return provenance(result('clarify',str(exc)),'local_rules')
    if local is not None and not interpret:
        if local["kind"] != "plan":
            return provenance(local, 'local_rules')
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
            return provenance(result('plan', parsed['summary'], plan=parsed['plan'],
                          warnings=parsed['warnings'], code=code), 'local_rules')
        except ValueError as exc:
            return provenance(result('clarify', f'未生成计划：{exc}'), 'local_rules')
    if not api_key:
        return provenance({**result('chat', '对话模型未配置；仍可整理明确的白名单动作。'), "ok": False,
                "error": "未配置对话模型；可在设置中配置。"}, "not_generated", "not_configured")
    if allow_model is not True:
        return provenance({**result('chat','本轮未授权发送对话；可使用本地规则，或明确允许后重新发送。'),'ok':False,'error':'本轮未授权发送对话，未调用模型。'},'not_generated')
    # No points, robot readings, scene, credentials or settings in model context.
    msgs = [{"role": "system", "content": PLAN_SYSTEM if interpret else SYSTEM}]
    # 答疑时如果连"有哪些点位"都不告诉模型，用户问"P1 能去吗"就只能换来
    # 一句"我不知道 P1 是哪个点"。这里只补名字，不带坐标、关节值和限位：
    # 名字是用户自己起的，且后续任何动作仍要在点位表里校验，不增加自由度。
    if interpret:
        names = [str(p.get('code') or p.get('name')) for p in (points or [])
                 if isinstance(p, dict) and (p.get('code') or p.get('name'))]
        if names:
            msgs[0]["content"] += ('\n\n当前已保存的点位名称（你能看到的只有名字，'
                                   '坐标与关节值对你是不可见的）：' + '、'.join(names[:60])
                                   + '。判断某点能否到达时只能依据这份名单和用户描述，'
                                   '不要编造坐标、关节值或工作空间尺寸。')
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
                return provenance({"ok": False, "kind": "chat", "error": "模型回答未完整结束，请重试；没有发布计划。", "plan": [], "code": ""}, "not_generated", "invalid_response", True)
            text = choice["message"]["content"]
            if not isinstance(text,str) or not text.strip() or len(text)>16000:
                raise ValueError('模型回答类型或长度不正确')
    except Exception as e:
        say("ai", f"调用失败：{type(e).__name__}")
        status='rate_limited' if isinstance(e,httpx.HTTPStatusError) and e.response.status_code==429 else 'invalid_response' if isinstance(e,(ValueError,KeyError,TypeError,IndexError)) else 'connection_failed'
        return provenance({"ok": False, "kind": "chat", "error": '模型请求限流，请稍后重试。' if status=='rate_limited' else '模型响应无效；未生成计划。' if status=='invalid_response' else '模型连接失败；未生成计划。', "plan": [], "code": ""}, "not_generated", status, True)
    say("ai", f"模型原始输出 {len(text)} 字")
    try:
        obj = _extract_json(text)
        if interpret and local.get('unhandled_skills'):
            if not isinstance(obj,dict) or obj.get('kind')!='chat' or obj.get('plan'):
                return provenance({**local,'error':'不能省略未接入的后续任务；没有发布部分计划。'},'local_rules','invalid_response',True)
            if not isinstance(obj.get('summary'),str) or not obj['summary'].strip():raise ValueError('模型解释为空')
            return provenance({**local,'model_summary':obj['summary'][:1000]},'model','success',True)
        if interpret and isinstance(obj,dict) and obj.get('kind')=='plan':
            if set(obj)-{'kind','summary','current_request','unhandled','plan','warnings'} or obj.get('current_request')!=user_text or obj.get('unhandled')!=[]:
                raise ValueError('模型没有完整消费本轮要求')
            if not isinstance(obj.get('summary'),str) or not obj['summary'].strip():raise ValueError('模型摘要为空')
            if re.search(r'[?？]|不要|不能|禁止|别(?:动|走|去|移动|执行)|不(?:许|能|准)|解释|为什么|如何|怎么|假如|如果|是否|能否|可以吗',user_text):raise ValueError('问句或否定不能成为动作许可')
            if re.search(r'循环|重复|往返|直到|碰到|遇到|条件|避障|安全高度',user_text):raise ValueError('不能省略尚未接入的条件或循环')
            if local.get('kind')=='skill_plan':
                value=skill_proposal(obj.get('plan'),points,model,settings,resources,expected=local)
                return provenance(value,'model','success',True)
            if re.search(r'工具|接近|撤离|TCP|实际安装|抓放|IO',local.get('summary',''),re.I):raise ValueError('工艺条件仍未完整，模型不能补造')
            if not isinstance(obj.get('plan'),list):raise ValueError('plan不是数组')
            for op in obj['plan']:
                if not isinstance(op,dict):raise ValueError('动作必须是对象')
                kind=op.get('op')
                if kind=='movej':
                    point=op.get('point')
                    if set(op)!={'op','point'} or not isinstance(point,str) or not re.search(r'(?<![A-Za-z0-9_])'+re.escape(point)+r'(?![A-Za-z0-9_])',user_text):raise ValueError('只能使用本轮明确点位')
                elif kind in ('speed','wait'):
                    number=op.get('pct' if kind=='speed' else 'sec')
                    unit=r'[%％]' if kind=='speed' else r'(?:秒|s)(?![a-z])'
                    if type(number) not in (int,float) or not re.search(r'(?<![\d.])'+re.escape(format(number,'g'))+r'\s*'+unit,user_text,re.I):raise ValueError('动作数值及其单位必须来自本轮原文')
                elif kind=='dout':
                    bit=op.get('bit')
                    match=re.search(r'DO'+str(bit+1)+r'\s*(?:设为|置为|置|=|输出)?\s*(打开|开启|关闭|断掉|关掉|断开|接通|通电|断电|开|关|[01])(?!\d)',user_text,re.I) if type(bit) is int else None
                    if not match or op.get('val')!=int(match[1] in ('打开','开启','接通','通电','开','1')):raise ValueError('IO目标和电平必须来自本轮原文')
                elif kind in ('tool','require_tool'):
                    if not re.search(r'工具|TOOL',user_text,re.I):raise ValueError('不能添加原文没有的工具动作')
            parsed=validate(obj,points,model,resources)
            if not parsed['plan']:raise ValueError('没有完整动作')
            code=compile_plan(parsed['plan'])
            from .program import parse
            if len(parse(code,lambda _:None))!=len(parsed['plan']):raise ValueError('代码与动作数量不一致')
            return provenance(result('plan',parsed['summary'],plan=parsed['plan'],warnings=parsed['warnings'],code=code),'model','success',True)
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
        return provenance({"ok": False, "kind": "chat", "error": f"回答格式不合法，已拒绝：{e}", "plan": [], "code": ""}, "not_generated", "invalid_response", True)
    return provenance(result('chat', summary[:3000], [q[:200] for q in questions[:8]]), 'model', 'success', True)
