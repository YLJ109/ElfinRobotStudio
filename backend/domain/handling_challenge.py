"""Finite simulation courses and outcome rules; no motion implementation."""
import math

LEVELS=(
    {'id':1,'name':'基础搬运','pieces':1,'clearance':120,'obstacles':[]},
    {'id':2,'name':'单障碍绕行','pieces':2,'clearance':180,'obstacles':[
        {'xyz':[450,0,30],'size':[140,80,60]}]},
    {'id':3,'name':'双障碍协作','pieces':3,'clearance':220,'obstacles':[
        {'xyz':[350,0,45],'size':[80,160,90]}, {'xyz':[550,0,45],'size':[80,160,90]}]},
)


def level_by_id(value):
    if type(value) is not int or not 1<=value<=len(LEVELS):raise ValueError('请选择第1至3关')
    return LEVELS[value-1]


def at_goal(item,goal,group,slot):
    return bool(item and item.get('task',{}).get('group')==group
                and item['task'].get('slot')==slot
                and isinstance(item.get('xyz'),list) and len(item['xyz'])==3
                and all(type(v) in (int,float) and math.isfinite(v) for v in item['xyz'])
                and math.dist(item['xyz'],goal)<=.5)


def score(completed,total,successful):
    return 100 if successful and completed==total and total>0 else int(99*completed/max(total,1))
