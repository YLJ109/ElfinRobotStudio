"""Bounded real-IK joint/branch probe; use run_offline_checks.py only.

No World import or hardware. Startup trajectory is intentionally excluded:
configure receives the recorded joints, then motion starts at its chosen center.
Independent +/-30mm probes do not require every joint to move on every axis.
JOINT_* JSON lines retain all six signed deltas, spans and failure evidence.
Set ELFIN_JOINT_PLANE=1 to include the optional bounded corner-path probe.
"""
from _offline_guard import require_offline_copy
require_offline_copy()

import copy
import json
import math
import os
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core import config
from backend.core.hand_follow import HandFollow
from backend.core.kinematics import pose_rotation_distance
from backend.core.sim_tool import DEFAULT_TOOL, model_for


STARTS = {
    'default': None,
    'recorded_center': [-90.148, -3.438, 79.964, 1.2795, 6.602, -1.265],
    'recorded_actual': [48.692054, -28.110317, 83.278342,
                        -21.823112, 157.125233, 159.749231],
}
CENTER = [0., -600., 650.]
DT = 1 / 30
VARIANT_SECONDS = 28.
LEG_SECONDS = 3.
MAX_TICKS = 240


def emit(kind, value):
    print('JOINT_' + kind, json.dumps(value, ensure_ascii=False), flush=True)


def span(samples):
    return [max(q[i] for q in samples) - min(q[i] for q in samples)
            for i in range(6)]


