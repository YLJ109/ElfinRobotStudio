"""Workspace APIs, with no World construction or hardware imports.

main integration (after constructing world):
    from .workspace_routes import create_workspace_router
    app.include_router(create_workspace_router(world))

Retain main's access/origin/body-size middleware. All handlers are async with
no await inside their check/write/publish transaction: World mutates on this
same event loop. Do not call the service from a worker thread while World runs.
Profile revision is an opaque optimistic concurrency token; send the revision
returned by compare verbatim to apply. It binds the saved profile AND current
settings/config revision; it contains no raw settings or credentials.

Scene alignment uses the same transaction/history as template and array:
    POST /api/workspace/scene/align {ids:[...], ground:true, revision:"..."}
    POST /api/workspace/scene/align {ids:[...], axis:["x","y"], step:50, revision:"..."}
Grid rounding matches JS Math.round (ties towards positive infinity). Ground
places each object's geometric bottom at Z=0, retaining all physics flags.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
import uuid

from fastapi import APIRouter, Body, HTTPException
from .core.objects import ObjectStore, half_height
from .core.storage import write_json
from .schemas import validate_settings


# Do not grow this from DEFAULTS: those include connection and real controls.
PROFILE_KEYS = frozenset({
    'sim_speed_pct', 'ghost_mode', 'ghost_opacity', 'ghost_align_tol_deg',
    'ghost_align_hold_s', 'tts', 'tts_rate', 'log_keep', 'play_len_s', 'trail_len',
})
MAX_PROFILES = 100
MAX_BYTES = 1024 * 1024
ID_RE = re.compile(r'^[a-f0-9]{32}$')


def fail(message, status=422):
    raise HTTPException(status, detail=message)


def fields(payload, allowed, required=()):
    if not isinstance(payload, dict) or set(payload) - set(allowed) or not set(required) <= set(payload):
        fail('请求字段无效或缺少必要字段')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def title(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 64 or any(ord(c) < 32 for c in value):
        fail('档案名称需要1～64个字符且不能包含控制字符')
    return value.strip()


def profile_settings(value):
    if not isinstance(value, dict) or not value or set(value) - PROFILE_KEYS:
        # Do not reflect attacker supplied keys/values into errors or logs.
        fail('配置档案仅接受非空的仿真/显示偏好白名单，禁止连接、凭据和真机设置')
    try:
        validate_settings(value)
        digest(value)
    except (ValueError, TypeError, OverflowError):
        fail('档案设置类型或数值无效')
    ranges = {'tts_rate': (.1, 4), 'ghost_align_tol_deg': (0, 180),
              'ghost_align_hold_s': (0, 60), 'play_len_s': (1, 3600), 'trail_len': (10, 100)}
    for key, (lo, hi) in ranges.items():
        if key in value and not lo <= value[key] <= hi:
            fail('档案设置数值超出范围')
    for key in ('play_len_s', 'trail_len'):
        if key in value and type(value[key]) is not int:
            fail('回放长度和拖尾长度需要整数')
    return copy.deepcopy(value)


class Workspace:
    def __init__(self, world, path=None):
        self.world = world
        self.path = Path(path) if path is not None else Path(world.objects.path).parent / 'workspace-profiles.json'
        self._lock = threading.RLock()
        self.undo_stack, self.redo_stack = [], []
        self.history_revision = None
        self._scene_disk = self._disk_hash()

    def require_idle(self,*,allow_play_planning=False):
        w = self.world
        play=getattr(w,'robot_play',None)
        if play and (play.active or play.held or (play.busy and not allow_play_planning)):
            fail('请先停止取放预检/程序并释放持件，再修改场景或执行其他任务',409)
        if w.settings.get('mode') != 'sim' or w.real is not None:
            fail('仅允许未连接真机的仿真模式', 409)
        if (w.executor.busy or w.runner.state in ('running', 'waiting', 'paused')
                or w.queue_state in ('running', 'paused') or w.sim.traj is not None
                or w.sim.jog is not None or w.sim.paused or w.hand_follow.active
                or w.real_jog_follow.busy or w.free.get('on') or w.ready_state.get('running')
                or w._stop_interlocked() or getattr(w, '_hand_centering', False)
                or getattr(w, '_hand_moving', False)
                or any(task is not None and not task.done() for task in
                    (getattr(w, key, None) for key in ('_ready_task', '_speed_task', '_boot_connect', '_hand_solver_task')))):
            fail('请先停止运动、程序、跟随或就绪任务并确认停止完成', 409)

    def _load(self):
        if not self.path.exists():
            return {}
        try:
            if self.path.stat().st_size > MAX_BYTES:
                raise ValueError()
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(raw, dict) or set(raw) != {'version', 'items'} or type(raw['version']) is not int or raw['version'] != 1:
                raise ValueError()
            items = raw['items']
            if not isinstance(items, dict) or len(items) > MAX_PROFILES:
                raise ValueError()
            for key, item in items.items():
                if not isinstance(key, str) or not ID_RE.fullmatch(key):
                    raise ValueError()
                fields(item, ('id', 'name', 'version', 'created', 'settings'), ('id', 'name', 'version', 'created', 'settings'))
                if item['id'] != key or title(item['name']) != item['name'] or type(item['version']) is not int or item['version'] < 1:
                    raise ValueError()
                if type(item['created']) not in (int, float) or not math.isfinite(item['created']):
                    raise ValueError()
                profile_settings(item['settings'])
            return items
        except (ValueError, OSError, TypeError, HTTPException, OverflowError):
            # Never silently replace a damaged/legacy store with an empty one.
            fail('档案存储损坏或包含非白名单字段，请先备份并检查文件', 409)

    def _save(self, items):
        payload = {'version': 1, 'items': items}
        if len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) > MAX_BYTES:
            fail('档案存储超过容量上限')
        try:
            write_json(self.path, payload)
        except OSError:
            fail('档案持久化失败，原档案未替换', 503)

    def _public(self, item):
        # Include every current setting only in the hash, never in the export.
        revision = digest({'profile': item, 'config_rev': self.world._cfg_rev,
                           'settings': self.world.settings})
        return {**copy.deepcopy(item), 'revision': revision}

    def _get(self, items, key):
        if not isinstance(key, str) or not ID_RE.fullmatch(key) or key not in items:
            fail('找不到配置档案', 404)
        return items[key]

    def profiles(self):
        with self._lock:
            return {'ok': True, 'items': [self._public(item) for item in self._load().values()],
                    'allowed_settings': sorted(PROFILE_KEYS)}

    def get_profile(self, key):
        with self._lock:
            item = self._public(self._get(self._load(), key))
            # settings at the top level also makes the response directly importable by C.
            return {'ok': True, **item, 'profile': item}

    def save_profile(self, payload, importing=False):
        fields(payload, ('name', 'settings') if importing else ('name',), ('name', 'settings') if importing else ('name',))
        name = title(payload['name'])
        settings = profile_settings(payload['settings'] if importing else
            {key: self.world.settings[key] for key in PROFILE_KEYS if key in self.world.settings})
        with self._lock:
            items = self._load()
            if len(items) >= MAX_PROFILES:
                fail('最多保存100个配置档案', 409)
            key = uuid.uuid4().hex
            item = {'id': key, 'name': name, 'version': 1, 'created': time.time(), 'settings': settings}
            self._save({**items, key: item})
            return {'ok': True, 'profile': self._public(item)}

    def compare_profile(self, key):
        with self._lock:
            item = self._get(self._load(), key)
            return {'ok': True, 'profile': self._public(item), 'changes': [
                {'key': name, 'current': copy.deepcopy(self.world.settings.get(name)), 'saved': copy.deepcopy(value)}
                for name, value in item['settings'].items() if self.world.settings.get(name) != value]}

    def apply_profile(self, key, payload):
        fields(payload, ('revision',), ('revision',))
        with self._lock:
            self.require_idle()
            item = self._get(self._load(), key)
            if not isinstance(payload['revision'], str) or payload['revision'] != self._public(item)['revision']:
                fail('revision冲突：档案或当前设置已变化，请重新比较', 409)
            patch = profile_settings(item['settings'])
            # Actual World.update_settings atomically persists via config.save_settings,
            # refreshes sim vmax/speed/limits, and invalidates safety via _cfg_rev.
            # No mode/connection/real setting can enter this patch.
            try:
                self.world.update_settings(patch)
            except OSError:
                fail('配置持久化失败，档案未应用；请回读当前状态', 503)
            return {'ok': True, 'profile': self._public(item), 'settings': patch}

    def delete_profile(self, key):
        with self._lock:
            items = self._load()
            self._get(items, key)
            self._save({name: item for name, item in items.items() if name != key})
            return {'ok': True}

    def _disk_hash(self):
        path = Path(self.world.objects.path)
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        except OSError:
            fail('无法读取场景存储，未修改现场', 503)

    def scene_revision(self):
        return digest({'objects': self.world.objects.list(), 'disk': self._disk_hash(), 'config': self.world._cfg_rev,
                       'settings': self.world.settings})

    def scene_status(self):
        with self._lock, self.world.objects._lock:
            revision = self.scene_revision()
            valid = self.history_revision == revision
            return {'ok': True, 'revision': revision, 'objects': self.world.objects.list(),
                    'can_undo': valid and bool(self.undo_stack), 'can_redo': valid and bool(self.redo_stack),
                    'templates': ['workbench', 'pallet']}

    def _scene_check(self, revision):
        self.require_idle()
        current = self.scene_revision()
        if not isinstance(revision, str) or current != revision:
            fail('revision冲突：现场或配置已变化，请重新读取', 409)
        if self._disk_hash() != self._scene_disk:
            # Normal ObjectStore APIs publish disk+memory together. A file-only
            # external edit has NOT been loaded into World: even a refreshed
            # HTTP revision must not let the old in-memory scene overwrite it.
            try:
                path = Path(self.world.objects.path)
                raw = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
                if not isinstance(raw, list) or len(raw) > 1000 or any(not isinstance(row, dict) or not row.get('id') for row in raw):
                    raise ValueError()
                loaded = [ObjectStore._sanitize(row) for row in raw]
                if loaded != self.world.objects.list():
                    raise ValueError()
            except (OSError, ValueError, TypeError):
                fail('检测到尚未载入的磁盘场景修改，请先处理外部修改；不会覆盖文件', 409)
        if self.world.objects.any_active():
            fail('场景物体仍在运动，请等待稳定或关闭重力', 409)
        return current

    def _replace(self, rows):
        try:
            self.world.objects.replace(rows)
        except OSError:
            fail('场景持久化失败，现场未替换', 503)
        self.world._cfg_rev += 1
        self.world._safe_key = None
        self._scene_disk = self._disk_hash()
        self.history_revision = self.scene_revision()

    def edit_scene(self, payload, action):
        if action == 'template':
            fields(payload, ('template', 'revision'), ('template', 'revision'))
        elif action == 'array':
            fields(payload, ('id', 'counts', 'spacing', 'revision'), ('id', 'counts', 'spacing', 'revision'))
        elif action == 'align':
            fields(payload, ('ids', 'axis', 'ground', 'step', 'revision'), ('ids', 'revision'))
        else:
            fail('未知场景操作')
        with self._lock, self.world.objects._lock:
            current = self._scene_check(payload['revision'])
            history_valid = self.history_revision == current
            before = self.world.objects.list()
            if action == 'template':
                templates = {
                    'workbench': [{'name': '仿真工作台', 'kind': 'box', 'xyz': [500, 0, 200], 'size': [600, 400, 400]}],
                    'pallet': [{'name': '仿真托盘', 'kind': 'box', 'xyz': [500, 0, 40], 'size': [400, 400, 80]}],
                }
                if not isinstance(payload['template'], str) or payload['template'] not in templates:
                    fail('未知场景模板')
                additions = templates[payload['template']]
            elif action == 'array':
                counts, spacing = payload['counts'], payload['spacing']
                if (not isinstance(counts, list) or len(counts) != 3
                        or any(type(n) is not int or not 1 <= n <= 12 for n in counts)
                        or not 2 <= math.prod(counts) <= 128):
                    fail('阵列需要三个1～12整数，总数量2～128（含原物体）')
                if (not isinstance(spacing, list) or len(spacing) != 3
                        or any(type(n) not in (int, float) or not -1e6 <= n <= 1e6 for n in spacing)):
                    fail('阵列间距需要三个有限数值')
                source = next((row for row in before if row['id'] == payload['id']), None)
                if source is None:
                    fail('找不到阵列源物体', 404)
                additions = [{**source, 'xyz': [source['xyz'][0]+i*spacing[0], source['xyz'][1]+j*spacing[1], source['xyz'][2]+k*spacing[2]]}
                    for i in range(counts[0]) for j in range(counts[1]) for k in range(counts[2]) if (i, j, k) != (0, 0, 0)]
            else:
                ids = payload['ids']
                if (not isinstance(ids, list) or not 1 <= len(ids) <= 1000
                        or any(not isinstance(key, str) or not key for key in ids)
                        or len(set(ids)) != len(ids)):
                    fail('对齐需要1～1000个不重复的物体ID')
                selected = set(ids)
                if not selected <= {row['id'] for row in before}:
                    fail('选中的物体已不存在，未对齐任何物体', 404)
                ground = payload.get('ground') is True
                if ground:
                    if set(payload) != {'ids', 'ground', 'revision'}:
                        fail('地面对齐不能混用网格轴或步距')
                    axes = []
                else:
                    if set(payload) != {'ids', 'axis', 'step', 'revision'}:
                        fail('请选择ground:true或明确的网格轴及步距')
                    axes = payload['axis']
                    if (not isinstance(axes, list) or not 1 <= len(axes) <= 3
                            or any(not isinstance(axis, str) or axis not in ('x', 'y', 'z') for axis in axes)
                            or len(set(axes)) != len(axes)):
                        fail('网格axis需要不重复的x/y/z轴数组')
                    step = payload['step']
                    if type(step) not in (int, float) or not 0 < step <= 1e6:
                        fail('网格步距需要0～1000000mm内的正有限数值')
                after = copy.deepcopy(before)
                for row in after:
                    if row['id'] not in selected:
                        continue
                    if ground:
                        row['xyz'][2] = half_height(row)
                    else:
                        for axis in axes:
                            index = ('x', 'y', 'z').index(axis)
                            ratio = row['xyz'][index] / step
                            # Tiny but finite step values must fail cleanly,
                            # rather than overflowing floor() before validation.
                            if not math.isfinite(ratio):
                                fail('网格步距过小，无法计算有限坐标')
                            row['xyz'][index] = math.floor(ratio + .5) * step
                    try:
                        ObjectStore._sanitize(row)
                    except (ValueError, OverflowError):
                        fail('对齐后坐标超出有效范围，未修改任何物体')
            if action != 'align':
                additions = [ObjectStore._sanitize({**row, 'id': uuid.uuid4().hex}) for row in additions]
                if len(before) + len(additions) > 1000:
                    fail('现场最多1000个物体')
                after = before + additions
            if after == before:
                return self.scene_status()
            self._replace(after)
            # External changes break the old history; explicit new edits start a fresh branch.
            if not history_valid:
                self.undo_stack.clear()
            self.undo_stack.append({'before': before, 'after': after})
            self.undo_stack = self.undo_stack[-32:]
            self.redo_stack.clear()
            return self.scene_status()

    def history(self, action, payload):
        fields(payload, ('revision',), ('revision',))
        with self._lock, self.world.objects._lock:
            current = self._scene_check(payload['revision'])
            if self.history_revision != current:
                fail('外部修改使撤销历史失效，不会覆盖当前现场', 409)
            source, target = (self.undo_stack, self.redo_stack) if action == 'undo' else (self.redo_stack, self.undo_stack)
            if not source:
                fail('没有可撤销/重做的操作', 409)
            entry = source[-1]
            self._replace(entry['before'] if action == 'undo' else entry['after'])
            source.pop()
            target.append(entry)
            return self.scene_status()


def create_workspace_router(world, *, profiles_path=None):
    router = APIRouter(prefix='/api/workspace')
    workspace = Workspace(world, profiles_path)
    router.workspace = workspace

    @router.get('/profiles')
    async def profiles():
        return workspace.profiles()

    @router.post('/profiles')
    async def save_profile(payload: dict = Body(...)):
        return workspace.save_profile(payload)

    @router.post('/profiles/import')
    async def import_profile(payload: dict = Body(...)):
        return workspace.save_profile(payload, importing=True)

    @router.get('/profiles/{key}')
    async def export_profile(key: str):
        return workspace.get_profile(key)

    @router.post('/profiles/{key}/compare')
    async def compare_profile(key: str, payload: dict = Body(...)):
        fields(payload, ())
        return workspace.compare_profile(key)

    @router.post('/profiles/{key}/apply')
    async def apply_profile(key: str, payload: dict = Body(...)):
        return workspace.apply_profile(key, payload)

    @router.delete('/profiles/{key}')
    async def delete_profile(key: str):
        return workspace.delete_profile(key)

    @router.get('/scene')
    async def scene():
        return workspace.scene_status()

    @router.post('/scene/template')
    async def scene_template(payload: dict = Body(...)):
        return workspace.edit_scene(payload, 'template')

    @router.post('/scene/array')
    async def scene_array(payload: dict = Body(...)):
        return workspace.edit_scene(payload, 'array')

    @router.post('/scene/align')
    async def scene_align(payload: dict = Body(...)):
        return workspace.edit_scene(payload, 'align')

    @router.post('/scene/undo')
    async def scene_undo(payload: dict = Body(...)):
        return workspace.history('undo', payload)

    @router.post('/scene/redo')
    async def scene_redo(payload: dict = Body(...)):
        return workspace.history('redo', payload)

    return router
