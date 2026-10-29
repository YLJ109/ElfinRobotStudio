"""Bounded, cooperative AST interpreter. No eval/exec, imports or host object access."""
from __future__ import annotations
import ast
import io
import math
import operator
import tokenize
from dataclasses import dataclass

KEYWORDS = {'如果': 'if', '否则如果': 'elif', '否则': 'else', '循环': 'for', '当': 'while',
            '定义': 'def', '返回': 'return', '跳出': 'break', '继续': 'continue', '在': 'in',
            '真': 'True', '假': 'False', '空': 'None', '且': 'and', '或': 'or', '非': 'not', '略过': 'pass'}
BUILTINS = {'范围': 'range', '长度': 'len', '绝对值': 'abs', '最小值': 'min', '最大值': 'max',
            '求和': 'sum', '取整': 'round', '整数': 'int', '小数': 'float', '字符串': 'str',
            '列表': 'list', '字典': 'dict', '枚举': 'enumerate', '配对': 'zip', '读取输入': 'DI'}
COMMANDS = {'SPEED', 'MOVEJ', 'MOVEL', 'WAIT', 'DOUT', 'WAIT_DI', 'TOOL', 'REQUIRE_TOOL', 'PAUSE'}
METHODS = {'append', 'pop', 'get', 'items', 'keys', 'values', 'update', '添加', '弹出', '获取', '键', '值', '条目', '更新'}
MAX_DATA_COST = 200000
MAX_DATA_DEPTH = 32
MAX_NATIVE_WORK = 1000000
METHOD_ALIASES = dict(zip(('添加', '弹出', '获取', '键', '值', '条目', '更新'), ('append', 'pop', 'get', 'keys', 'values', 'items', 'update')))
ALLOWED = (ast.Module, ast.Expr, ast.Assign, ast.AugAssign, ast.If, ast.For, ast.While, ast.FunctionDef,
           ast.Return, ast.Break, ast.Continue, ast.Pass, ast.Name, ast.Constant, ast.List, ast.Tuple, ast.Dict,
           ast.Subscript, ast.Slice, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Call, ast.Attribute,
           ast.arguments, ast.arg, ast.keyword, ast.Load, ast.Store, ast.Add, ast.Sub, ast.Mult, ast.Div,
           ast.FloorDiv, ast.Mod, ast.UAdd, ast.USub, ast.Not, ast.And, ast.Or, ast.Eq, ast.NotEq,
           ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn)

def normalized(source):
    tokens = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME and token.string in KEYWORDS:
            token = tokenize.TokenInfo(token.type, KEYWORDS[token.string], token.start, token.end, token.line)
        tokens.append(token)
    return tokenize.untokenize(tokens)

