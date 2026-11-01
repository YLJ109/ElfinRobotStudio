"""Board tool selection and grasp geometry; simulation never qualifies hardware."""
from __future__ import annotations

import math
import copy
import time
from functools import lru_cache

import numpy as np

from ..core import sim_tool, tool_library
from ..core.kinematics import rpy_to_mat
from ..core.objects import half_height

GRIPPER_ID = 'robotiq-2f85'
GRIPPER_GAMES = ('chess', 'xiangqi')


def tool_id_for(game):
    if game not in ('ttt', 'gomoku', 'go', *GRIPPER_GAMES):
        raise ValueError('未知棋种')
    return GRIPPER_ID if game in GRIPPER_GAMES else 'vacuum-reference'


def ensure_board_tool(world, game):
    """Use the same mounted descriptor, motion model and action contract as UI."""
    if world.settings.get('mode') != 'sim' or world.real is not None:
        raise ValueError('真机棋类尚需工具、TCP及工位标定，未发送动作')
    if tool_id_for(game) == GRIPPER_ID:
        result = world.set_sim_tool({'kind': 'library', 'id': GRIPPER_ID, 'rotation': [0, 0, 0]})
    else:
        result = world.ensure_sim_task_tool('chess')
        if result.get('ok'):
            descriptor = sim_tool.normalize(world.settings.get('sim_tool', sim_tool.DEFAULT_TOOL))
            if descriptor and descriptor['kind'] == 'vacuum-reference' and descriptor['dimensions'] == sim_tool.DEFAULT_TOOL['dimensions']:
                # Match the existing photographed reference in visualAssets.js:
                # 40mm connector and 30mm cups. The old backend fallback had
                # 0/50, bringing the real wrist envelope into a dense tray.
                # Apply only this unchanged legacy default, never custom sizes.
                descriptor['dimensions'].update(mountHeight=40., cupHeight=30.)
                result = world.set_sim_tool(descriptor)
    if not result.get('ok'):
        raise ValueError(result.get('why') or '棋类工具切换失败')
    return result


@lru_cache(maxsize=128)
def _pad_geometry(closure):
    """FK of the checked-in URDF contact pads, in mm; no fabricated two fingers."""
    entry = tool_library.get(GRIPPER_ID)
    from ..core.tool_contact_geometry import rig_matrices
    matrices = rig_matrices(entry['animation'],closure)
    left = matrices['left_inner_finger_pad'][:3, 3] * 1000
    right = matrices['right_inner_finger_pad'][:3, 3] * 1000
    # Actual source pad thickness is 6.35 mm and its axial length is 37.5 mm.
    return float(np.linalg.norm(left-right)-6.35), float((left[2]+right[2])/2 + 18.75)


def grasp_geometry(descriptor, piece, *, cup_side='right'):
    """Return contact offset, height and closure in the *mounted TCP* frame."""
    if descriptor.get('kind') == 'vacuum-reference':
        d = descriptor['dimensions']
        return {'offset': [(-1 if cup_side == 'left' else 1)*(d['length']-2*d['edge'])/2, 0., d['thickness']/2+d['cupHeight']],
                'height': half_height(piece), 'closure': None}
    if descriptor.get('kind') != 'library' or descriptor.get('id') != GRIPPER_ID or descriptor['rotation'] != [0, 0, 0]:
        raise ValueError('棋类夹取需要正确安装的 Robotiq 2F-85')
    width = piece['size'][0]*2 if piece['kind'] in ('cylinder', 'sphere') else max(piece['size'][:2])
    if not 1 <= width <= _pad_geometry(0.)[0]:
        raise ValueError('棋子宽度超出 Robotiq 2F-85 开口')
    def closure_for(opening):
        low, high = 0., 1.
        for _ in range(20):
            mid = (low+high)/2
            if _pad_geometry(mid)[0] > opening: low = mid
            else: high = mid
        return (low+high)/2
    closure = closure_for(width)
    open_closure = closure_for(min(width+3.,_pad_geometry(0.)[0]))
    _, lower_tip = _pad_geometry(closure)
    tcp = tool_library.get(GRIPPER_ID)['tcp_mm']
    # Grip the upper edge. The actual pad's bottom stays above the support
    # surface even for thin xiangqi discs; do not plunge its centre into a tray.
    depth = min(6., half_height(piece))
    return {'offset': [0., 0., lower_tip-tcp[2]],
            'height': half_height(piece)-depth, 'closure': closure,'open_closure':open_closure}


