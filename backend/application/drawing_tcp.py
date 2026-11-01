"""Drawing-only action point over the existing tool geometry and verified adapter."""
import copy
import math
from ..core.kinematics import RobotModel,TCP_OFFSET_Z_MM,rpy_to_mat
from ..core.real_follow_tool_binding import build_real_follow_model
from ..core.planning import digest

def tool_key(world):
    base=world.motion_model()
    if world.settings.get('mode')=='real':
        mounted=world.tools.mounted;profile=world.tools.get(mounted.get('tool_id'))
        return digest(['real',mounted,profile,world._cfg_rev,base.variant])
    return digest(['sim',base.variant,base.tcp_translation_mm,base.tcp_rpy_deg,
                   getattr(base,'reference_tool',None),getattr(base,'library_tool',None)])

def build_tool(world):
    base=world.motion_model();key=tool_key(world)
    offset=[0.,0.,0.];label='当前工具TCP';kind='tool_tcp'
    if world.settings.get('mode')=='real':
        mounted=copy.deepcopy(world.tools.mounted);profile=world.tools.get(mounted.get('tool_id'))
        result=build_real_follow_model(mounted,profile,config_revision=world._cfg_rev,variant=base.variant,
                                      model_factory=RobotModel,flange_offset_z_mm=TCP_OFFSET_Z_MM)
        if not result['ok']:
            names={'identity':'安装身份','mount':'安装变换','tcp':'TCP','payload':'负载','geometry':'实际包络'}
            missing=list(dict.fromkeys(names.get(b['layer'],'档案') for b in result['blockers']))
            raise ValueError('当前真实工具验证不完整：'+'、'.join(missing)+'；请完成已有工具验证后再绘制')
        model=result['model'];label='已验证工具TCP';kind='verified_tool_tcp'
        if profile.get('type')=='pen':
            label='已验证笔尖TCP';kind='verified_pen_tip'
    else:
        reference=getattr(base,'reference_tool',None)
        if reference:
            d=reference['dimensions'];local=[-(d['length']/2-d['edge']),0,d['mountHeight']+d['thickness']+d['cupHeight']]
            rotation=rpy_to_mat(*map(math.radians,reference['rotation']))
            translation=[sum(rotation[i][j]*local[j] for j in range(3)) for i in range(3)]
            translation[2]-=TCP_OFFSET_Z_MM
            model=RobotModel(base.variant,tcp_translation_mm=translation,tcp_rpy_deg=list(base.tcp_rpy_deg))
            model.reference_tool=copy.deepcopy(reference)
            # World's existing TCP is the plate centre. This delta is in that
            # SAME TCP's axes, not an additional flange/world-space offset.
            offset=[local[0],0,d['thickness']/2+d['cupHeight']]
            kind='left_cup_tip';label='左吸盘作用点'
        else:
            model=copy.copy(base);model._fk_key=None;model._fk_value=None
            if getattr(base,'pen_tool',False):kind='pen_tip';label='笔尖'
    return model,{'binding_id':key,'kind':kind,'label':label,'scope':world.settings.get('mode'),
                  'offset_in_snapshot_tcp_mm':offset,'rpy_delta_deg':[0.,0.,0.]}
