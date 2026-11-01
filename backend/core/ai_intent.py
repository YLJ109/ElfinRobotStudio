"""Current-message intent contract. No model output can grant an action.

Deliberately bounded language: ambiguous process requests are questions, not
partially executed recipes. History supplies conversation, never authorization.
"""
import re
from ..domain.tool_actions import TOOL_ACTIONS


def result(kind, summary, questions=(), **extra):
    return {"ok": True, "kind": kind, "summary": summary, "questions": list(questions),
            "plan": [], "warnings": [], "code": "", **extra}


def unsupported_skills(text):
    """Public task entry points, not a second motion or program compiler."""
    registry = (
        ('drawing', r'绘制|画(?:个|一个|一颗|一条)?(?:爱心|心形|圆|正方形|长方形|直线)', '有趣 → 绘制', 'hand-follow', 'drawing'),
        ('writing', r'写字|书写|写(?:文字|汉字|英文)', '有趣 → 写字', 'hand-follow', 'writing'),
        ('sorting', r'分拣|装盒|拼摆', '分拣', 'sorting', ''),
        ('chess', r'井字棋|下棋', '有趣 → 井字棋', 'hand-follow', 'chess'),
        ('following', r'手掌跟随|鼠标跟随', '有趣 → 跟随', 'hand-follow', 'interaction'),
    )
    return [{'skill':skill,'entry':entry,'view':view,'tab':tab}
            for skill,pattern,entry,view,tab in registry if re.search(pattern,text)]


def drawing_request(text):
    """Whole-message grammar for the existing finite drawing task, not code."""
    pattern=(r'\s*(?:请|帮我)?\s*'
             r'(?:(?:先)?\s*(?:以|到|去|移动到|运动到|前往)?\s*(P\d+)\s*'
             r'(?:到起点|作为起点|为起点|当作起点|起点)?\s*[,，;；。]?\s*(?:然后|接着|再)\s*)?'
             r'(?:绘制|画)(?:一个|个|一颗)?\s*(爱心|心形|圆圈|圆形|正方形|长方形)\s*[。！!]?\s*')
    match=re.fullmatch(pattern,text)
    if not match:return None
    shape={'爱心':'heart','心形':'heart','圆圈':'circle','圆形':'circle','正方形':'square','长方形':'rectangle'}[match[2]]
    ops=([{'op':'movej','point':match[1]}] if match[1] else [])+[{'op':'drawing','shape':shape}]
    return result('skill_plan','已整理完整绘制任务；点击执行后机器人开始。',plan=ops,
                  skill_plan={'kind':'drawing','shape':shape})

def merge_clarification(original, supplement):
    """Only explicit slot replacements or a full current restatement compose.

    Unstructured text is never appended to an earlier motion request. Questions,
    negations, history and bare confirmation cannot become implicit permission.
    """
    if not isinstance(supplement,str) or not 1<=len(supplement.strip())<=2000:
        raise ValueError('请填写本轮补充，最多2000字')
    supplement=supplement.strip()
    full=re.fullmatch(r'完整要求[：:]\s*(.+)',supplement,re.S)
    if full:return full[1]
    slots=[(r'(?:等待多久|等待待定|等待未指定)',r'(?:等待|等)\s*\d+(?:\.\d+)?\s*(?:秒|s)'),
           (r'(?:速度多少|速度待定|速度未指定)',r'速度\s*\d+(?:\.\d+)?\s*[%％]'),
           (r'(?:移动到哪里|移动到哪个点|目标点待定)',r'(?:去|移动到|目标点)\s*P\d+')]
    for missing,answer in slots:
        if len(re.findall(missing,original))==1 and re.fullmatch(answer,supplement,re.I):
            return re.sub(missing,lambda _:supplement,original,count=1)
    # Preserve the original unresolved request; caller asks for a new explicit
    # full request instead of silently dropping its unhandled requirements.
    return original




def tool_problem(resources, action=None, tool_id=None):
    resources = resources or {}
    tool, mounted = resources.get('tool') or {}, resources.get('mounted') or {}
    if not tool.get('id') or tool.get('type') == 'none' or tool.get('id') != mounted.get('tool_id'):
        return '当前没有明确的已安装工具，请先确认工具。'
    if tool_id is not None and tool_id != tool['id']:
        return '所需工具与当前工具不同，请先确认正确工具。'
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', tool['id']):
        return '工具标识不能安全编译为项目 DSL。'
    if resources.get('mode') == 'real' and mounted.get('state') not in ('manual_confirmed', 'detected_verified', 'confirmed'):
        return '尚未确认实际安装工具。'
    if action is not None and action not in tool.get('actions', {}):
        return f'当前工具不支持语义动作 {action}。'
    if action is not None and resources.get('mode') == 'real' and action not in resources.get('mapped_actions', []):
        return f'工具动作 {action} 缺少有效的真实 IO 映射，请先完成标定；不会猜测 DOUT。'
    return ''