def contact_pose(descriptor, piece, xyz=None, *, cup_side='right', yaw=0.):
    if type(yaw) not in (int,float) or not math.isfinite(yaw) or not 0<=yaw<=360:
        raise ValueError('棋类吸盘朝向无效')
    geometry = grasp_geometry(descriptor, piece, cup_side=cup_side)
    # Board execution pre-opens only 3 mm beyond the measured proxy width;
    # a full 85 mm opening would strike neighbouring pieces in a dense rank.
    rotation = rpy_to_mat(math.pi, 0., math.radians(yaw))
    shift = [sum(rotation[i][j]*geometry['offset'][j] for j in range(3)) for i in range(3)]
    xyz = piece['xyz'] if xyz is None else xyz
    return [xyz[i]-shift[i]+(geometry['height'] if i == 2 else 0.) for i in range(3)] + [180., 0., yaw]


def wait_pose(world, piece, clearance=50):
    descriptor = sim_tool.normalize(world.settings.get('sim_tool', sim_tool.DEFAULT_TOOL))
    pose = contact_pose(descriptor, piece, cup_side='left')
    pose[2] += clearance
    return pose


def probe_vacuum_yaw(world, piece, place, yaw, clearance=70., *, cancelled=lambda:False, budget_s=2., deadline=None):
    """Bounded endpoint IK + full-link contact check, with zero device effects.

    This only ranks a finite board grasp candidate. The chosen candidate must
    still use RobotPlay's exact complete-path preparation and execution gate.
    """
    if world.settings.get('mode')!='sim' or world.real is not None:
        return {'ok':False,'why':'棋类朝向检查仅用于仿真'}
    from ..core.planning import inspect_path
    descriptor=sim_tool.normalize(world.settings.get('sim_tool',sim_tool.DEFAULT_TOOL))
    if not descriptor or descriptor.get('kind')!='vacuum-reference':
        return {'ok':False,'why':'当前棋类工具不是双吸盘'}
    model=copy.copy(world.motion_model())
    lower,upper=world.limits()
    settings=copy.deepcopy(world.settings)
    objects=copy.deepcopy(world.objects.list())
    pick=contact_pose(descriptor,piece,cup_side='left',yaw=yaw)
    destination=contact_pose(descriptor,piece,place,cup_side='left',yaw=yaw)
    pick_up=pick[:];pick_up[2]+=clearance
    place_up=destination[:];place_up[2]+=clearance
    deadline=min(deadline if deadline is not None else float('inf'),time.monotonic()+max(.1,min(5.,budget_s)))
    reason='当前朝向未找到可达取放姿态'
    def checkpoint():
        if cancelled():raise RuntimeError('棋局已终止')
        if time.monotonic()>=deadline:raise RuntimeError('当前朝向逆解预算耗尽')
    # Match the existing RobotPlay branch candidates; an elbow-down seed alone
    # can touch a dense tray although the elbow-up branch is collision-free.
    for seed in (list(world.current_joints()), [0,-45,90,0,45,0],
                 [0,-70,-90,0,100,0], [0,45,-90,0,90,0]):
        q=seed
        for index,pose in enumerate((pick_up,pick,place_up,destination)):
            if cancelled():return {'ok':False,'why':'棋局已终止'}
            if time.monotonic()>=deadline:return {'ok':False,'why':'当前朝向逆解预算耗尽'}
            q,_,_,ok=model.ik(q,pose,lower,upper,iters=140,max_restarts=1,deadline=deadline,cancelled=cancelled)
            if not ok:break
            at_endpoint=[{**obj,'xyz':list(place)} if index>=2 and obj['id']==piece['id'] else obj for obj in objects]
            try:report=inspect_path(model,q,q,settings,lower,upper,at_endpoint,contact_ids={piece['id']},check=checkpoint)
            except RuntimeError as exc:return {'ok':False,'why':str(exc)}
            if report['risk_detected']:
                issues=report.get('issues') or [{}]
                hits=issues[0].get('collisions') or []
                reason=f'端位{index}朝向{yaw}会碰撞'+('/'.join(
                    str(hit.get('object') or hit.get('type'))+'@'+str(hit.get('link'))+
                    '('+str(hit.get('gap_mm'))+'mm)' for hit in hits[:2]) or '场景')
                break
        else:return {'ok':True,'yaw':float(yaw)}
    return {'ok':False,'why':reason}


