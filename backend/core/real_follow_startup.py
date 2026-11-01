"""Pure centre preparation and measured-arrival policy.

No transport or motion exists here. The host must execute an accepted goal via
its qualified finite trajectory executor (speed/acceleration/stop ownership),
then feed actual feedback. A checked geometric chord is NOT a timed trajectory.
This module cannot qualify a tool, start Servo, enable or connect a robot.
"""
from copy import copy, deepcopy
import math
import secrets
import time

from backend.core.position_servo import solve_position_step
from .real_palm_pipeline import vector


class RealFollowStartup:
    def __init__(self, model, read_context, validate_segment, *, clock=time.monotonic,
                 solve=solve_position_step):
        self.model, self.read_context = model, read_context
        self.validate_segment, self.clock, self.solve = validate_segment, clock, solve
        self.cancel('尚未准备')

    def cancel(self, reason='已取消'):
        self.token = secrets.token_hex(16)
        self.phase, self.reason = 'blocked', reason
        self.goal = self.context = self.center = None
        self.start_q = None
        self.last_seq = -1
        self.arrivals = 0

    def _fresh(self, feedback, *, require_motion_permission=True):
        return (isinstance(feedback,dict) and vector(feedback.get('q'),6)
                and vector([feedback.get('stamp')],1)
                and type(feedback.get('frame_seq')) is int
                and 0 <= self.clock()-feedback['stamp'] < .15
                and (not require_motion_permission or
                     (feedback.get('enabled') is True
                      and feedback.get('safety_ok') is True
                      and feedback.get('motion_allowed') is True)))

    def prepare(self, feedback, center, lower, upper):
        """Find centre with J6=0; check the FULL measured-to-goal chord.

        Limits and tool-bound model come from the trusted host, not a browser.
        Nothing is dispatched. Any failure invalidates the previous plan.
        """
        self.cancel('中心准备未通过')
        try:
            context = deepcopy(self.read_context())
            if (not context or not self._fresh(feedback) or feedback.get('idle') is not True
                    or feedback.get('connection') != context.get('connection')
                    or not vector(center,3) or not vector(lower,6) or not vector(upper,6)
                    or any(a>b for a,b in zip(lower,upper))
                    or not lower[5] <= 0. <= upper[5]
                    or any(not a<=q<=b for q,a,b in zip(feedback['q'],lower,upper))):
                return None
            start = list(feedback['q'])
            work = copy(self.model)
            work._fk_key = work._fk_value = None
            q = list(start)
            q[5] = 0.  # Solver seed only; NEVER sent as a wrist-only command.
            lo,hi = list(lower),list(upper)
            lo[5] = hi[5] = 0.
            deadline = self.clock()+.4
            for _ in range(80):
                pose = work.pose_vec(q)
                if not vector(pose,6):return None
                if math.dist(pose[:3],center) <= 1.:break
                if self.clock() >= deadline:return None
                result = self.solve(work,q,center,lo,hi,max_joint_step=8.,
                                    max_cartesian_step=24.,deadline=deadline)
                nxt = result.get('q') if isinstance(result,dict) else None
                if (not vector(nxt,6) or abs(nxt[5])>1e-9
                        or any(not a<=v<=b for v,a,b in zip(nxt,lo,hi))
                        or math.dist(work.pose_vec(nxt)[:3],center) >= math.dist(pose[:3],center)-1e-6):
                    return None
                q=list(nxt)
            else:return None
            # Do not wrap 180deg to zero. Each checked interval is <=.25deg,
            # including J6; host still owns its timed collision-safe execution.
            steps=max(1,math.ceil(max(abs(a-b) for a,b in zip(start,q))/.25))
            previous=list(start)
            path_deadline=self.clock()+5.
            for i in range(1,steps+1):
                if self.clock()>=path_deadline:return None
                point=[a+(b-a)*i/steps for a,b in zip(start,q)]
                if self.validate_segment(previous,point) is not True:return None
                previous=point
            if self.read_context()!=context:return None
            self.goal,self.center,self.context=list(q),list(center),context
            self.start_q=list(start)
            self.phase,self.reason='planned','等待受控执行到中心，J6为0度'
            return dict(token=self.token,q=list(q),center=list(center),context=context)
        except Exception:
            self.cancel('中心求解或路径检查失败')
            return None

    def mark_dispatched(self, token, feedback):
        """Host call AFTER finite execution acceptance; an ACK is not arrival."""
        try:
            valid=(token==self.token and self.phase=='planned' and self._fresh(feedback)
                   and feedback.get('connection')==self.context.get('connection')
                   and vector(self.start_q,6)
                   and max(abs(a-b) for a,b in zip(feedback['q'],self.start_q))<=.05
                   and self.read_context()==self.context)
        except Exception:valid=False
        if not valid:
            self.cancel('中心执行上下文已失效')
            return False
        self.phase='centering'
        self.last_seq,self.dispatched_at=feedback['frame_seq'],self.clock()
        self.last_stamp=feedback['stamp']
        return True

    def observe_arrival(self, token, feedback):
        """Three distinct fresh post-dispatch frames, not target/ACK echoes."""
        try:
            if (token!=self.token or self.phase not in ('centering','ready')
                    or not self._fresh(feedback) or self.read_context()!=self.context):
                self.cancel('到位确认失效，需重新启动')
                return False
            if feedback.get('connection')!=self.context.get('connection'):
                self.cancel('反馈连接已变化')
                return False
            if feedback['frame_seq']<=self.last_seq or feedback['stamp']<=self.last_stamp:
                return False
            self.last_seq,self.last_stamp=feedback['frame_seq'],feedback['stamp']
            if feedback['stamp']<=self.dispatched_at:return False
            q=feedback['q']
            at_goal=(feedback.get('idle') is True and abs(q[5])<=.05
                     and max(abs(a-b) for a,b in zip(q,self.goal))<=.1
                     and math.dist(self.model.pose_vec(q)[:3],self.center)<=1.)
            self.arrivals=self.arrivals+1 if at_goal else 0
            if not at_goal and self.phase=='ready':
                self.cancel('中心保持已失效')
                return False
            if self.arrivals>=3:
                self.phase,self.reason='ready','中心已到位，J6为0度，可由主机启动跟随'
            return self.phase=='ready'
        except Exception:
            self.cancel('实际反馈或运动学不可用')
            return False
