"""Controlled continuous-input / dense-scene follow checks; no physical camera."""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
import copy
import json
import math
from pathlib import Path
import random
import statistics
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_hand_follow as fixtures
from backend.core.world import World
from backend.core.kinematics import RobotModel
from backend.core.objects import capsule_object_gap
from backend.domain.object_contacts import physical, penetrates
from backend.application.board_games import GameStore
from backend.application.board_robot import BoardExecutor


def brute_hits(capsules, objects, simulation=False):
    """Reference predicates before spatial pruning; no shortcut or relaxed test."""
    hits = []
    for obj in objects:
        if not (physical(obj) if simulation else obj.get('collide', True)):
            continue
        for cap in capsules:
            if simulation:
                if penetrates({**cap, 'kind': 'capsule'}, obj):
                    gap = capsule_object_gap(cap, obj) if obj['kind'] == 'box' else None
                    hits.append({'link': cap['link'], 'name': obj['name'], 'gap_mm': gap, 'penetrating': True})
            else:
                gap = capsule_object_gap(cap, obj)
                if gap < 0:
                    hits.append({'link': cap['link'], 'name': obj['name'], 'gap_mm': gap})
    return hits


class SpatialQueryCases(unittest.TestCase):
    def test_pruned_query_preserves_penetrating_touching_rotated_and_far_pairs(self):
        rng = random.Random(109)
        for sample in range(8):
            caps = RobotModel('elfin5').capsules([rng.uniform(-60, 60) for _ in range(6)])
            cap = caps[3]
            middle = [(a+b)/2 for a, b in zip(cap['a'], cap['b'])]
            rows = []
            for index in range(225):
                kind = ('box', 'sphere', 'cylinder')[index % 3]
                rows.append({'id': str(index), 'name': str(index), 'kind': kind,
                             'xyz': [1300+index % 15*30, 1200+index//15*30, 50],
                             'size': [10, 10, 12], 'scale': 1., 'rotation_deg': [15, 25, 35], 'collide': True})
            for index, kind in enumerate(('box', 'sphere', 'cylinder')):
                rows.append({'id': 'near'+str(index), 'name': 'near'+str(index), 'kind': kind,
                             'xyz': middle, 'size': [14, 14, 14], 'rotation_deg': [20, 35, 10], 'collide': True})
            rows.append({'id': 'touch', 'name': 'touch', 'kind': 'sphere', 'size': [5, 5, 5],
                         'xyz': [middle[0]+cap['r_mm']+5, middle[1], middle[2]], 'collide': True})
            for simulation in (False, True):
                with self.subTest(sample=sample, simulation=simulation):
                    self.assertEqual(World._object_hits(caps, rows, simulation=simulation),
                                     brute_hits(caps, rows, simulation=simulation))


class FollowPipelineCases(unittest.IsolatedAsyncioTestCase):
    async def test_worker_miss_does_not_brake_a_checked_short_segment_and_reversal_rejects_it(self):
        fixture = fixtures.WorldTests('test_background_goal_from_before_reversal_is_never_reinstalled')
        await fixture.asyncSetUp()
        try:
            await fixture.begin()
            w = fixture.w
            follow = w.hand_follow
            await fixture.action('sample', fixtures.observation(follow.token, 2, u=.6))
            now = time.monotonic()
            follow.solve_goal(now)
            self.assertIsNotNone(follow._goal)
            goal = list(follow._goal)
            model = copy.copy(follow.model)
            result = copy.deepcopy(follow, {id(follow.model): model})
            result._goal = None
            result._solver_cause = 'stalled'
            failed = asyncio.get_running_loop().create_future()
            failed.set_result((follow, follow.token, w._cfg_rev, follow.returning, now, result))
            w._hand_solver_task = failed
            follow._last_ik = now
            w._poll_hand_solver(now)
            self.assertEqual(follow._goal, goal)
            self.assertTrue(World._hand_goal_still_useful(follow))
            before = list(w.sim.q)
            w._tick_hand_follow(.02, time.monotonic())
            self.assertNotEqual(w.sim.q, before)
            await fixture.action('sample', fixtures.observation(follow.token, 3, u=.3))
            self.assertFalse(World._hand_goal_still_useful(follow))
            self.assertIsNone(follow._goal)
        finally:
            await fixture.asyncTearDown()

    async def test_continuous_latest_inputs_in_full_board_scene_keep_inside_without_backlog(self):
        fixture = fixtures.WorldTests('test_background_goal_from_before_reversal_is_never_reinstalled')
        await fixture.asyncSetUp()
        try:
            w = fixture.w
            w.objects.clear()
            games = GameStore()
            games.new('go')
            BoardExecutor(w, games).ensure('go', force=True)
            await fixture.begin()
            w._hand_async_solver = True
            follow = w.hand_follow
            start = list(follow.actual)
            objects = w.objects.list()
            from backend.core.safety import object_capsules
            caps = object_capsules(w.motion_model(), w.motion_model().capsules(w.current_joints(), conservative=True))
            timings = {}
            for name, call in (('unpruned_legacy_query', lambda: brute_hits(caps, objects)),
                               ('production_query', lambda: World._object_hits(caps, objects))):
                samples = []
                for _ in range(12):
                    began = time.perf_counter()
                    call()
                    samples.append((time.perf_counter()-began)*1000)
                timings[name+'_median_ms'] = statistics.median(samples)
            work, moves, tasks, ages = [], 0, set(), []
            for frame in range(90):
                now = time.monotonic()
                sample = fixtures.observation(follow.token, frame+2,
                    u=.5+.12*math.sin(frame/18), v=.5+.08*math.sin(frame/25))
                response = await fixture.action('sample', sample)
                self.assertTrue(response['ok'], response)
                now = time.monotonic()
                began = time.perf_counter()
                w._poll_hand_solver(now)
                before = list(w.sim.q)
                w._tick(1/60, now)
                work.append((time.perf_counter()-began)*1000)
                moves += before != w.sim.q
                if w._hand_solver_task is not None:
                    tasks.add(id(w._hand_solver_task))
                self.assertTrue(follow._inside(follow.actual))
                self.assertEqual(follow.seq, frame+2)
                ages.append(now-follow._last_sample)
                await asyncio.sleep(max(.001, 1/60-(time.monotonic()-now)))
            self.assertGreater(moves, 30, 'fresh continuous input must create actual accepted motion')
            self.assertGreater(math.dist(start[:3], follow.actual[:3]), 1.)
            self.assertTrue(follow.active)
            self.assertFalse(follow.blocked_reason)
            self.assertLessEqual(len(tasks), 90, 'one live worker slot, not a frame queue')
            self.assertLess(max(ages), .3)
            timings.update(objects=len(objects), controlled_frames=90, moving_frames=moves,
                           control_work_median_ms=statistics.median(work),
                           control_work_p95_ms=sorted(work)[int(len(work)*.95)],
                           control_work_max_ms=max(work), physical_camera=False)
            print('FOLLOW_PIPELINE_METRICS ' + json.dumps(timings), flush=True)
        finally:
            await fixture.asyncTearDown()
            pending = getattr(fixture.w, '_hand_solver_task', None)
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
