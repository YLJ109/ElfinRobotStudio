"""Build and execute the requested demonstration library in a disposable checkout."""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio
import json
import math
import time
from backend.core.world import World
from backend.application.drawing import Drawing
from backend.core.planning import inspect_path
from backend.core.trajectory import JointTrajectory


async def main():
    w = World()
    w.settings['mode'] = 'sim'
    d = Drawing(w)
    programs = []
    prepared = {}
    async def execute(name, source):
        # Each saved example must also start independently from simulation home.
        w.sim.q = [0.0]*6
        result = await w.load_program(source)
        assert result['ok'], result
        assert w.start_program()['ok']
        deadline = time.monotonic() + 65
        while w.runner.state not in ('finished', 'aborted', 'error'):
            w._tick(.05, time.monotonic())
            await asyncio.sleep(.001)
            assert time.monotonic() < deadline, (name, w.runner.snapshot())
        assert w.runner.state == 'finished', (name, w.runner.snapshot())
        assert w.runner.completed_index == len(w.runner.steps)
        assert w.sim.traj is None
        print('EXECUTED', name, len(w.runner.steps), flush=True)
    for shape, label in [('heart','爱心'),('square','正方形'),('rectangle','长方形'),
                         ('circle','圆圈'),('nod','点头'),('shake','摇头'),
                         ('heart','小爱心'),('circle','小圆圈')]:
        small = label.startswith('小')
        payload = dict(shape=shape, center=[-500,0,700,90,0,0], plane='xz',
                       width_mm=120 if small else 240, height_mm=100 if small else 200,
                       range_center_mm=[-500,0,700], range_size_mm=[900,300,500],
                       center_policy='range_lower', angle_deg=3 if shape in ('nod','shake') else 0,
                       speed_pct=100, repeats=1)
        d.preview(payload)
        await asyncio.wait_for(d.worker, 95)
        assert d.phase == 'ready', (label, d.why)
        plan = d.plan
        assert plan['max_tcp_deviation_mm'] <= 2
        source = '# 仿真示例：'+label+'；使用顶部速度，末端左吸盘尖端轨迹\n'+plan['program']
        prepared[label] = plan['steps']
        await execute(label, source)
        programs.append(dict(name=f'{len(programs)+1:02d} {label}·仿真', src=source))
        d.phase = 'idle'
    # Extract each straight edge from the already checked rectangle compiler.
    for axis, label in [(0,'水平直线'),(2,'竖直直线')]:
        steps = prepared['长方形']
        index = next(i for i,s in enumerate(steps) if s['drawing_trace'] and
                     abs(s['drawing_line_end'][axis]-s['drawing_line_start'][axis]) > 1)
        start = steps[index-1]['joints']
        edge=[]
        for step in steps[index:]:
            a,b=step['drawing_line_start'],step['drawing_line_end']
            if not step['drawing_trace'] or abs(b[axis]-a[axis]) <= .001:
                break
            edge.append(step)
        assert edge
        # The independent approach is also sampled; never assume a shortened
        # sequence inherits collision clearance from a different approach.
        lo, hi = w.limits()
        curve = JointTrajectory.build([w.current_joints(),start],w.motion_model().vmax,100)
        report = inspect_path(w.motion_model(),w.current_joints(),start,w.settings,lo,hi,
                              w.objects.list(),trajectory=curve)
        assert not report['risk_detected'], (label, report)
        source = '# 仿真示例：'+label+'；使用顶部速度\nMOVEJ '+','.join(format(v,'.10g') for v in start)+'\n'+'\n'.join(s['src'] for s in edge)
        await execute(label,source)
        programs.append(dict(name=f'{len(programs)+1:02d} {label}·仿真',src=source))
    candidates = []
    for label in ('爱心','正方形','圆圈','点头','摇头'):
        steps=prepared[label]
        for index in (0,len(steps)//3,2*len(steps)//3):
            q=steps[index]['joints']
            if all(max(abs(a-b) for a,b in zip(q,p['joints']))>2 for p in candidates):
                candidates.append(dict(joints=[round(v,3) for v in q],label=label))
    assert len(candidates)>=10
    points=[]
    for i,row in enumerate(candidates[:10],1):
        q=row['joints']
        lo,hi=w.limits()
        for start in ([0]*6, w.current_joints()):
            curve=JointTrajectory.build([start,q],w.motion_model().vmax,100)
            report=inspect_path(w.motion_model(),start,q,w.settings,lo,hi,w.objects.list(),trajectory=curve)
            assert not report['risk_detected'], (i,report)
        points.append(dict(name=f'P{i}',joints=q,source='sim',note='仿真演示姿势：'+row['label']))
    assert len(programs)==len(points)==10
    print('DEMO_LIBRARY_JSON '+json.dumps(dict(points=points,programs=programs,
          validation=dict(programs_executed=10,points_checked=10,mode='sim',
                          max_tcp_deviation_mm=2,physical_robot_verified=False)),ensure_ascii=False),flush=True)
    await d.close()
    await w.stop_execution('demo library validation complete')

if __name__=='__main__':
    asyncio.run(main())
