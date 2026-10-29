"""场景玩法只事务式移动仿真道具；不导入或调用机器人运动接口。"""
from __future__ import annotations
import copy
import math
import uuid
import secrets
from fastapi import APIRouter, Body, HTTPException
from .core.objects import ObjectStore, half_height
from .core.storage import write_json
from .workspace_routes import Workspace
from .core.play_vision import piece_width

LINES=((0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6))
COLORS={'red':'#e4553f','green':'#40c979','blue':'#397ef2'}

def fail(message, status=422):
    raise HTTPException(status_code=status,detail=message)

def fields(value,allowed,required):
    if not isinstance(value,dict) or set(value)-set(allowed) or not set(required)<=set(value):
        fail('请求字段无效')

def outcome(board):
    for a,b,c in LINES:
        if board[a] and board[a]==board[b]==board[c]:return board[a]
    return 'draw' if all(board) else None

def computer_move(board):
    # 正确：人执O先手，电脑执X；搜索空间固定为九格，输入不作为代码执行。
    def score(cells,side):
        result=outcome(cells)
        if result:return 1 if result=='X' else -1 if result=='O' else 0
        values=[]
        for slot in range(9):
            if cells[slot]:continue
            cells[slot]=side;values.append(score(cells,'O' if side=='X' else 'X'));cells[slot]=None
        return (max if side=='X' else min)(values)
    best,best_score=None,-2
    for slot in (4,0,2,6,8,1,3,5,7):
        if board[slot]:continue
        cells=board[:];cells[slot]='X';value=score(cells,'O')
        if value>best_score:best,best_score=slot,value
    return best