def intent(text, points, resources=None):
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        return result("clarify", "请输入不超过 2000 字的本次要求。")
    text = text.strip()
    if re.fullmatch(r"[你好您好嗨哈喽早上下午晚上好！!，,。\s]+|hi[!.\s]*|hello[!.\s]*", text, re.I):
        return result("chat", "你好！可以聊天、解释程序，或帮你准备待人工确认的机器人动作。")
    # Questions, negations, hypotheticals and code discussion are never orders.
    if re.search(r"[?？]|解释|为什么|如何|怎么|介绍|含义|区别|示例|举例|写.{0,8}(代码|程序)|不要|别动|禁止|不能|假如|如果|是否|能否|可以吗", text, re.I):
        return None  # Conversational model, forced chat-only by caller.
    drawing=drawing_request(text)
    if drawing:return drawing
    unavailable=unsupported_skills(text)
    if unavailable:
        start=re.search(r'(P\d+)\s*(?:作为|当作|设为|到)?\s*起点',text)
        recognized={'start_point':start[1]} if start else {}
        entries='、'.join(row['entry'] for row in unavailable)
        prefix=f'已理解以{start[1]}为起点；' if start else ''
        return result('clarify',prefix+'AI暂未接入这些任务组合，未生成部分动作。请在'+entries+'执行。',
                      unhandled_skills=unavailable,recognized=recognized,reason_code='unsupported_skill')
    if re.search(r"回起点|回原点|安全高度|吹气|抓住", text):
        return result("clarify", "还不能确定抓放工艺；不会把 DO1 当作吸取。请补充本次动作的现场定义。", [
            "取物点和放物点分别是哪一个已保存点位？",
            "工具已确认的吸取/释放 IO 通道、电平及到位反馈是什么？",
            "安全高度、接近/撤离点位和返回起点分别是什么？",
            "确认这些信息后，请给出明确的点位与 IO 动作顺序。"])
    # Only named, explicit primitive requests can become plans. Consume every
    # meaningful token so an unsupported suffix cannot silently disappear.
    work = list(text)
    used = [False] * len(text)
    ops = []
    semantic = []

    def take(pattern, build, flags=re.I):
        for m in re.finditer(pattern, text, flags):
            if not any(used[m.start():m.end()]):
                ops.append((m.start(), build(m)))
                work[m.start():m.end()] = " " * (m.end() - m.start())
                used[m.start():m.end()] = [True] * (m.end() - m.start())

    num = r"\d+(?:\.\d+)?"
    take(rf"以\s*({num})\s*[%％]\s*(?:的)?速度(?=[。！!\s]*$)", lambda m: {"op": "speed", "pct": float(m[1])})
    take(rf"(?:速度|speed)\s*(?:调到|设为|设置为|设置|到|为|=)?\s*({num})\s*[%％]", lambda m: {"op": "speed", "pct": float(m[1])})
    take(rf"(?:等待|等|wait)\s*({num})\s*(?:秒|s)(?![a-z])", lambda m: {"op": "wait", "sec": float(m[1])})
    take(r"(?:把\s*)?DO([1-3])\s*(?:设为|置为|置|=|输出)?\s*(打开|开启|关闭|断掉|关掉|断开|接通|通电|断电|开|关|[01])(?!\d)",
         lambda m: {"op": "dout", "bit": int(m[1])-1, "val": int(m[2] in ("打开", "开启", "接通", "通电", "开", "1"))})
    take(r"(?:回到|返回|回|移动到|运动到)\s*零位", lambda m: {"op": "movej", "joints": [0]*6})
    take(r"(?:关节角|joints)\s*[：:=]?\s*\[([^\]\n]+)\]", lambda m: {"op": "movej", "joints": _joints(m[1])})
    def action(m):
        value = m[1].lower() if m.lastindex else {'吸附': 'vacuum_on', '吸取': 'vacuum_on', '释放': 'vacuum_off', '夹紧': 'close', '夹取': 'close', '张开': 'open'}[m[0]]
        if m[0] == '释放' and 'vacuum_off' not in ((resources or {}).get('tool') or {}).get('actions', {}):
            value = 'open'
        semantic.append((m.start(), m.end(), value))
        return {'op': 'tool', 'action': value}
    take(r'TOOL\s+(' + '|'.join(re.escape(action.upper()) for action in sorted(TOOL_ACTIONS)) + r')\b', action)
    take(r'吸附|吸取|释放|夹紧|夹取|张开', action)
    take(r'REQUIRE_TOOL\s+([A-Za-z0-9_.:-]+)', lambda m: {'op': 'require_tool', 'tool_id': m[1]})
    # Pn refers to the project's stable code, not a display name or list index.
    labels = {str(p["name"]): p for p in points}
    labels.update({str(p.get("code")): p for p in points if p.get("code")})
    pattern = r"(?<![a-zA-Z0-9_])P\d+(?![a-zA-Z0-9_])"
    names = sorted((n for n in labels if not re.fullmatch(r"P\d+", n)), key=len, reverse=True)
    if names:
        pattern += "|" + "|".join(re.escape(n) for n in names)
    missing = []
    def point(m):
        name = m[0]
        if name not in labels:
            missing.append(name)
        return {"op": "movej", "point": name}
    take(pattern, point, flags=0)
    remaining = "".join(work)
    remaining = re.sub(r"按顺序|的顺序|走一遍|依次|顺序|然后|接着|最后|先|再|并且|以及|并|和|请|帮我|把|将|按|以|用|接近点|撤离点|点位|关节移动到|移动到|运动到|前往|返回|回到|到|去|在|经|速度|调节|设置|全局|末端|输出|工具|物体|工件|\s|[，,。.;；、→%％]", "", remaining)
    if missing:
        return result("clarify", "找不到点位：" + "、".join(missing), ["请保存这些点位，或提供正确的点位编号。"])
    if any(op['op'] == 'movej' for _, op in ops) and not re.search(r'移动|运动|前往|到|去|回|走|在|经|先|movej|关节角|joints', text, re.I):
        return result('clarify', '提到了点位，但尚未明确要求运动；没有生成计划。', ['是要解释点位，还是按什么顺序运动？'])
    if remaining or not ops:
        if ops or re.search(r"运动|移动|执行|点位|DO\d|速度|等待|零位|循环", text, re.I):
            return result("clarify", "本次要求还不能完整转换为白名单动作；未生成部分计划。", [
                "请明确点位顺序、速度百分比、等待秒数或 DO1～DO3 的 0/1 电平；不继承上次计划。"])
        return None
    for _, op in ops:
        if op['op'] in ('tool', 'require_tool'):
            why = tool_problem(resources, op.get('action'), op.get('tool_id'))
            if why:
                return result('clarify', why)
    # Process motions require explicitly named approach/contact and withdrawal
    # segments. There is no calibrated safe-height resource to invent them from.
    if semantic and not any(op['op'] == 'movej' for _, op in ops) and not re.search(r'工具|\bTOOL\b', text, re.I):
        return result('clarify', '缺少取放点位、接近和撤离路径；若只切换工具状态，请明确说“工具吸附”等。')
    if semantic and any(op['op'] == 'movej' for _, op in ops):
        if resources.get('mode') == 'real' and (resources['tool'].get('tcp') or {}).get('status') != 'verified':
            return result('clarify', '实际工具 TCP 尚未验证，不能将工具取放路径当作已知几何；请先完成标定。')
        previous = 0
        for begin, end, _ in sorted(semantic):
            segment = text[previous:begin]
            if '接近点' not in segment or len([op for pos, op in ops if previous <= pos < begin and op['op'] == 'movej']) < 2:
                return result('clarify', '已识别工具动作，但缺少每次动作前的接近点和接触点；未生成部分计划。',
                              ['例如：经接近点P2到P1吸附，经撤离点P2和接近点P4到P3释放，经撤离点P4返回P1。'])
            if previous and '撤离点' not in segment:
                return result('clarify', '缺少前一次工具动作后的撤离点，请明确撤离路径。')
            previous = end
        if '撤离点' not in text[previous:]:
            return result('clarify', '缺少最后工具动作后的撤离点，请明确返回路径。')
    # Only explicit trailing global modifiers may precede earlier moves.
    ordered = [op for _, op in sorted(ops)]
    if sum(op['op'] == 'speed' for op in ordered) == 1 and re.search(rf'以\s*{num}\s*[%％]\s*(?:的)?速度[。！!\s]*$', text):
        ordered = [op for op in ordered if op['op'] == 'speed'] + [op for op in ordered if op['op'] != 'speed']
    if semantic and not any(op['op'] == 'require_tool' for op in ordered):
        ordered.insert(0, {'op': 'require_tool', 'tool_id': resources['tool']['id']})
    return result("plan", "已按本条要求整理动作；尚未执行。", plan=ordered)