def validate(source, known_names=None):
    from .program import ProgramError
    try:
        tree = ast.parse(normalized(source))
        nodes = list(ast.walk(tree))
        if len(nodes) > 10000:
            raise ValueError('程序过大，最多 10000 个语法节点')
        for node in nodes:
            if not isinstance(node, ALLOWED):
                raise ValueError(f'不支持 {type(node).__name__} 语法')
            if isinstance(node, (ast.Name, ast.arg)) and (getattr(node, 'id', getattr(node, 'arg', '')).startswith('_')):
                raise ValueError('名称不能以下划线开头')
            if isinstance(node, ast.Attribute) and node.attr not in METHODS:
                raise ValueError('只支持文档列出的列表、字典方法')
            if isinstance(node, ast.Call) and not isinstance(node.func, (ast.Name, ast.Attribute)):
                raise ValueError('只能调用具名函数或允许的容器方法')
            if isinstance(node, ast.FunctionDef) and (node.decorator_list or node.args.vararg or node.args.kwarg or node.args.kwonlyargs or node.args.posonlyargs or node.args.defaults):
                raise ValueError('函数使用固定参数，不支持装饰器、默认参数或可变参数')
        from .program import COMMAND_ALIASES
        known = {n.name for n in nodes if isinstance(n, ast.FunctionDef)} | set(COMMAND_ALIASES) | COMMANDS | set(BUILTINS) | set(BUILTINS.values()) | {'DI','range','len','abs','min','max','sum','round','int','float','str','list','dict','enumerate','zip'}
        for node in nodes:
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id not in known and node.func.id.upper() not in COMMANDS:
                raise ValueError(f'不支持的函数：{node.func.id}')
        if known_names is not None:
            from .program import ACTION_ALIASES
            variables = set(known_names) | known | set(ACTION_ALIASES) | set(ACTION_ALIASES.values())
            variables |= {n.id for n in nodes if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
            variables |= {n.arg for n in nodes if isinstance(n, ast.arg)}
            for node in nodes:
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in variables and node.id.upper() not in COMMANDS:
                    raise ValueError(f'未定义变量或点位：{node.id}')
        # Compile validates break/return placement and assignment targets, without executing code.
        compile(tree, '<robot-program>', 'exec')
        return tree
    except (SyntaxError, ValueError, tokenize.TokenError, IndentationError, RecursionError) as e:
        line = getattr(e, 'lineno', None) or (getattr(locals().get('node'), 'lineno', 1))
        raise ProgramError(line, source.splitlines()[line - 1] if 0 < line <= len(source.splitlines()) else '', str(e)) from e

@dataclass
class Function:
    node: ast.FunctionDef
    scope: dict

class ReturnValue(Exception):
    def __init__(self, value): self.value = value
class BreakLoop(Exception): pass
class ContinueLoop(Exception): pass

class Interpreter:
    def __init__(self, spec, input_reader=None):
        from .program import COMMAND_ALIASES
        self.tree = validate(spec['source'])
        self.lines = spec['source'].splitlines()
        self.points = spec.get('points', [])
        self.input_reader = input_reader
        self.scope = {k: self.parameter(v) for k, v in spec.get('params', {}).items()}
        self.aliases = COMMAND_ALIASES
        self.operations = 0
        self.last_yield = 0
        self.depth = 0
        self.debug = False
        self.debug_scope = self.scope

    def debug_variables(self):
        """Bounded data-only display; never repr/evaluate user or host objects."""
        budget = [400]
        def preview(value, depth=0):
            budget[0] -= 1
            if budget[0] < 0 or depth > 4:
                return '…'
            if type(value) in (type(None), bool, int, float):
                return value
            if type(value) is str:
                return value[:240] + ('…' if len(value) > 240 else '')
            if type(value) in (list, tuple):
                return [preview(v, depth+1) for v in value[:20]] + (['…'] if len(value)>20 else [])
            if type(value) is dict:
                from itertools import islice
                return {'entries': [[preview(k, depth+1), preview(v, depth+1)]
                                    for k,v in islice(value.items(), 20)], 'size': len(value)}
            return '<' + type(value).__name__ + '>'
        from itertools import islice
        return [{'name': name, 'value': preview(value),
                 'scope': 'global' if self.debug_scope is self.scope else 'local (含继承名称)'}
                for name,value in islice(self.debug_scope.items(), 100)]

    @staticmethod
    def parameter(value):
        if isinstance(value, str):
            if len(value) > 200000:
                raise ValueError('参数文本过长')
            try: value = ast.literal_eval(value)
            except (ValueError, SyntaxError): pass

        # 参数只接收数据，不允许把宿主对象及其方法带入解释器。
        remaining = [10000]
        def clone(item, depth=0):
            remaining[0] -= 1
            if remaining[0] < 0 or depth > 32:
                raise ValueError('参数嵌套或总大小超限')
            if type(item) in (list, tuple, dict):
                if len(item) > 10000:
                    raise ValueError('参数容器过大')
                if type(item) is dict:
                    return {clone(k, depth + 1): clone(v, depth + 1) for k, v in item.items()}
                values = [clone(v, depth + 1) for v in item]
                return tuple(values) if type(item) is tuple else values
            if type(item) not in (str, int, float, bool, type(None)):
                raise ValueError('参数只能包含文本、有限数值及列表字典')
            if isinstance(item, str) and len(item) > 10000:
                raise ValueError('参数字符串过长')
            if isinstance(item, int) and item.bit_length() > 256:
                raise ValueError('参数整数超出范围')
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError('参数数值必须有限')
            return item
        return clone(value)

    def _data_cost(self, value, replacements=None):
        """计算展开成本；共享子图缓存计算结果，但每次引用仍计入展开预算。"""
        active, memo = set(), {}
        replacements = replacements or {}

        def visit(item, depth):
            if depth > MAX_DATA_DEPTH:
                raise ValueError('数据嵌套不能超过 32 层')
            kind = type(item)
            if kind is str:
                if len(item) > 10000: raise ValueError('字符串长度不能超过 10000')
                return len(item) + 1, 0
            if kind in (int, bool):
                if item.bit_length() > 256: raise ValueError('整数超出范围')
                return 2 + (item.bit_length() + 2) // 3, 0
            if kind is float:
                if not math.isfinite(item): raise ValueError('数值必须有限')
                return 32, 0
            if item is None: return 5, 0
            if kind is range:
                for part in (item.start, item.stop, item.step): visit(part, depth)
                if len(item) > 10000: raise ValueError('容器长度不能超过 10000')
                return 1 + len(item) * (2 + max(abs(item.start), abs(item.stop)).bit_length() // 3), 0
            if kind not in (list, tuple, dict, slice):
                raise ValueError('原生操作只允许受支持的数据类型')
            identity = id(item)
            if identity in active: raise ValueError('不支持循环引用数据')
            if identity in memo:
                cost, height = memo[identity]
                if depth + height > MAX_DATA_DEPTH: raise ValueError('数据嵌套不能超过 32 层')
                return cost, height
            effective = replacements.get(identity, item)
            if kind is not slice and len(effective) > 10000:
                raise ValueError('容器长度不能超过 10000')
            active.add(identity)
            cost, height = 1, 0
            children = ((part for pair in effective.items() for part in pair) if kind is dict else
                        (effective.start, effective.stop, effective.step) if kind is slice else effective)
            for child in children:
                child_cost, child_height = visit(child, depth + 1)
                cost += child_cost
                height = max(height, child_height + 1)
                if cost > MAX_DATA_COST: raise ValueError('数据展开预算超限')
            active.remove(identity)
            memo[identity] = cost, height
            return cost, height

        cost, _ = visit(value, 0)
        if cost > MAX_DATA_COST: raise ValueError('数据展开预算超限')
        return cost

    def guard(self, value):
        self._data_cost(value)
        return value

    def _guard_inputs(self, args, kwargs):
        if sum(self._data_cost(value) for value in (*args, *kwargs.values())) > MAX_DATA_COST:
            raise ValueError('调用参数展开预算超限')

    def _guard_mutation(self, obj, candidate):
        # 用替换图预检，避免浅复制掩盖 a.append(a) 等提交后才形成的环。
        self._data_cost(obj, {id(obj): candidate})

    def budget(self):
        self.operations += 1
        if self.operations > 100000:
            raise ValueError('程序超过 100000 次计算上限，请拆分任务')

    def point(self, ref):
        return next((p for p in self.points if str(ref) in (str(p['id']), p.get('code'), p['name'])), None)

    def run(self):
        yield from self.block(self.tree.body, self.scope)

    def block(self, nodes, scope):
        from .program import ProgramError
        for node in nodes:
            try:
                if self.debug:
                    self.debug_scope = scope
                    yield {'kind': 'debug_checkpoint', 'line': node.lineno}
                self.budget()
                # Batch ordinary statements; motion yields naturally. Pure loops still release the tick.
                if self.operations - self.last_yield >= 64:
                    self.last_yield = self.operations
                    yield {'kind': 'script_yield', 'line': node.lineno, 'src': self.lines[node.lineno - 1]}
                yield from self.statement(node, scope)
            except (ReturnValue, BreakLoop, ContinueLoop, ProgramError):
                raise
            except Exception as e:
                raise ProgramError(node.lineno, self.lines[node.lineno - 1], str(e)) from e

    def assign(self, target, value, scope):
        value = self.guard(value)
        if isinstance(target, ast.Name): scope[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            if len(target.elts) != len(value): raise ValueError('解包数量不匹配')
            for t, v in zip(target.elts, value): self.assign(t, v, scope)
        elif isinstance(target, ast.Subscript):
            container = self.simple(target.value, scope)
            index = self.simple(target.slice, scope)
            self.guard(container)
            self.guard(index)
            if type(container) is dict and index not in container and len(container) >= 10000:
                raise ValueError('字典过大')
            candidate = container.copy()
            candidate[index] = value
            self._guard_mutation(container, candidate)
            if type(container) is list: container[:] = candidate
            else:
                container.clear()
                container.update(candidate)
        else: raise ValueError('无效赋值目标')

    def simple(self, node, scope):
        generator = self.expression(node, scope)
        try: next(generator)
        except StopIteration as result: return result.value
        raise ValueError('赋值目标不能调用运动函数')

    def statement(self, node, scope):
        if isinstance(node, ast.Expr): yield from self.expression(node.value, scope)
        elif isinstance(node, ast.Assign):
            value = yield from self.expression(node.value, scope)
            for target in node.targets: self.assign(target, value, scope)
        elif isinstance(node, ast.AugAssign):
            left = self.simple(node.target, scope)
            right = yield from self.expression(node.value, scope)
            self.assign(node.target, self.binary(node.op, left, right), scope)
        elif isinstance(node, ast.If):
            test = yield from self.expression(node.test, scope)
            yield from self.block(node.body if test else node.orelse, scope)
        elif isinstance(node, (ast.For, ast.While)):
            iterator = iter((yield from self.expression(node.iter, scope))) if isinstance(node, ast.For) else None
            broken = False
            for _ in range(10000):
                self.budget()
                if self.operations - self.last_yield >= 64:
                    self.last_yield = self.operations
                    yield {'kind': 'script_yield', 'line': node.lineno, 'src': self.lines[node.lineno - 1]}
                if iterator is not None:
                    try: item = next(iterator)
                    except StopIteration: break
                    self.assign(node.target, item, scope)
                elif not (yield from self.expression(node.test, scope)): break
                try: yield from self.block(node.body, scope)
                except ContinueLoop: continue
                except BreakLoop: broken = True; break
            else: raise ValueError('单个循环超过 10000 次')
            if not broken: yield from self.block(node.orelse, scope)
        elif isinstance(node, ast.FunctionDef): scope[node.name] = Function(node, scope)
        elif isinstance(node, ast.Return): raise ReturnValue((yield from self.expression(node.value, scope)) if node.value else None)
        elif isinstance(node, ast.Break): raise BreakLoop()
        elif isinstance(node, ast.Continue): raise ContinueLoop()
        elif isinstance(node, ast.Pass): pass

    def binary(self, op, left, right):
        left_cost, right_cost = self._data_cost(left), self._data_cost(right)
        if isinstance(op, ast.Mod) and isinstance(left, str):
            raise ValueError('不支持字符串格式化运算')
        if isinstance(op, ast.Mult):
            sequence, count = (left, right) if isinstance(left, (str, list, tuple)) else (right, left)
            if isinstance(sequence, (str, list, tuple)) and isinstance(count, int) and len(sequence) * max(count, 0) > 10000:
                raise ValueError('重复后的容器过大')
            if isinstance(sequence, (str, list, tuple)) and isinstance(count, int):
                if 1 + (self._data_cost(sequence) - 1) * max(count, 0) > MAX_DATA_COST:
                    raise ValueError('重复后的数据展开预算超限')
        if isinstance(op, ast.Add) and left_cost + right_cost > MAX_DATA_COST:
            raise ValueError('合并后的数据展开预算超限')
        return self.guard({ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
            ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}[type(op)](left, right))

    def expression(self, node, scope):
        self.budget()
        if isinstance(node, ast.Constant): return self.guard(node.value)
        if isinstance(node, ast.Name):
            if node.id in scope: return scope[node.id]
            if self.point(node.id): return node.id
            from .program import ACTION_ALIASES
            if node.id in ACTION_ALIASES: return ACTION_ALIASES[node.id]
            if node.id in ('OPEN', 'CLOSE', 'VACUUM_ON', 'VACUUM_OFF', 'ATTACH', 'DETACH'): return node.id
            raise ValueError(f'未定义变量或点位：{node.id}')
        if isinstance(node, (ast.List, ast.Tuple)):
            values = []
            for n in node.elts: values.append((yield from self.expression(n, scope)))
            return self.guard(tuple(values) if isinstance(node, ast.Tuple) else values)
        if isinstance(node, ast.Dict):
            result = {}
            for k, v in zip(node.keys, node.values):
                if k is None: raise ValueError('不支持字典展开')
                key = yield from self.expression(k, scope)
                result[key] = yield from self.expression(v, scope)
            return self.guard(result)
        if isinstance(node, ast.Subscript):
            value = yield from self.expression(node.value, scope)
            index = yield from self.expression(node.slice, scope)
            self.guard(value)
            self.guard(index)
            return self.guard(value[index])
        if isinstance(node, ast.Slice):
            parts = []
            for n in (node.lower, node.upper, node.step): parts.append((yield from self.expression(n, scope)) if n else None)
            return slice(*parts)
        if isinstance(node, ast.BinOp):
            left = yield from self.expression(node.left, scope)
            right = yield from self.expression(node.right, scope)
            return self.binary(node.op, left, right)
        if isinstance(node, ast.UnaryOp):
            value = yield from self.expression(node.operand, scope)
            return self.guard({ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Not: operator.not_}[type(node.op)](value))
        if isinstance(node, ast.BoolOp):
            for n in node.values:
                value = yield from self.expression(n, scope)
                if isinstance(node.op, ast.And) and not value or isinstance(node.op, ast.Or) and value: return value
            return value
        if isinstance(node, ast.Compare):
            left = yield from self.expression(node.left, scope)
            for op, n in zip(node.ops, node.comparators):
                right = yield from self.expression(n, scope)
                left_cost, right_cost = self._data_cost(left), self._data_cost(right)
                if isinstance(op, (ast.In, ast.NotIn)) and left_cost * right_cost > MAX_NATIVE_WORK:
                    raise ValueError('成员比较工作预算超限')
                fn = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
                      ast.Gt: operator.gt, ast.GtE: operator.ge, ast.In: lambda a,b: a in b, ast.NotIn: lambda a,b: a not in b}[type(op)]
                if not fn(left, right): return False
                left = right
            return True
        if isinstance(node, ast.Call):
            args, kwargs = [], {}
            for arg in node.args: args.append((yield from self.expression(arg, scope)))
            for kw in node.keywords:
                if kw.arg is None: raise ValueError('不支持参数展开')
                kwargs[kw.arg] = yield from self.expression(kw.value, scope)
            self._guard_inputs(args, kwargs)
            if isinstance(node.func, ast.Attribute):
                obj = yield from self.expression(node.func.value, scope)
                self.guard(obj)
                method = METHOD_ALIASES.get(node.func.attr, node.func.attr)
                allowed = ('append', 'pop') if type(obj) is list else ('get', 'pop', 'items', 'keys', 'values', 'update') if type(obj) is dict else ()
                if method not in allowed: raise ValueError('此类型不支持该方法')
                if method == 'append' and len(obj) >= 10000: raise ValueError('列表过大')
                if method == 'append':
                    candidate = obj.copy()
                    candidate.append(*args, **kwargs)
                    self._guard_mutation(obj, candidate)
                    obj[:] = candidate
                    return None
                if method == 'update':
                    candidate = obj.copy()
                    candidate.update(*args, **kwargs)
                    self._guard_mutation(obj, candidate)
                    obj.clear()
                    obj.update(candidate)
                    return None
                value = getattr(obj, method)(*args, **kwargs)
                return self.guard(list(value) if method in ('items', 'keys', 'values') else value)
            name = node.func.id
            if isinstance(scope.get(name), Function):
                fn = scope[name]
                names = [arg.arg for arg in fn.node.args.args]
                if len(args) > len(names) or any(k not in names for k in kwargs): raise ValueError('函数参数不匹配')
                local = fn.scope.copy()
                supplied = dict(zip(names, args))
                if set(supplied) & set(kwargs): raise ValueError('函数参数重复')
                supplied.update(kwargs)
                if set(supplied) != set(names): raise ValueError('函数缺少参数')
                local.update(supplied)
                self.depth += 1
                if self.depth > 16: raise ValueError('函数调用深度不能超过 16')
                try:
                    yield from self.block(fn.node.body, local)
                except ReturnValue as result: return result.value
                finally: self.depth -= 1
                return None
            canonical = self.aliases.get(name, name.upper())
            if canonical in COMMANDS:
                if self.debug:
                    self.debug_scope = scope
                yield self.command(canonical, args, kwargs, node.lineno)
                return None
            name = BUILTINS.get(name, name)
            if name == 'DI':
                if not self.input_reader: raise ValueError('输入数据不可用')
                if len(args) != 1 or kwargs: raise ValueError('读取输入需要一个 IO 名称')
                return self.input_reader(str(args[0]))
            funcs = {'range': range, 'len': len, 'abs': abs, 'min': min, 'max': max, 'sum': sum, 'round': round,
                     'int': int, 'float': float, 'str': str, 'list': list, 'dict': dict, 'enumerate': enumerate, 'zip': zip}
            if name not in funcs: raise ValueError(f'不支持的函数：{name}')
            if name in ('min', 'max'):
                candidates = args[0] if len(args) == 1 else args
                if isinstance(candidates, (list, tuple)) and candidates:
                    if self._data_cost(candidates) * max(self._data_cost(v) for v in candidates) > MAX_NATIVE_WORK:
                        raise ValueError('最值比较工作预算超限')
            value = funcs[name](*args, **kwargs)
            if name in ('enumerate', 'zip'): value = list(value)
            return self.guard(value)
        raise ValueError('不支持此表达式')

    def command(self, name, args, kwargs, line):
        from .program import compile_steps, ACTION_ALIASES
        if name == 'WAIT_DI':
            if len(args) not in (2, 3, 4) or set(kwargs) - {'timeout', 'debounce'}: raise ValueError('等待输入(名称, 值, timeout=5, debounce=0.1)')
            raw = f"WAIT_DI {args[0]} {args[1]} TIMEOUT {kwargs.get('timeout', args[2] if len(args)>2 else 5)} DEBOUNCE {kwargs.get('debounce', args[3] if len(args)>3 else .1)}"
        else:
            if kwargs: raise ValueError('此指令使用位置参数')
            if name in ('MOVEJ', 'MOVEL'):
                values = args[0] if len(args) == 1 else args
                if isinstance(values, str):
                    p = self.point(values)
                    if not p: raise ValueError(f'点位不存在：{values}')
                    if name == 'MOVEJ': values = p['joints']
                    else:
                        tcp = p.get('tcp') or {}
                        values = [*tcp.get('xyz_mm', []), *tcp.get('rpy_deg', [])]
                if not isinstance(values, (tuple, list)) or len(values) != 6: raise ValueError('运动需要点位或六个数值')
                if any(type(v) not in (int, float) or not math.isfinite(v) for v in values): raise ValueError('运动参数必须是有限数值')
                raw = name + ' ' + ','.join(str(v) for v in values)
            elif name == 'PAUSE':
                if args: raise ValueError('暂停不接收参数')
                raw = name
            elif name == 'DOUT':
                if len(args) != 2: raise ValueError('输出需要位号/名称、值')
                raw = name + ' ' + ','.join(str(v) for v in args)
            else:
                if len(args) != 1: raise ValueError('此指令需要一个参数')
                value = ACTION_ALIASES.get(str(args[0]), str(args[0]).upper()) if name == 'TOOL' else str(args[0])
                raw = name + ' ' + value
        step = compile_steps([(line, raw)], self.point)[0]
        if name in ('MOVEJ', 'MOVEL') and len(args) == 1 and isinstance(args[0], str):
            point = self.point(args[0]); step.update(point=point['name'], point_code=point.get('code'))
        return step