def homes(count,zone):
    columns=max(1,math.ceil(math.sqrt(count)));pitch=min(90,260/max(1,columns-1))
    rows=math.ceil(count/columns)
    return [(zone['xyz'][0]+(index%columns-(columns-1)/2)*pitch,
             zone['xyz'][1]+(index//columns-(rows-1)/2)*pitch) for index in range(count)]

def at(piece,zone,x,y,slot):
    piece['xyz']=[x,y,zone['xyz'][2]+half_height(zone)+half_height(piece)]
    piece['task']['slot']=slot
    piece.update(gravity=False,floating=True,floor=True)

def board_position(zone,slot):
    return zone['xyz'][0]+(slot%3-1)*100,zone['xyz'][1]+(slot//3-1)*100

class PlayService:
    def __init__(self,world):
        self.world=world
        self.guard=Workspace(world)

    def transaction(self,action):
        self.guard.require_idle()
        store=self.world.objects
        with store._lock:
            candidate=copy.deepcopy(store.items)
            result=action(candidate)
            candidate=[ObjectStore._sanitize(item) for item in candidate]
            # 正确：先原子落盘再发布内存，失败不能留下半套场景/半个回合。
            write_json(store.path,candidate)
            store.items=candidate
            store._vel={key:value for key,value in store._vel.items() if key not in {item['id'] for item in candidate if item.get('task')}}
            return {'ok':True,**result,'objects':copy.deepcopy(candidate)}

    @staticmethod
    def group(items,group,mode=None):
        members=[item for item in items if item.get('task',{}).get('group')==group]
        if not isinstance(group,str) or len(group)!=32 or not members:fail('任务已变化，请重新布置',409)
        if len({item['task']['mode'] for item in members})!=1 or mode and members[0]['task']['mode']!=mode:
            fail('任务类型不匹配',409)
        zones={role:[item for item in members if item['task']['role']==role] for role in ('zone_a','zone_b')}
        if any(len(value)!=1 for value in zones.values()):fail('料区已缺失，请重新布置',409)
        pieces=sorted([item for item in members if item['task']['role']=='piece'],key=lambda item:item['task']['home'])
        if not pieces or len({item['task']['home'] for item in pieces})!=len(pieces):fail('任务工件不完整，请重新布置',409)
        for zone in (zones['zone_a'][0],zones['zone_b'][0]):
            if min(zone['size'][:2])<320:fail('料区尺寸不足，请恢复至至少320毫米',409)
        return pieces,zones['zone_a'][0],zones['zone_b'][0]

    def setup(self,payload):
        fields(payload,('mode','counts'),('mode',))
        mode=payload['mode']
        if mode not in ('chess','sorting'):fail('未知玩法')
        counts=payload.get('counts',{'red':3,'green':3,'blue':3})
        if (not isinstance(counts,dict) or set(counts)!=set(COLORS)
                or any(type(value) is not int or not 0<=value<=9 for value in counts.values())
                or not 1<=sum(counts.values())<=27):fail('红绿蓝分别需要0～9个，总数1～27个')
        group=uuid.uuid4().hex
        def create(items):
            # 仅替换本功能带明确任务标记的物体，普通用户道具保留。
            items[:]=[item for item in items if not item.get('task')]
            if len(items)>450:fail('场景过大，请先减少物体')
            def item(role,preset,size,color,xyz,symbol='',home=0):
                value={'id':uuid.uuid4().hex,'name':{'zone_a':'A 原料区','zone_b':'B 放料区'}.get(role,f'{symbol or "工件"}{home+1}'),
                    'kind':'box','preset':preset,'size':size,'color':color,'surface':'plastic','xyz':xyz,
                    'gravity':False,'floating':True,'floor':True,'collide':role=='piece',
                    'task':{'group':group,'mode':mode,'role':role,'symbol':symbol,'home':home,'slot':-1}}
                items.append(value);return value
            a=item('zone_a','zone_a',[330,330,4],'#397e9b',[450,-350,2])
            item('zone_b','zone_b',[330,330,4],'#4b8d77',[450,350,2])
            tokens=['O']*5+['X']*4 if mode=='chess' else [color for color in COLORS for _ in range(counts[color])]
            if mode=='sorting':
                # Independent of RGB grouping; preserve counts and randomize homes.
                secrets.SystemRandom().shuffle(tokens)
            width=piece_width(counts)
            for index,(token,(x,y)) in enumerate(zip(tokens,homes(len(tokens),a))):
                preset='token_'+token.lower() if mode=='chess' else 'box'
                color=('#30cadb' if token=='O' else '#e0a33b') if mode=='chess' else COLORS[token]
                piece=item('piece',preset,[82,82,18] if mode=='chess' else [width,width,24],color,[x,y,13 if mode=='chess' else 16],token if mode=='chess' else '',index)
                if mode=='sorting':piece['name']={'red':'红','green':'绿','blue':'蓝'}[token]+f'工件{index+1}'
            return {'group':group,'mode':mode}
        return self.transaction(create)

    def reset(self,payload):
        fields(payload,('group',),('group',))
        def reset(items):
            pieces,a,_=self.group(items,payload['group'])
            for piece,(x,y) in zip(pieces,homes(len(pieces),a)):at(piece,a,x,y,-1)
            return {'group':payload['group'],'board':[None]*9,'result':None}
        return self.transaction(reset)

    def chess(self,payload):
        fields(payload,('group','slot'),('group','slot'))
        if type(payload['slot']) is not int or not 0<=payload['slot']<9:fail('棋盘格需要0～8整数')
        def move(items):
            pieces,_,b=self.group(items,payload['group'],'chess')
            if len(pieces)!=9 or sum(p['task']['symbol']=='O' for p in pieces)!=5 or sum(p['task']['symbol']=='X' for p in pieces)!=4:
                fail('棋子必须完整保留5圈4叉',409)
            board=[None]*9
            for piece in pieces:
                task=piece['task'];slot=task['slot']
                if slot<0:continue
                if slot>=9 or board[slot]:fail('棋子格位冲突，请复位',409)
                x,y=board_position(b,slot)
                if math.dist(piece['xyz'],[x,y,b['xyz'][2]+half_height(b)+half_height(piece)])>.5:
                    fail('已落棋子或B区被手动移动，请先复位棋局',409)
                board[slot]=task['symbol']
            if board.count('O')!=board.count('X') or outcome(board):fail('棋局已结束或回合状态异常，请重新开始',409)
            if board[payload['slot']]:fail('该格已有棋子',409)
            def place(symbol,slot):
                piece=next((p for p in pieces if p['task']['symbol']==symbol and p['task']['slot']==-1),None)
                if piece is None:fail('A区棋子不足，请重新布置',409)
                at(piece,b,*board_position(b,slot),slot);board[slot]=symbol
            place('O',payload['slot'])
            if not outcome(board):place('X',computer_move(board))
            return {'group':payload['group'],'board':board,'result':outcome(board)}
        return self.transaction(move)

    def sort(self,payload):
        fields(payload,('group','criterion'),('group','criterion'))
        fail('请使用带视觉绑定和完整轨迹预检的机器人分拣程序',409)

def create_play_router(world):
    service=PlayService(world);router=APIRouter(prefix='/api/play')
    for name in ('setup','reset','chess','sort'):
        def endpoint(method):
            async def call(payload:dict=Body(...)):
                try:
                    if method.__name__ in ('chess','sort'):
                        fail('请先生成并预检机器人取放程序，再人工确认执行；不再直接搬动任务工件',409)
                    return method(payload)
                except ValueError as exc:fail(str(exc))
                except OSError:fail('任务场景保存失败，未修改内存状态',503)
            return call
        router.add_api_route('/'+name,endpoint(getattr(service,name)),methods=['POST'])
    return router

