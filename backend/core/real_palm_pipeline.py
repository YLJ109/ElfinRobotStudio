"""Palm -> bounded IK -> commissioned Servo mailbox bridge.

No sockets, enabling, connection, initial
pose motion or automatic return-to-centre exists here. A host must supply an
already commissioned driver, bound tool model and actual path validator.
"""
from __future__ import annotations

import asyncio
from copy import copy
import math
import secrets
import time

from backend.core.position_servo import solve_position_step, POSITION_TOLERANCE_MM


def vector(value, count):
    return (isinstance(value, (list, tuple)) and len(value) == count
            and all(type(v) in (int, float) and math.isfinite(v) for v in value))


class RealPalmTargetPipeline:
    """One latest observation and one IK worker; never a queue of hand poses.

    configure() is an internal host API, NOT a browser qualification endpoint.
    read_context returns camera_session, connection and tool_context identities.
    profile/driver commissioning and command ownership remain the host's job.
    """
    def __init__(self, model, driver, read_context, validate_path, *,
                 startup=None, solve=solve_position_step, clock=time.monotonic,lock_j6=True):
        if not callable(read_context) or not callable(validate_path):
            raise ValueError('缺少真实上下文和整段路径检查器')
        self.model, self.driver = model, driver
        self.read_context, self.validate_path = read_context, validate_path
        self.solve, self.clock = solve, clock
        self.startup = startup
        self.lock_j6=lock_j6
        self.generation = 0
        self.token = secrets.token_hex(16)
        self.context = self.workspace = self.latest = None
        self.seq = self.frame_seq = -1
        self.published_seq=-1
        self.worker = None
        self.reason = '未配置'

    def configure(self, *, camera_session, connection, tool_context,
                  center, size, direction=0, standoff=0., role='follow'):
        if (not isinstance(camera_session, str) or not camera_session
                or connection is None or not isinstance(tool_context, str) or not tool_context
                or not vector(center, 3) or not vector(size, 3)
                or any(not 0 < n <= 5000 for n in size)
                or type(direction) is not int or direction not in (0, 90, 180, 270)
                or role not in ('follow','track')
                or type(standoff) not in (int, float) or not math.isfinite(standoff) or not 0 <= standoff <= 500):
            raise ValueError('跟随框或真实工具绑定无效')
        # Coordinates have the same single rotation as the shared workspace.
        span = [size[1], size[0], size[2]] if direction in (90, 270) else list(size)
        if center[2] - span[2]/2 < 0:
            raise ValueError('跟随框不能低于地面')
        context = dict(camera_session=camera_session, connection=connection, tool_context=tool_context)
        if self.read_context() != context:
            raise ValueError('相机、连接或工具修订已变化')
        self.invalidate('正在重新配置')
        if self.startup is not None:
            self.startup.cancel('范围已配置，需重新确认中心与J6零位')
        self.context = context
        forward=([0.,-1.,0.],[1.,0.,0.],[0.,1.,0.],[-1.,0.,0.])[direction//90]
        # The palm box and TCP box are distinct. A horizontal optical-axis
        # offset must not lower the camera or first move through the palm.
        tcp_center=[c-f*standoff for c,f in zip(center,forward)]
        self.workspace = dict(center=list(center), tcp_center=tcp_center, forward=list(forward),
                              role=role,size=list(size), direction=direction,
                              standoff=float(standoff),
                              low=[x-h/2 for x,h in zip(tcp_center,span)],
                              high=[x+h/2 for x,h in zip(tcp_center,span)])
        self.reason = '等待主机受控到中心；本输入模块不自动居中'
        return self.token

    def invalidate(self, reason='已撤销输入'):
        self.generation += 1
        self.token = secrets.token_hex(16)
        self.latest = None
        self.seq = self.frame_seq = -1
        self.published_seq=-1
        self.reason = reason
        # A running Python worker is not killed by cancelling an await. Keep
        # its single occupied slot until it really exits; generation gates it.

    def _same(self):
        try:
            return self.context is not None and self.read_context() == self.context
        except Exception:
            self.reason = '跟随上下文不可用，保持停止'
            return False

    def _ready(self):
        # Never accept a frontend checkbox as arrival evidence. Only the
        # host-owned startup policy, using measured feedback, opens this gate.
        return (self.startup is not None and self.startup.phase == 'ready'
                and self.startup.context == self.context and self.workspace is not None
                and self.startup.center == self.workspace.get('tcp_center',self.workspace['center']))

    def submit(self, *, token, session, seq, frame_seq, captured_at, u, v, opening):
        if token != self.token or not self.workspace or not self._same() or not self._ready():
            return False
        now = self.clock()
        if (session != self.context['camera_session'] or type(seq) is not int
                or type(frame_seq) is not int or not self.seq < seq <= 2**53-1
                or not self.frame_seq < frame_seq <= 2**53-1
                or not vector([captured_at,u,v,opening],4)
                or not 0 <= now-captured_at < .3
                or not all(0 <= x <= 1 for x in (u,v,opening))
                or (self.latest and captured_at <= self.latest['captured_at'])):
            return False
        w = self.workspace
        x, y, z = (u-.5)*w['size'][0], (.5-opening)*w['size'][1], (.5-v)*w['size'][2]
        for _ in range(w['direction']//90):
            x,y = -y,x
        palm = [a+b for a,b in zip(w['center'],(x,y,z))]
        target=[a+b for a,b in zip(w.get('tcp_center',w['center']),(x,y,z))]
        self.latest = dict(seq=seq,frame_seq=frame_seq,captured_at=captured_at,target=target,palm=palm)
        self.seq, self.frame_seq = seq, frame_seq
        self.reason = ''
        return True

    async def publish_latest(self, measured_q):
        """Solve from real measured q; publish only a verified current result.

        The driver independently enforces fixed-period packets, acceleration,
        tracking error, feedback freshness and stop confirmation. This method
        never starts it and cannot turn read-only/configure into motion.
        """
        if self.worker is not None and not self.worker.done():
            return False
        if self.latest is None or not vector(measured_q,6) or not self._same() or not self._ready():
            return False
        observation, generation, context = self.latest, self.generation, dict(self.context)
        if observation['seq']<=self.published_seq:return False
        if not 0 <= self.clock()-observation['captured_at'] < .3:
            self.reason = '手掌观测已过期'
            return False
        profile = self.driver.profile
        # A private cache shell cannot modify the shared FK cache.
        model = copy(self.model)
        model._fk_key,model._fk_value = None,None
        start = list(measured_q)
        # The legacy position-only policy locks J6 after startup. The explicit
        # upright policy uses all six measured joints, never a zeroed feedback
        # substitute; its approved policy and pose validator are host-owned.
        if self.lock_j6 and abs(start[5]) > .05:
            self.reason = 'J6尚未到0度，不能开始手掌跟随'
            return False
        solve_start = list(start)
        if self.lock_j6:solve_start[5] = 0.
        lower, upper = list(profile.lower), list(profile.upper)
        if self.lock_j6 and not lower[5] <= 0. <= upper[5]:
            return False
        if self.lock_j6:lower[5] = upper[5] = 0.
        self.worker = asyncio.create_task(asyncio.to_thread(self.solve, model,solve_start,
            observation['target'],lower,upper,
            max_joint_step=8.,max_cartesian_step=24.,deadline=self.clock()+.025))
        own_worker = self.worker
        try:
            result = await asyncio.shield(own_worker)
        except asyncio.CancelledError:
            self.invalidate('输入求解已取消')
            raise
        except Exception:
            self.reason = '位置求解失败，未发布运动目标'
            return False
        finally:
            if own_worker.done() and self.worker is own_worker:
                self.worker = None
        if (generation != self.generation or self.context != context or not self._same() or not self._ready()
                or self.latest is not observation
                or not 0 <= self.clock()-observation['captured_at'] < .3):
            return False
        q = result.get('q') if isinstance(result,dict) else None
        if (not vector(q,6) or (self.lock_j6 and abs(q[5]) > 1e-9)
                or any(not lo <= x <= hi for x,lo,hi in zip(q,profile.lower,profile.upper))
                or max(abs(a-b) for a,b in zip(start,q)) > 8.+1e-9):
            self.reason = '没有合法的连续小步'
            return False
        before,after = model.pose_vec(start),model.pose_vec(q)
        w = self.workspace
        if (not vector(before,6) or not vector(after,6)
                or math.dist(before[:3],after[:3]) > 24.+1e-6
                or (math.dist(after[:3],observation['target']) > POSITION_TOLERANCE_MM
                    and math.dist(after[:3],observation['target']) >= math.dist(before[:3],observation['target'])-1e-6)
                or any(not lo <= x <= hi for x,lo,hi in zip(after[:3],w['low'],w['high']))):
            self.reason = '下一步未改善末端位置或超出工作框'
            return False
        try:
            valid = self.validate_path(start,list(q)) is True
        except Exception:
            valid = False
        # Re-check after an arbitrarily slow validator; no stale target can
        # borrow a new clock or enter the commissioned driver's mailbox.
        if (not valid or generation != self.generation or not self._same() or not self._ready()
                or self.latest is not observation
                or not 0 <= self.clock()-observation['captured_at'] < .3):
            return False
        accepted=self.driver.publish(list(q),token=self.driver.owner,seq=observation['seq'],
                                     captured_at=observation['captured_at']) is True
        if accepted:self.published_seq=observation['seq']
        return accepted
