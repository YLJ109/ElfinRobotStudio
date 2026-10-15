"""程序执行：本项目自定义的脚本语言。

⚠️ 这不是华沿示教器的脚本格式 —— 厂商格式没有拿到样本，不猜。
   这里定义的语法在「关于」页里逐条列明，解析失败会指出行号和原因。

语法：
    # 注释
    SPEED 12                全局速度百分比（1~100）
    MOVEJ P3                关节运动到已保存点位
    MOVEJ 10,20,30,40,50,60 关节运动到给定角度
    MOVEL x,y,z,a,b,c       笛卡尔直线（仿真执行；真机需单独资格）
    DOUT 1,1                末端 DO1 置 1（0~2）
    WAIT 0.5                等待秒数（≤60）
    LOOP 3 ... END          循环，展开上限 200 次
    PAUSE                   等人工点"继续"
    TOOL OPEN|CLOSE|VACUUM_ON|VACUUM_OFF  末端工具动作
    REQUIRE_TOOL <id>       要求当前确认的工具档案
"""

from __future__ import annotations

import re

MAX_LOOP_EXPANSION = 200
SPEED_RE = re.compile(r"^SPEED\s+(\d+(?:\.\d+)?)$")
MOVEJ_RE = re.compile(r"^MOVEJ\s+(\S+)$")
MOVEL_RE = re.compile(r"^MOVEL\s+(\S+)$")
DOUT_RE = re.compile(r"^DOUT\s+(\d+)\s*,\s*(\d+)$")
WAIT_RE = re.compile(r"^WAIT\s+(\d+(?:\.\d+)?)$")
LOOP_RE = re.compile(r"^LOOP\s+(\d+)$")
TOOL_RE = re.compile(r"^TOOL\s+(OPEN|CLOSE|VACUUM_ON|VACUUM_OFF|ATTACH|DETACH)$")
REQUIRE_TOOL_RE = re.compile(r"^REQUIRE_TOOL\s+(\S+)$")


class ProgramError(Exception):
    def __init__(self, line_no, text, why):
        super().__init__(f"第 {line_no} 行 `{text}`：{why}")
        self.line_no, self.text, self.why = line_no, text, why


COMMAND_ALIASES = {
    "速度": "SPEED", "关节移动": "MOVEJ", "关节运动": "MOVEJ", "直线移动": "MOVEL", "直线运动": "MOVEL", "笛卡尔直线": "MOVEL",
    "输出": "DOUT", "末端输出": "DOUT", "等待": "WAIT", "等待输入": "WAIT_DI", "如果输入": "IF_DI",
    "循环": "LOOP", "结束": "END", "暂停": "PAUSE", "标签": "LABEL", "需要工具": "REQUIRE_TOOL", "工具": "TOOL",
}
ACTION_ALIASES = {"打开": "OPEN", "关闭": "CLOSE", "吸附": "VACUUM_ON", "释放": "VACUUM_OFF", "连接": "ATTACH", "脱离": "DETACH"}


def normalize_command(raw):
    """只翻译首个命令和 TOOL 动作，参数、点位名和中文注释原样保留。"""
    m = re.match(r"^(\s*)(\S+)(.*)$", raw)
    if not m:
        return raw
    command = COMMAND_ALIASES.get(m.group(2), m.group(2).upper())
    rest = m.group(3)
    if command == "TOOL":
        am = re.match(r"(\s+)(\S+)(.*)$", rest)
        if am:
            rest = am.group(1) + ACTION_ALIASES.get(am.group(2), am.group(2).upper()) + am.group(3)
    return m.group(1) + command + rest