def probe_gripper_yaw(world, piece, place, yaw, clearance=70., *, cancelled=lambda:False, budget_s=2., deadline=None):
    """Check the formal gripper at both contact poses and opening states.

    No motion, state writes, geometry shrinkage or neighbour exemptions. The
    selected candidate still undergoes the unique full RobotPlay path check.
    """
    if world.settings.get('mode')!='sim' or world.real is not None:
        return {'ok':False,'why':'棋类夹爪朝向检查仅用于仿真'}
    from ..core.planning import inspect_path
    descriptor=sim_tool.normalize(world.settings.get('sim_tool',sim_tool.DEFAULT_TOOL))
    if not descriptor or descriptor.get('id')!=GRIPPER_ID:
        return {'ok':False,'why':'当前棋类工具不是 Robotiq 2F-85'}
    model=copy.copy(world.motion_model());model._fk_key=model._fk_value=None
    lower,upper=world.limits()
    settings=copy.deepcopy(world.settings);objects=copy.deepcopy(world.objects.list())
    grip=grasp_geometry(descriptor,piece)
    pick=contact_pose(descriptor,piece,yaw=yaw)
    destination=contact_pose(descriptor,piece,place,yaw=yaw)
    pick_up=pick[:];pick_up[2]+=clearance
    place_up=destination[:];place_up[2]+=clearance
    ends=((pick_up,False,False),(pick,False,False),(pick,True,False),
          (place_up,True,True),(destination,True,True),(destination,False,True))
    deadline=min(deadline if deadline is not None else float('inf'),time.monotonic()+max(.1,min(5.,budget_s)))
    reason='当前夹爪朝向未找到可达取放姿态'
    def checkpoint():
        if cancelled():raise RuntimeError('棋局已终止')
        if time.monotonic()>=deadline:raise RuntimeError('当前夹爪朝向逆解预算耗尽')
    for seed in (list(world.current_joints()),[180.,-45.,90.,0.,45.,0.],
                 [180.,-70.,-90.,0.,100.,0.],[180.,45.,-90.,0.,90.,0.]):
        q=seed
        for index,(pose,closed,placed) in enumerate(ends):
            try:checkpoint()
            except RuntimeError as exc:return {'ok':False,'why':str(exc)}
            model.library_tool={**model.library_tool,'closure':grip['closure' if closed else 'open_closure']}
            q,_,_,ok=model.ik(q,pose,lower,upper,iters=140,max_restarts=1,deadline=deadline,cancelled=cancelled)
            if not ok:break
            at_endpoint=[{**obj,'xyz':list(place)} if placed and obj['id']==piece['id'] else obj for obj in objects]
            try:report=inspect_path(model,q,q,settings,lower,upper,at_endpoint,contact_ids={piece['id']},check=checkpoint)
            except RuntimeError as exc:return {'ok':False,'why':str(exc)}
            if report['risk_detected']:
                hits=(report.get('issues') or [{}])[0].get('collisions') or []
                reason=f'夹爪端位{index}朝向{yaw}会碰撞'+('/'.join(
                    str(hit.get('object') or hit.get('type'))+'@'+str(hit.get('link')) for hit in hits[:2]) or '场景')
                break
        else:return {'ok':True,'yaw':float(yaw)}
    return {'ok':False,'why':reason}