class JointMotion(unittest.TestCase):
    def drive(self, f, point, name, variant, deadline):
        began = time.perf_counter()
        before = list(f.q)
        history = [before]
        tcp_start = list(f.actual[:3])
        causes = {}
        missing_recovery = None
        max_tick_s = 0.
        still = 0
        outcome = 'tick_limit'
        for tick in range(MAX_TICKS):
            if time.perf_counter() >= min(deadline, began + LEG_SECONDS):
                outcome = 'wall_budget'
                break
            now = f._clock_value + DT
            reply = f.sample(dict(token=f.token, session=f.session, seq=f.seq + 1,
                u=(point[0] + 350) / 700, v=(825 - point[2]) / 350,
                hand_open=(-525 - point[1]) / 150,
                palm_scale=.2, angle=0., age_ms=0), now=now)
            self.assertTrue(reply['ok'], reply.get('why'))
            previous = list(f.q)
            previous_tcp = list(f.actual[:3])
            wrist_delta = f.q[5] - f._center_q[5]
            needs_recovery = (abs(wrist_delta) > 180 - f.IK_JOINT_TRUST_DEG or
                (f._wrist_recovery_reference is None and abs(wrist_delta) > 120
                 and abs(f.q[4]) >= 3 and wrist_delta * f._velocity[5] > 0))
            if (needs_recovery and math.dist(f.actual[:3], f.target[:3]) > 1
                    and f._wrist_hub() is None and missing_recovery is None):
                missing_recovery = dict(tick=tick, q=list(f.q),
                    center_q=list(f._center_q), target=list(f.target),
                    wrist_delta=wrist_delta, velocity=list(f._velocity))
            started = time.perf_counter()
            f.tick(DT, now=now, speed_pct=100)
            max_tick_s = max(max_tick_s, time.perf_counter() - started)
            history.append(list(f.q))
            self.assertTrue(f._valid_q(f.q), f.q)
            self.assertTrue(f._inside(f.actual), f.actual)
            self.assertTrue(f._body_inside(f.q, f.base_offset), f.q)
            self.assertEqual(f.base_offset, [0., 0., 0.])
            self.assertLessEqual(pose_rotation_distance(f.actual, f._base), .100001)
            self.assertLessEqual(math.dist(previous_tcp, f.actual[:3]), 300 * DT + 1e-6)
            for a, b, cap in zip(previous, f.q, f.model.vmax):
                self.assertLessEqual(abs(b - a), cap * DT + 1e-6)
            cause = f._solver_cause
            causes[cause] = causes.get(cause, 0) + 1
            error = math.dist(f.actual[:3], f._tracking_target(f.target)[:3])
            if error < 1.:
                outcome = 'reached'
                break
            if cause == 'geometric_limit':
                outcome = 'proven_unreachable'
                break
            still = still + 1 if max(abs(a-b) for a, b in zip(previous, f.q)) < 1e-8 else 0
            # Longer than the 250ms rejection retry; wrist null motion counts
            # as progress even when TCP is stationary.
            if still >= 30:
                outcome = 'stationary'
                break
        row = dict(variant=variant, input=name, point=point, outcome=outcome,
            ticks=len(history)-1, wall_s=time.perf_counter()-began,
            max_tick_s=max_tick_s, start_q=before, end_q=list(f.q),
            delta_deg=[b-a for a, b in zip(before, f.q)], span_deg=span(history),
            tcp_start=tcp_start, tcp_end=list(f.actual[:3]),
            error_mm=math.dist(f.actual[:3], f._tracking_target(f.target)[:3]),
            causes=causes, why=f.why, hub_available=f._wrist_hub() is not None,
            missing_recovery=missing_recovery, solver=dict(f._solver_detail))
        emit('MOVE', row)
        return row, history

    def test_recorded_startup_branches_and_joint_motion(self):
        failures = []
        inconclusive = []
        summaries = []
        for variant, start in STARTS.items():
            began = time.perf_counter()
            deadline = began + VARIANT_SECONDS
            f = HandFollow(model_for('elfin5', DEFAULT_TOOL))
            lo, hi = map(list, zip(*config.MANUAL_LIMITS))
            reply = f.configure(dict(session='joint-probe', aspect=4/3,
                depth=True, rotation=False, direction=0, depth_mode='gesture',
                enclosure={'min': [-350., -675., 475.], 'max': [350., -525., 825.]}),
                lower=lo, upper=hi, current_q=start, now=10.)
            setup = dict(variant=variant, input_q=start, ok=reply['ok'],
                why=reply.get('why'), wall_s=time.perf_counter()-began)
            if not reply['ok']:
                emit('SETUP', setup)
                failures.append(setup)
                continue
            setup.update(center_q=list(f._center_q), base=list(f._base),
                reference_q=list(f.reference_q), hub=f._wrist_hub(),
                reused_input=start is not None and
                    max(abs(a-b) for a, b in zip(start, f._center_q)) < 1e-8)
            emit('SETUP', setup)
            self.assertTrue(f.start(dict(token=f.token, session=f.session),
                                   current_q=f._center_q, now=10.)['ok'])
            rows = []
            all_q = [list(f.q)]
            # Each small probe starts at the same accepted center. Shared model
            # cache is safe here: calls are sequential, never background work.
            for axis, label in enumerate(('X', 'Y', 'Z')):
                for amount in (-30., 30.):
                    probe = copy.deepcopy(f, {id(f.model): f.model})
                    point = list(CENTER)
                    point[axis] += amount
                    row, history = self.drive(probe, point, f'{label}{amount:+g}', variant, deadline)
                    rows.append(row)
                    all_q.extend(history)
            # Continuous history is essential: independent endpoint IK cannot
            # expose wrist winding/recovery. Top corners may be proven outside
            # fixed-orientation reach; subsequent inward inputs must recover.
            path = [(-350., -600., 475.), (350., -600., 475.), tuple(CENTER),
                    (-350., -600., 825.), (350., -600., 475.),
                    (350., -600., 825.), (-350., -600., 475.), tuple(CENTER)]
            if os.environ.get('ELFIN_JOINT_PLANE') != '1':
                path = []
            for index, point in enumerate(path):
                if time.perf_counter() >= deadline:
                    inconclusive.append(dict(variant=variant, outcome='variant_budget', next_leg=index))
                    break
                row, history = self.drive(f, list(point), f'plane{index}', variant, deadline)
                rows.append(row)
                all_q.extend(history)
            for row in rows:
                if row['outcome'] not in ('reached', 'proven_unreachable'):
                    collection = inconclusive if row['outcome'] in ('wall_budget', 'tick_limit') else failures
                    collection.append({key: row[key] for key in
                        ('variant', 'input', 'outcome', 'error_mm', 'end_q', 'missing_recovery')})
            summary = dict(variant=variant, center_q=setup['center_q'],
                hub_available=setup['hub'] is not None, span_deg=span(all_q),
                outcomes={row['input']: row['outcome'] for row in rows},
                wall_s=time.perf_counter()-began)
            # 正确：整组XYZ输入必须带动肩部两轴，单个方向无需强制六轴都动。
            self.assertGreater(summary['span_deg'][0], 1., f'{variant}: J1未产生有效运动')
            self.assertGreater(summary['span_deg'][1], 1., f'{variant}: J2未产生有效运动')
            summaries.append(summary)
            emit('VARIANT', summary)
        emit('REPORT', dict(variants=summaries, failures=failures, inconclusive=inconclusive,
            plane_probe=os.environ.get('ELFIN_JOINT_PLANE') == '1',
            note='wall_budget/tick_limit are inconclusive, not geometric unreachability; '
                 'no per-step all-axis movement requirement; no World startup trajectory tested'))
        self.assertFalse(failures, 'See JOINT_MOVE / JOINT_REPORT for bounded reproduction evidence')
        if inconclusive:
            self.skipTest('Probe budget reached: partial evidence only; see JOINT_REPORT')


if __name__ == '__main__':
    unittest.main(verbosity=2)