def parse(src, point_lookup, params=None, point_items=None):
    """展开成线性步骤列表；LOOP 在解析期展开并设上限。"""
    if not isinstance(src, str) or len(src) > 200000:
        raise ProgramError(0, '程序', '代码必须是文本，最多 200000 字符')
    if params is not None and (not isinstance(params, dict) or len(params) > 100 or any(not isinstance(k,str) or not k.isidentifier() or k.startswith('_') for k in params)):
        raise ProgramError(0, '参数', '参数需为最多 100 个具名变量的字典')
    params = params or {}
    meaningful = [line.strip() for line in src.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    legacy_commands = {'SPEED','MOVEJ','MOVEL','WAIT','DOUT','WAIT_DI','IF_DI','LOOP','END','PAUSE','LABEL','REQUIRE_TOOL','TOOL'}
    legacy = all(normalize_command(line).split()[0] in legacy_commands and not any(c in line.split('#',1)[0] for c in '()=') for line in meaningful)
    if meaningful and not legacy:
        from .python_program import validate
        import ast
        tree = validate(src)
        points = list(point_items or [])
        for node in ast.walk(tree):
            ref = node.id if isinstance(node, ast.Name) else node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None
            p = point_lookup(ref) if ref else None
            if p and not any(v['id'] == p['id'] for v in points): points.append(p)
        names = set(params)
        for p in points: names.update([p['name'], p.get('code') or p['name']])
        validate(src, known_names=names)
        import copy
        return [{'kind': 'python', 'source': src, 'params': params, 'points': copy.deepcopy(points)}]
    def replace(match):
        name = match.group(1)
        if name not in params:
            raise ProgramError(0, name, "缺少任务参数")
        value = str(params[name])
        if not re.fullmatch(r"[\w.+-]+", value):
            raise ProgramError(0, name, "参数仅允许名称或数字，不能注入指令")
        return value
    src = re.sub(r"\$\{(\w+)\}", replace, src)
    lines = [ln.split('#', 1)[0].strip() for ln in src.splitlines()]
    steps = []
    stack = []            # [(count, body_lines)]
    for i, raw in enumerate(lines, start=1):
        if not raw or raw.startswith("#"):
            continue
        raw = normalize_command(raw)
        if raw == "END":
            if not stack:
                raise ProgramError(i, raw, "END 没有对应的 LOOP")
            count, body = stack.pop()
            if stack:
                raise ProgramError(i, raw, "不支持 LOOP 嵌套（先写平）")
            steps.extend(body * count)
            continue
        if stack:
            stack[-1][1].append((i, raw))
            continue
        m = LOOP_RE.match(raw)
        if m:
            n = int(m.group(1))
            if not 1 <= n <= MAX_LOOP_EXPANSION:
                raise ProgramError(i, raw, f"循环次数要在 1~{MAX_LOOP_EXPANSION}")
            stack.append([n, []])
            continue
        steps.append((i, raw))
    if stack:
        raise ProgramError(stack[-1][1][0][0] if stack[-1][1] else 0, "LOOP", "缺少 END")
    return compile_steps(steps, point_lookup)


def compile_steps(steps, point_lookup):
    out = []
    for line_no, original in steps:
        raw = normalize_command(original)
        if SPEED_RE.match(raw):
            v = float(SPEED_RE.match(raw).group(1))
            if not 1 <= v <= 100:
                raise ProgramError(line_no, raw, "速度要在 1~100")
            out.append({"kind": "speed", "pct": v, "line": line_no, "src": raw})
            continue
        if raw == "PAUSE":
            out.append({"kind": "pause", "line": line_no, "src": raw})
            continue
        m = TOOL_RE.match(raw)
        if m:
            out.append({"kind": "tool_action", "action": m.group(1).lower(), "line": line_no, "src": raw})
            continue
        m = REQUIRE_TOOL_RE.match(raw)
        if m:
            out.append({"kind": "require_tool", "tool_id": m.group(1), "line": line_no, "src": raw})
            continue
        m = re.fullmatch(r"WAIT_DI\s+(\S+)\s+([01])\s+TIMEOUT\s+(\d+(?:\.\d+)?)(?:\s+DEBOUNCE\s+(\d+(?:\.\d+)?))?", raw)
        if m:
            timeout, debounce = float(m[3]), float(m[4] or .1)
            if not 0 < timeout <= 60 or not 0 <= debounce <= timeout:
                raise ProgramError(line_no, raw, "输入超时需0~60秒且大于消抖时间")
            out.append({"kind": "wait_di", "alias": m[1], "val": int(m[2]), "timeout": timeout,
                        "debounce": debounce, "line": line_no, "src": raw})
            continue
        m = re.fullmatch(r"LABEL\s+(\w+)", raw)
        if m:
            out.append({"kind": "label", "label": m[1], "line": line_no, "src": raw})
            continue
        m = re.fullmatch(r"IF_DI\s+(\S+)\s+([01])\s+GOTO\s+(\w+)", raw)
        if m:
            out.append({"kind": "if_di", "alias": m[1], "val": int(m[2]), "label": m[3], "line": line_no, "src": raw})
            continue
        m = WAIT_RE.match(raw)
        if m:
            v = float(m.group(1))
            if not 0 <= v <= 60:
                raise ProgramError(line_no, raw, "WAIT 秒数要在 0~60")
            out.append({"kind": "wait", "sec": v, "line": line_no, "src": raw})
            continue
        m = DOUT_RE.match(raw)
        if m:
            bit, val = int(m.group(1)), int(m.group(2))
            if bit not in (0, 1, 2) or val not in (0, 1):
                raise ProgramError(line_no, raw, "DOUT 的位号只能 0~2、值只能 0/1（协议 p51）")
            out.append({"kind": "dout", "bit": bit, "val": val, "line": line_no, "src": raw})
            continue
        m = re.fullmatch(r"DOUT\s+([^,\s]+)\s*,\s*([01])", raw)
        if m:
            out.append({"kind": "dout", "alias": m[1], "val": int(m[2]), "line": line_no, "src": raw})
            continue
        m = MOVEJ_RE.match(raw)
        if m:
            arg = m.group(1)
            if "," in arg:
                try:
                    j = [float(x) for x in arg.split(",")]
                except ValueError:
                    raise ProgramError(line_no, raw, "六个角度必须是数字")
                if len(j) != 6:
                    raise ProgramError(line_no, raw, f"给了 {len(j)} 个角度，需要 6 个")
                out.append({"kind": "movej", "joints": j, "point": None,
                            "line": line_no, "src": raw})
            else:
                p = point_lookup(arg)
                if not p:
                    raise ProgramError(line_no, raw, f"找不到点位 {arg}（按名称或 id 都没匹配上）")
                out.append({"kind": "movej", "joints": p["joints"], "point": p["name"],
                            "line": line_no, "src": raw})
            continue
        m = MOVEL_RE.match(raw)
        if m:
            point = point_lookup(m.group(1))
            if point:
                tcp = point.get('tcp') or {}
                pose = [*tcp.get('xyz_mm', []), *tcp.get('rpy_deg', [])]
                if len(pose) != 6:
                    raise ProgramError(line_no, raw, '点位没有完整的 TCP 数据')
                out.append({'kind': 'movel', 'pose': pose, 'point': point['name'], 'line': line_no, 'src': raw})
                continue
            try:
                pose = [float(x) for x in m.group(1).split(",")]
            except ValueError:
                raise ProgramError(line_no, raw, "MOVEL 的 X,Y,Z,A,B,C 必须是数字")
            if len(pose) != 6:
                raise ProgramError(line_no, raw, f"给了 {len(pose)} 个笛卡尔值，需要 6 个")
            out.append({"kind": "movel", "pose": pose, "line": line_no, "src": raw})
            continue
        raise ProgramError(line_no, raw, "不认识这条指令")
    labels = {step["label"]: i for i, step in enumerate(out) if step["kind"] == "label"}
    for step in out:
        if step["kind"] == "if_di":
            if step["label"] not in labels:
                raise ProgramError(step["line"], step["src"], "跳转标签不存在")
            step["jump"] = labels[step["label"]]
    return out


class Runner:
    """由 world.tick 驱动的步进器；不自己起线程，避免和 WS 循环抢状态。"""

    def __init__(self):
        self.steps = []
        self.i = -1
        self.state = "idle"        # idle|running|waiting|paused|finished|aborted|error
        self.wait_until = 0.0
        self.busy = False          # 由外部（机器人）置位：运动没完就别推进
        self.error = ""
        self.completed_index = 0
        self.active_index = None
        self.script_spec = None
        self.script = None
        self.input_reader = None

    def load(self, steps):
        self.script_spec = steps[0] if len(steps) == 1 and steps[0].get('kind') == 'python' else None
        self.script = None
        self.steps = steps
        self.i = -1
        self.state = "idle"
        self.error = ""
        self.completed_index = 0
        self.active_index = None

    def start(self):
        if self.state in ("running", "waiting"):
            return False
        if not self.steps and not self.script_spec:
            return False
        if self.script_spec:
            from .python_program import Interpreter
            self.script = Interpreter(self.script_spec, self.input_reader).run()
            self.steps = []
        self.i = 0
        self.completed_index = 0
        self.active_index = None
        self.state = "running"
        return True

    def stop(self, why="手动中止"):
        self.state = "aborted"
        self.error = why

    def resume(self):
        if self.state == "paused":
            self.state = "running"

    def current(self):
        index = self.active_index if self.active_index is not None else self.i
        return self.steps[index] if 0 <= index < len(self.steps) else None

    def complete(self):
        if self.active_index is not None:
            self.completed_index = self.active_index + 1
            self.active_index = None

    def advance(self, now):
        """推进一步；返回本步该执行的动作（dict）或 None。"""
        if self.state not in ("running", "waiting"):
            return None
        if self.busy:
            return None
        if self.state == "waiting":
            if now < self.wait_until:
                return None
            self.state = "running"
            self.complete()
        if self.script and self.i >= len(self.steps):
            try:
                step = next(self.script)
                if step['kind'] == 'script_yield': return None
                self.steps.append(step)
            except StopIteration:
                self.script = None
            except Exception as e:
                self.stop(str(e))
                self.script = None
                return None
        if self.i >= len(self.steps):
            self.state = "finished"
            return None
        step = self.steps[self.i]
        self.active_index = self.i
        self.i += 1
        if step["kind"] == "wait":
            self.state = "waiting"
            self.wait_until = now + step["sec"]
            return None
        if step["kind"] == "pause":
            self.state = "paused"
            self.complete()
            return None
        return step

    def progress(self):
        if self.script_spec:
            return 1.0 if self.state == 'finished' else 0.0
        n = len(self.steps) or 1
        return round(min(1.0, max(0.0, self.completed_index / n)), 3)

    def snapshot(self):
        return {"state": self.state, "index": self.i, "total": len(self.steps),
                'dynamic': bool(self.script_spec),
                "completed_index": self.completed_index, "active_index": self.active_index,
                "progress": self.progress(), "current": self.current(), "error": self.error}
