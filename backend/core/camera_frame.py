"""Camera optical +Z and upper mount -X are expressed in the physical flange."""
import math
import time
from .kinematics import rpy_to_mat,mat_to_xyzrpy,_mat_mul

# Shared acceptance contract for simulated and qualified real follow publishers.
CAMERA_FACING_TOL_DEG = .5
CAMERA_FACING_MIN_DOT = math.cos(math.radians(CAMERA_FACING_TOL_DEG))


def camera_axes(model,q):
    flange=model.fk(list(q))[model.flange]
    return ([flange[i][2] for i in range(3)],[-flange[i][0] for i in range(3)])


def camera_front_rpy(model,direction):
    length=math.sqrt(sum(v*v for v in direction))
    if length<1e-9:raise ValueError('相机前向无效')
    z=[v/length for v in direction]
    # Camera mount offset is -flangeX. Project world-up onto its image plane.
    up=[-z[i]*z[2]+(1. if i==2 else 0.) for i in range(3)]
    norm=math.sqrt(sum(v*v for v in up))
    if norm<1e-9:up=[1.,0.,0.];norm=1.
    x=[-v/norm for v in up]
    y=[z[1]*x[2]-z[2]*x[1],z[2]*x[0]-z[0]*x[2],z[0]*x[1]-z[1]*x[0]]
    flange=[[x[i],y[i],z[i],0.] for i in range(3)]+[[0.,0.,0.,1.]]
    # pose_vec reports the mounted TCP, not the raw physical flange.
    tcp=_mat_mul(flange,rpy_to_mat(*map(math.radians,getattr(model,'tcp_rpy_deg',[0,0,0]))))
    _,rpy=mat_to_xyzrpy(tcp)
    return [math.degrees(v) for v in rpy]


