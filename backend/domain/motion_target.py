"""Immutable endpoint identity and conservative, same-frame arrival evidence.

Tolerances are software endpoint checks, never a claim of calibrated accuracy.
"""
from dataclasses import dataclass, asdict
import math
import secrets
from ..core.kinematics import pose_rotation_distance


def vector(value):
    if not isinstance(value,(list,tuple)) or len(value)!=6:
        raise ValueError('目标需要六个有限数值')
    result=tuple(float(v) for v in value)
    if not all(math.isfinite(v) for v in result):raise ValueError('目标需要六个有限数值')
    return result


def axes(value):
    if value is None:return None
    if (not isinstance(value,(list,tuple)) or not value
            or any(type(i) is not int or not 0<=i<=5 for i in value)
            or len(set(value))!=len(value)):
        raise ValueError('限定轴必须为非空、唯一的0..5整数列表')
    return tuple(value)


@dataclass(frozen=True)
class MotionTarget:
    plan_id: str
    requested: tuple
    achieved: tuple
    joints: tuple
    start: tuple
    context: str
    session: str
    connection: int | None
    frame_seq: int
    created_at: float
    method: str
    start_captured_at: float | None = None
    allowed_axes: tuple | None = None

    @classmethod
    def create(cls,**fields):
        for name in ('requested','achieved','joints','start'):fields[name]=vector(fields[name])
        fields['allowed_axes']=axes(fields.get('allowed_axes'))
        return cls(plan_id=secrets.token_urlsafe(18),**fields)

    def snapshot(self):
        result=asdict(self)
        for name in ('requested','achieved','joints','start'):result[name]=list(result[name])
        if self.allowed_axes is not None:result['allowed_axes']=list(self.allowed_axes)
        return result


class ArrivalEvidence:
    def __init__(self,target,*,hold_s=.5,joint_tol_deg=.3,tcp_tol_mm=1.,rotation_tol_deg=.5):
        self.target=target
        self.hold_s=max(.5,float(hold_s))
        self.joint_tol_deg=joint_tol_deg
        self.tcp_tol_mm=tcp_tol_mm
        self.rotation_tol_deg=rotation_tol_deg
        self.last_seq=target.frame_seq
        self.last_captured_at=target.created_at
        self.since=None
        self.result={'plan_id':target.plan_id,'reached':False,'status':'unknown'}

    def observe(self,*,connection,frame_seq,captured_at,now,joints,pose,settled,fresh):
        age=now-captured_at if isinstance(captured_at,(int,float)) else None
        result={'plan_id':self.target.plan_id,'reached':False,'status':'unknown',
                'frame_seq':frame_seq,'connection':connection,'feedback_age_s':age,
                'joint_error_deg':None,'tcp_position_error_mm':None,'tcp_rotation_error_deg':None}
        # A repeated frame cannot accumulate hold time. An unknown/stale frame
        # also revokes a previous completion rather than extending its lifetime.
        if (not fresh or connection!=self.target.connection or type(frame_seq) is not int
                or frame_seq<self.last_seq or age is None or not 0<=age<=.3
                or captured_at<=self.target.created_at):
            self.since=None;self.result=result;return result
        if frame_seq==self.last_seq:
            self.result={**self.result,'feedback_age_s':age}
            return self.result
        if captured_at<=self.last_captured_at:
            self.since=None;self.result=result;return result
        if captured_at-self.last_captured_at>.3:self.since=None
        self.last_seq=frame_seq
        self.last_captured_at=captured_at
        try:q=vector(joints)
        except (ValueError,TypeError,OverflowError):
            self.since=None;self.result=result;return result
        result['joint_error_deg']=max(abs(a-b) for a,b in zip(q,self.target.joints))
        try:p=vector(pose)
        except (ValueError,TypeError,OverflowError):
            self.since=None;self.result=result;return result
        result['tcp_position_error_mm']=math.dist(p[:3],self.target.achieved[:3])
        result['tcp_rotation_error_deg']=pose_rotation_distance(p,self.target.achieved)
        aligned=(result['joint_error_deg']<=self.joint_tol_deg
            and result['tcp_position_error_mm']<=self.tcp_tol_mm
            and result['tcp_rotation_error_deg']<=self.rotation_tol_deg)
        if settled is True and aligned:
            if self.since is None:self.since=captured_at
            result['reached']=captured_at-self.since>=self.hold_s
            result['status']='reached' if result['reached'] else 'settling'
        else:self.since=None;result['status']='outside' if not aligned else 'moving'
        self.result=result
        return result
