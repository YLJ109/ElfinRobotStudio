"""纯函数生成仿真吸取放置脚本与点位表，不访问场景、设备、存储或执行器。

XYZ 单位 mm，范围 ±10000；ABC 单位度，范围 ±360；上方点沿基座 +Z
抬升 1—1000 mm，生成后仍须满足 XYZ 边界。速度 1—100%，延时 0—60 秒。
本模块仅生成几何与语义命令；完整路径、IK 及纯仿真执行许可由调用方检查。
"""
from __future__ import annotations

from decimal import Decimal
import math
import re


MAX_ITEMS = 27
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', re.ASCII)
_OPTIONS = {'items', 'wait', 'clearance', 'speed', 'pick_wait', 'blow_wait', 'tool_id'}


def _number(value, low, high, name):
    if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
        raise ValueError(f'{name}需为 {low}—{high} 范围内的有限数值')
    return float(value)


def _pose(value, name):
    if not isinstance(value, list) or len(value) != 6:
        raise ValueError(name + '需要六项 XYZABC 数值')
    return [_number(v, -10000 if i < 3 else -360, 10000 if i < 3 else 360, name)
            for i, v in enumerate(value)]


def _identifier(value, name):
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(name + '需为1—80位ASCII字母、数字、下划线或连字符，首位为字母或数字')
    return value


def _literal(value):
    # The legacy SPEED/WAIT grammar requires plain decimals, never 1e-07.
    if value == 0:
        return '0'
    text = format(Decimal(str(value)), 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def generate_pick_place(payload):
    """Return {source, points, cycles}; never mutate payload or execute commands.

    Required fields: items (1—27 distinct {id,pick,place}), wait (six-value pose).
    Defaults: clearance=100, speed=10, pick_wait=.2, blow_wait=.2,
    tool_id='vacuum-reference'. Unknown fields at either level are rejected.
    points has one WAIT row followed by four rows per item; roles are wait,
    pick_up, pick, place_up, place. cycles is the number of item transfers.
    SIM_PICK/SIM_PLACE each take the validated object ID without a slot argument.
    """
    if not isinstance(payload, dict) or set(payload) - _OPTIONS or not {'items', 'wait'} <= set(payload):
        raise ValueError('需要 items、wait，且不接受未知生成选项')
    items = payload['items']
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise ValueError('每次需要1—27件工件')
    wait = _pose(payload['wait'], '等待/起点')
    clearance = _number(payload.get('clearance', 100), 1, 1000, '上方距离')
    speed = _number(payload.get('speed', 10), 1, 100, '速度')
    pick_wait = _number(payload.get('pick_wait', .2), 0, 60, '吸取等待')
    blow_wait = _number(payload.get('blow_wait', .2), 0, 60, '吹气等待')
    tool_id = _identifier(payload.get('tool_id', 'vacuum-reference'), '工具ID')
    gripper = tool_id == 'robotiq-2f85'
    points = [{'name': 'WAIT', 'pose': wait.copy(), 'role': 'wait'}]
    lines = ['# 仿真吸取放置程序（可编辑）', '# 等待/起点 → A原料区吸取 → B放料区放置 → 等待/起点',
             '# SIM_PICK / SIM_PLACE 仅供仿真；执行前准备并检查完整路径',
             'REQUIRE_TOOL ' + tool_id, 'SPEED ' + _literal(speed),
             '# 起始状态：关闭吸真空与破真空，不猜测真实DO映射',
             'TOOL VACUUM_OFF', 'TOOL BLOW_OFF']
    if gripper:
        lines[-3:] = ['# 起始状态：张开夹爪，不发送真实 IO', 'TOOL OPEN']
    seen = set()

    def move(name, pose):
        lines.extend(['# ' + name, 'MOVEL ' + ','.join(_literal(v) for v in pose)])

    for index, item in enumerate(items, 1):
        if not isinstance(item, dict) or set(item) != {'id', 'pick', 'place'}:
            raise ValueError('每件工件仅接受 id、pick、place')
        object_id = _identifier(item['id'], '工件ID')
        if object_id in seen:
            raise ValueError('工件ID不能重复')
        seen.add(object_id)
        pick, place = _pose(item['pick'], 'A取料点'), _pose(item['place'], 'B放料点')
        pick_up, place_up = pick.copy(), place.copy()
        pick_up[2] += clearance
        place_up[2] += clearance
        pick_up, place_up = _pose(pick_up, 'A上方点'), _pose(place_up, 'B上方点')
        a_up, a_pick, b_up, b_place = f'A{index}_UP', f'A{index}_PICK', f'B{index}_UP', f'B{index}_PLACE'
        for name, pose, role in ((a_up, pick_up, 'pick_up'), (a_pick, pick, 'pick'),
                                 (b_up, place_up, 'place_up'), (b_place, place, 'place')):
            points.append({'name': name, 'pose': pose.copy(), 'object_id': object_id, 'role': role})
        lines.append(f'# 第{index}件工件：{object_id}')
        move('WAIT 等待/起点', wait)
        move(a_up + ' A原料区上方点', pick_up)
        move(a_pick + ' A原料区取料点', pick)
        lines.extend(['TOOL CLOSE'] if gripper else ['TOOL BLOW_OFF', 'TOOL VACUUM_ON'])
        if pick_wait:
            lines.append('WAIT ' + _literal(pick_wait))
        lines.append('SIM_PICK ' + object_id)
        move(a_up + ' A原料区抬起', pick_up)
        move(b_up + ' B放料区上方点', place_up)
        move(b_place + ' B放料区放料点', place)
        lines.extend(['TOOL OPEN'] if gripper else ['TOOL VACUUM_OFF', 'TOOL BLOW_ON'])
        if blow_wait:
            lines.append('WAIT ' + _literal(blow_wait))
        lines.append('SIM_PLACE ' + object_id)
        if not gripper: lines.append('TOOL BLOW_OFF')
        move(b_up + ' B放料区抬起', place_up)
        move('WAIT 返回等待/起点', wait)
    return {'source': '\n'.join(lines) + '\n', 'points': points, 'cycles': len(items)}
