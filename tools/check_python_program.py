"""Pure interpreter tests: no World, no hardware or persistence."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sim.backend.core.program import parse, Runner, ProgramError

POINT = {'id': 1, 'code': 'P1', 'name': '取料点', 'joints': [1,2,3,4,5,6], 'tcp': {'xyz_mm':[260,-230,80], 'rpy_deg':[0,90,0]}, 'source':'sim'}
def lookup(ref): return POINT if ref in ('P1','取料点','1') else None
def run(source, reader=None, limit=30000):
    runner = Runner(); runner.input_reader = reader
    runner.load(parse(source, lookup, point_items=[POINT])); assert runner.start()
    actions = []; clock = 0
    for _ in range(limit):
        action = runner.advance(clock); clock += .1
        if action: actions.append(action); runner.complete()
        if runner.state in ('finished','aborted'): break
    assert runner.state in ('finished','aborted'), 'not cooperative'
    return runner, runner.steps

def main():
    cases = [
        ('变量列表字典嵌套函数', '配置 = {"速度": 15, "次数": 2}\n路径 = [P1, "取料点"]\n定义 作业(点):\n    关节移动(点)\n    等待(0.1)\n循环 i 在 范围(配置["次数"]):\n    如果 i == 0:\n        速度(配置["速度"])\n    循环 点 在 路径:\n        作业(点)\n', ['speed','movej','wait','movej','wait','movej','wait','movej','wait']),
        ('英文函数返回while', 'def double(x):\n    return x * 2\nx = 0\nwhile x < 3:\n    x += 1\n    if x == 2:\n        continue\n    SPEED(double(x))\n', ['speed','speed']),
        ('容器方法及解包', 'items = []\nitems.append(P1)\nconfig = {"v": 10}\nconfig.update({"v": 12})\nfor index, point in enumerate(items):\n    SPEED(config.get("v"))\n    MOVEJ(point)\n', ['speed','movej']),
        ('动态输入判断', 'if DI("ready") == 1:\n    MOVEL(P1)\nelse:\n    PAUSE()\n', ['movel']),
        ('嵌套break', 'for i in range(2):\n    for j in range(3):\n        if j == 1:\n            break\n        WAIT(0)\n', ['wait','wait']),
        ('中文容器方法', '列表值 = []\n列表值.添加(5)\n参数 = {"速度": 9}\n速度(参数.获取("速度"))\n等待(列表值.弹出() / 10)\n', ['speed','wait']),
    ]
    passed = 0
    for title, source, expected in cases:
        runner, actions = run(source, lambda alias: 1)
        assert runner.state == 'finished', (title, runner.error)
        assert [s['kind'] for s in actions] == expected, title
        if expected == ['movel']: assert actions[0]['pose'] == [260,-230,80,0,90,0]
        passed += 1
    for source in ('import os', 'open("x")', 'x = [1] * 1000000000', 'while True:\n    pass', 'x = (1).__class__', 'SPEED(101)', 'MOVEJ([1,2,3,4,5,float("nan")])', 'def f():\n    f()\nf()', 'x = "%1000000000s" % 1'):
        try:
            runner, _ = run(source)
            assert runner.state == 'aborted', source
        except ProgramError: pass
        passed += 1
    runner = Runner(); runner.load(parse('while True:\n    pass', lookup)); runner.start(); runner.advance(0); runner.stop(); assert runner.advance(1) is None; passed += 1
    runner, actions = run('MOVEJ(P1)'); assert actions[0]['joints'] == POINT['joints']; assert runner.start(); passed += 1
    assert parse('MOVEL P1', lookup)[0]['pose'] == [260,-230,80,0,90,0]; passed += 1
    print(f'python-program: {passed}/{passed} passed')
if __name__ == '__main__': main()