def solve_camera_forward_step(model,q,target,lower,upper,forward,*,max_joint_step=8.,max_cartesian_step=24.,deadline=None,position_tol=.02,cancelled=lambda:False):
    """Bounded translation with a fixed upright physical camera orientation.

    All six joints may participate within their actual limits. Holding J6 at
    zero while translating overconstrains the task and lets the camera roll.
    """
    import numpy as np
    from copy import copy
    invalid=dict(q=None,position_error_mm=None,progress_mm=0.,cause='invalid_input',iterations=0)
    vectors=((q,6),(target,3),(lower,6),(upper,6),(forward,3))
    try:
        if any(len(value)!=size or any(isinstance(v,bool) or not math.isfinite(float(v)) for v in value) for value,size in vectors):return invalid
        if (any(isinstance(v,bool) or not math.isfinite(float(v)) or v<=0 for v in (max_joint_step,max_cartesian_step,position_tol))
                or any(a>b or not a<=v<=b for a,v,b in zip(lower,q,upper))):return invalid
    except (TypeError,ValueError,OverflowError):return invalid
    norm=math.sqrt(sum(v*v for v in forward))
    if norm<1e-9:return invalid
    forward=[v/norm for v in forward]
    if cancelled() or (deadline is not None and time.monotonic()>=deadline):return {**invalid,'cause':'cancelled' if cancelled() else 'deadline'}
    work=copy(model);work._fk_key=work._fk_value=None
    origin=np.asarray(work.pose_vec(q)[:3]);start=np.asarray(q,dtype=float)
    target=np.asarray(target,dtype=float);remaining=float(np.linalg.norm(target-origin))
    goal=origin+(target-origin)*min(1.,max_cartesian_step*.8/max(1e-12,remaining))
    low=np.maximum(np.asarray(lower),start-max_joint_step)
    high=np.minimum(np.asarray(upper),start+max_joint_step)
    desired_up=np.asarray([-forward[i]*forward[2]+(1. if i==2 else 0.) for i in range(3)])
    upnorm=float(np.linalg.norm(desired_up))
    if upnorm<1e-9:return invalid
    desired_up/=upnorm
    current=start.copy();desired=np.asarray(forward);iterations=0
    for iterations in range(24):
        if cancelled() or (deadline is not None and time.monotonic()>=deadline):break
        point=np.asarray(work.pose_vec(current.tolist())[:3]);axis,up=camera_axes(work,current.tolist())
        axis=np.asarray(axis)
        error=np.concatenate((goal-point,(desired-axis)*10000.,(desired_up-np.asarray(up))*10000.))
        if np.linalg.norm(error[:3])<=position_tol and np.linalg.norm(error[3:])<=.002:break
        jac=np.asarray(work.jacobian(current.tolist()))
        active=[i for i in range(6) if high[i]>low[i]+1e-10]
        if not active:break
        direction=np.asarray([np.cross(jac[3:,i]*math.pi/180.,axis) for i in active]).T*10000.
        upright=np.asarray([np.cross(jac[3:,i]*math.pi/180.,np.asarray(up)) for i in active]).T*10000.
        matrix=np.vstack((jac[:3,active],direction,upright))
        delta=np.linalg.lstsq(np.vstack((matrix,np.eye(len(active))*.03)),
                             np.concatenate((error,np.zeros(len(active)))),rcond=None)[0]
        delta*=min(1.,3./max(1e-12,float(np.max(np.abs(delta)))))
        cost=float(np.linalg.norm(error));accepted=False
        for scale in (1.,.5,.25,.125,.0625):
            candidate=current.copy();candidate[active]=np.clip(candidate[active]+delta*scale,low[active],high[active])
            point=np.asarray(work.pose_vec(candidate.tolist())[:3]);axis,up=camera_axes(work,candidate.tolist())
            residual=np.concatenate((goal-point,(desired-np.asarray(axis))*10000.,(desired_up-np.asarray(up))*10000.))
            if (float(np.linalg.norm(point-origin))<=max_cartesian_step+1e-8 and up[2]>=-.001
                    and float(np.linalg.norm(residual))<cost-1e-9):
                current=candidate;accepted=True;break
        if not accepted:break
    end=float(np.linalg.norm(np.asarray(work.pose_vec(current.tolist())[:3])-target))
    if cancelled() or (deadline is not None and time.monotonic()>=deadline):
        return dict(q=None,position_error_mm=end,progress_mm=0.,cause='cancelled' if cancelled() else 'deadline',iterations=iterations+1)
    axis,up=camera_axes(work,current.tolist())
    alignment=sum(a*b for a,b in zip(axis,forward))
    upper_alignment=float(np.dot(desired_up,np.asarray(up)))
    valid=(alignment>=CAMERA_FACING_MIN_DOT and upper_alignment>=CAMERA_FACING_MIN_DOT
           and (end<remaining-1e-8 or remaining<=.05))
    return dict(q=current.tolist() if valid else None,position_error_mm=end,progress_mm=remaining-end,
                cause='reached' if end<=.05 else 'camera_forward_progress' if valid else 'camera_forward_limit',iterations=iterations+1,
                camera_alignment=alignment,camera_upper_alignment=upper_alignment)


def solve_camera_forward_center(model,q,target,lower,upper,forward,*,deadline=None,cancelled=lambda:False):
    """Detached startup endpoint only; caller must validate/execute the whole path."""
    from copy import copy
    failure=dict(q=None,position_error_mm=None,progress_mm=0.,cause='camera_center_unreachable',iterations=0)
    if cancelled() or (deadline is not None and time.monotonic()>=deadline):return {**failure,'cause':'cancelled' if cancelled() else 'deadline'}
    work=copy(model);work._fk_key=work._fk_value=None
    pose=list(target)+camera_front_rpy(work,forward)
    candidate,ep,er,ok=work.ik(list(q),pose,list(lower),list(upper),iters=160,max_restarts=2,
        tol_mm=.05,tol_deg=.2,deadline=deadline,cancelled=cancelled)
    if cancelled() or (deadline is not None and time.monotonic()>=deadline):return {**failure,'cause':'cancelled' if cancelled() else 'deadline'}
    axis,up=camera_axes(work,candidate)
    norm=math.sqrt(sum(v*v for v in forward))
    alignment=sum(a*b for a,b in zip(axis,forward))/norm if norm>1e-9 else -1.
    if (ok and ep<=.05 and alignment>=CAMERA_FACING_MIN_DOT and up[2]>=CAMERA_FACING_MIN_DOT
            and all(math.isfinite(v) and a<=v<=b for a,v,b in zip(lower,candidate,upper))):
        return dict(q=list(candidate),position_error_mm=ep,progress_mm=math.dist(work.pose_vec(list(q))[:3],target)-ep,
                    cause='reached',iterations=160,camera_alignment=alignment,camera_upper_z=up[2])
    return {**failure,'position_error_mm':ep}

