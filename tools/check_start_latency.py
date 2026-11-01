"""SIM03 代码级启动延迟：分拣 / 绘制 / 写字「开始」→「首次关节变化」。

只调用后端真正被 HTTP 路由调用的启动函数（PlayService + RobotPlay.generate/run、
Drawing.preview/run），自己按固定步长推进仿真，不经过浏览器、不用假进度。
所以这里得到的是**代码级证据**：扣掉了 HTTP 往返与画面渲染，不能当成 UI 实测数字。

在独立 data_dir 里跑；不连真机、不开相机、不监听端口。
    backend/.venv/Scripts/python.exe -B tools/check_start_latency.py [--repeat N]
"""
import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIRST_MOTION_EPS = 1e-6   # 度；首动判据只排除浮点噪声
TICK_REAL_S = .01         # 每次推进的仿真秒数
TICK_WALL_SLEEP = .002    # 让出事件循环；执行器本身以 20ms 为一步，睡眠不能为 0
DEADLINE_S = 120.


def isolate_data_dir():
    """Must run before importing backend: config reads ELFIN_DATA_DIR at import."""
    data = tempfile.mkdtemp(prefix='elfin-latency-')
    os.environ['ELFIN_DATA_DIR'] = data
    os.environ.setdefault('HUYAN_NO_REAL', '1')
    return data


DATA_DIR = isolate_data_dir()
sys.path.insert(0, str(ROOT))

from backend.core.world import World                      # noqa: E402
from backend.play_routes import PlayService              # noqa: E402
from backend.robot_play_routes import RobotPlay          # noqa: E402
from backend.application.drawing import Drawing          # noqa: E402


START_POSE = [0., 0., 0., 0., 0., 0.]   # 仿真零位姿态，保证三次测量同一几何起点


def new_world(tag):
    world = World(data_dir=Path(DATA_DIR) / tag)
    world.settings['mode'] = 'sim'
    world.real = None
    world.robot_play = RobotPlay(world)
    world.sim.q = list(START_POSE)
    return world


async def run_to_first_motion(world, start, q0):
    """start(): 一次「点击开始」里该做的全部后端调用。返回各阶段耗时。"""
    t0 = time.perf_counter()
    phase = await start(t0)
    submitted = time.perf_counter() - t0
    while time.perf_counter() - t0 < DEADLINE_S:
        world._tick(TICK_REAL_S, time.monotonic())
        await asyncio.sleep(TICK_WALL_SLEEP)
        if any(abs(a - b) > FIRST_MOTION_EPS for a, b in zip(world.sim.q, q0)):
            first = time.perf_counter() - t0
            return {'phase': phase, 'submit_s': submitted, 'first_motion_s': first}
    raise RuntimeError('等待首动超时：%ss' % DEADLINE_S)


async def case_sorting():
    world = new_world('sorting')
    play = world.robot_play
    group = PlayService(world).setup(
        {'mode': 'sorting', 'criterion': 'color', 'counts': {'red': 1, 'green': 1, 'blue': 1}})['group']
    q0 = list(world.sim.q)

    async def start(t0):
        # 与 RobotPlayProgram.vue『开始』一致：先完整路径预检，再提交执行。
        plan = await play.generate({'group': group, 'action': 'sorting', 'criterion': 'color',
                                    'perception': 'scene', 'clearance': 120, 'speed': 100})
        preflight = time.perf_counter() - t0
        if not plan.get('ok'):
            raise RuntimeError(plan.get('why') or '预检未通过')
        started = play.run({'plan_id': plan['plan_id'], 'source': plan['source']})
        if not started.get('ok'):
            raise RuntimeError(started.get('why') or '执行未启动')
        return {'preflight_s': preflight, 'cycles': plan.get('cycles')}

    try:
        return await run_to_first_motion(world, start, q0)
    finally:
        await world.stop_execution('latency sorting done')


def drawing_payload():
    return {'shape': 'heart', 'center': [-500, 0, 700, 90, 0, -90], 'plane': 'xz',
            'width_mm': 280, 'height_mm': 240, 'angle_deg': 0, 'speed_pct': 100, 'repeats': 1,
            'direction': 270, 'range_center_mm': [-500, 0, 700], 'range_size_mm': [900, 300, 500],
            'center_policy': 'range_lower'}


def writing_payload():
    # 手写一笔折线，不依赖字体文件；只测启动链路，不测字形质量。
    strokes = [[[0., 0.], [.6, 0.]], [[.3, -.4], [.3, .4]]]
    return {'shape': 'text', 'text': '十', 'strokes': strokes, 'font_size_mm': 90., 'writing_z_mm': 1.,
            'center': [-500, 0, 16, 180, 0, 0], 'range_center_mm': [-500, 0, 40],
            'range_size_mm': [140, 140, 86], 'plane': 'xy', 'direction': 0, 'center_policy': 'fixed',
            'width_mm': 90, 'height_mm': 90, 'angle_deg': 0, 'speed_pct': 100, 'repeats': 1}


async def case_drawing(tag, payload, tool):
    world = new_world(tag)
    world.ensure_sim_task_tool(tool)
    drawing = Drawing(world)
    q0 = list(world.sim.q)

    async def start(t0):
        # 与 DrawingTask/TextWritingTask『执行』一致：预检直接串在同一次开始里。
        ticket = drawing.preview(payload)
        if drawing.worker:
            await drawing.worker
        preflight = time.perf_counter() - t0
        state = drawing.state()
        if state.get('phase') != 'ready':
            raise RuntimeError(state.get('why') or '预检未通过')
        result = drawing.run({'plan_id': ticket['plan_id']})
        if not result.get('ok'):
            raise RuntimeError(result.get('why') or '执行未启动')
        return {'preflight_s': preflight, 'steps': len(state.get('path') or [])}

    try:
        return await run_to_first_motion(world, start, q0)
    finally:
        await drawing.cancel({'plan_id': drawing.plan['id']}) if drawing.plan else None
        await world.stop_execution('latency drawing done')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeat', type=int, default=2)
    parser.add_argument('--only', default='sorting,drawing,writing')
    args = parser.parse_args()
    wanted = [name for name in args.only.split(',') if name]
    cases = {
        'sorting': case_sorting,
        'drawing': lambda: case_drawing('drawing', drawing_payload(), 'drawing'),
        'writing': lambda: case_drawing('writing', writing_payload(), 'writing'),
    }
    report = {}
    for name in wanted:
        rows = []
        for _ in range(max(1, args.repeat)):
            rows.append(asyncio.run(cases[name]()))
        first = sorted(row['first_motion_s'] for row in rows)
        report[name] = {
            'runs': rows,
            'first_motion_s_min': round(first[0], 3),
            'first_motion_s_median': round(first[len(first) // 2], 3),
            'preflight_s_median': round(sorted(row['phase'].get('preflight_s', 0) for row in rows)[len(rows) // 2], 3),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    shutil.rmtree(DATA_DIR, ignore_errors=True)


if __name__ == '__main__':
    main()
