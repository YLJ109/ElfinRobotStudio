"""Side addressing and electrical exclusivity; no IO or holding inference."""
from copy import deepcopy
from .tool_actions import TOOL_ACTION_DEFINITIONS

SIDES=('left','right')
SUCTION_ACTIONS=frozenset(row['id'] for row in TOOL_ACTION_DEFINITIONS if 'suction' in row['toolTypes'])


def side_for(side):
    # Historical single-cup programs use the left channel. New UI supplies side.
    if side is None: return 'left'
    if side not in SIDES: raise ValueError('请选择左侧或右侧吸盘')
    return side


def validate_mapping(value):
    if not isinstance(value,dict) or value.get('version') != 1 or set(value) != {'version','sides'}:
        raise ValueError('吸盘映射版本或结构无效')
    sides=value.get('sides')
    if not isinstance(sides,dict) or set(sides) != set(SIDES):
        raise ValueError('必须分别配置左右吸盘')
    aliases=[]
    for side in SIDES:
        item=sides[side]
        if not isinstance(item,dict) or set(item) != {'vacuum','blow'}:
            raise ValueError('每侧必须配置吸气和吹气通道')
        for kind in ('vacuum','blow'):
            channel=item[kind]
            if (not isinstance(channel,dict) or set(channel) != {'alias','on'}
                    or not isinstance(channel['alias'],str) or not channel['alias'].strip()
                    or len(channel['alias'])>64 or type(channel['on']) is not int or channel['on'] not in (0,1)):
                raise ValueError('每个通道必须明确输出别名和有效电平')
            aliases.append(channel['alias'])
    if len(set(aliases)) != 4:
        raise ValueError('左右吸气和吹气不能复用同一输出别名')
    return deepcopy(value)


def resolve_channels(mapping, io):
    value=validate_mapping(mapping)
    addresses=set()
    for item in value['sides'].values():
        for channel in item.values():
            address=io.get(channel['alias'])
            if (not isinstance(address,dict) or address.get('direction')!='output'
                    or address.get('group') not in ('end','box') or type(address.get('bit')) is not int
                    or not 0<=address['bit']<(3 if address['group']=='end' else 8)):
                raise ValueError('吸盘别名必须明确标定为有效输出通道')
            key=(address['group'],address['bit'])
            if key in addresses: raise ValueError('吸盘输出地址重叠，请核对接线映射')
            addresses.add(key)
            channel.update(group=address['group'],bit=address['bit'])
    return value['sides']


def read_state(channels, snapshot, fresh):
    result={}
    for side,item in channels.items():
        result[side]={'holding':None}
        for kind,channel in item.items():
            values=snapshot.get(channel['group']+'_do')
            raw=values[channel['bit']] if fresh and isinstance(values,(list,tuple)) and len(values)>channel['bit'] else None
            result[side][kind]=(raw==channel['on']) if type(raw) is int and raw in (0,1) else None
    return result


def transition(action, state):
    if action not in SUCTION_ACTIONS: raise ValueError('不支持的吸盘动作')
    kind=action.split('_')[0]
    enabled=action.endswith('_on')
    opposite='blow' if kind=='vacuum' else 'vacuum'
    if enabled and state.get(opposite) is not False:
        raise ValueError('请先确认同侧另一气路已关闭')
    return kind,enabled