def _joints(text):
    try:
        return [float(v.strip()) for v in text.replace("，", ",").split(",")]
    except ValueError:
        return []  # validate supplies a clear finite-six-joints error.


def compile_plan(plan):
    """Legal project DSL only; numeric joints freeze the resolved point identity.

    Never interpolate a model string, point label, Python expression or comment.
    MOVEJ numeric form is accepted by core.program.parse without executing Python.
    """
    def n(v):
        # Fixed notation: the legacy DSL does not accept scientific notation.
        from decimal import Decimal
        s = format(Decimal(str(v)), 'f')
        return (s.rstrip('0').rstrip('.') if '.' in s else s) if v else '0'
    lines = []
    for op in plan:
        kind = op['op']
        if kind == 'speed': lines.append('SPEED ' + n(op['pct']))
        elif kind == 'movej': lines.append('MOVEJ ' + ','.join(n(v) for v in op['joints']))
        elif kind == 'dout': lines.append(f"DOUT {op['bit']},{op['val']}")
        elif kind == 'wait': lines.append('WAIT ' + n(op['sec']))
        elif kind == 'tool': lines.append('TOOL ' + op['action'].upper())
        elif kind == 'require_tool': lines.append('REQUIRE_TOOL ' + op['tool_id'])
        else: raise ValueError('非白名单动作不能生成代码')
    return '\n'.join(lines)
