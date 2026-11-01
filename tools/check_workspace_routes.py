"""Workspace API offline checks.

Pure modules/stub HTTP: backend/.venv/Scripts/python.exe -B tools/check_workspace_routes.py --stub
Real World in runner copies: same command with --offline-runner instead.
The latter registers this check ONLY in a temporary bootstrap copy's manifest;
the workspace's main, offline manifest and actual data are never edited.
"""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def offline_runner():
    import json
    import shutil
    import subprocess
    import tempfile
    target = Path(tempfile.mkdtemp(prefix='elfin-workspace-bootstrap-', dir=os.environ.get('WORKSPACE_CHECK_TMP')))
    for rel in ('backend', 'tools', 'web/public/models', 'web/public/tools'):
        source = ROOT / rel
        if source.exists():
            shutil.copytree(source, target / rel, ignore=shutil.ignore_patterns('data', '__pycache__', '*.log', '*.png'))
    manifest = target / 'tools/offline_checks.json'
    value = json.loads(manifest.read_text(encoding='utf-8'))
    if 'check_workspace_routes.py' not in value['checks']:
        value['checks'].append('check_workspace_routes.py')
    manifest.write_text(json.dumps(value), encoding='utf-8')
    env = dict(os.environ, HUYAN_NO_REAL='1', PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    if os.environ.get('WORKSPACE_CHECK_TMP'):
        env.update(TEMP=os.environ['WORKSPACE_CHECK_TMP'], TMP=os.environ['WORKSPACE_CHECK_TMP'])
    print('Temporary runner bootstrap:', target, flush=True)
    return subprocess.run([sys.executable, '-B', str(target / 'tools/run_offline_checks.py'),
                           'check_workspace_routes.py'], cwd=target, env=env).returncode


if __name__ == '__main__' and '--offline-runner' in sys.argv:
    raise SystemExit(offline_runner())

# Importing these modules does not instantiate World, load config, or create DATA_DIR.
import copy
import json
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from backend.core.objects import ObjectStore
from backend.core.storage import write_json
from backend.workspace_routes import Workspace, PROFILE_KEYS, create_workspace_router


def stub(root):
    settings = {'mode': 'sim', 'sim_speed_pct': 10, 'ghost_opacity': .34, 'ghost_mode': 'ghost',
        'ghost_align_tol_deg': .3, 'ghost_align_hold_s': .5, 'tts': True, 'tts_rate': 1.,
        'log_keep': 2000, 'play_len_s': 300, 'trail_len': 40, 'robot_ip': 'private-controller',
        'auto_connect': False, 'real_speed_pct': 5, 'real_move_cmd': 'relj',
        'api_key': 'never-export-this', 'ai_base_url': 'https://private.example',
        'fence': {'enabled': True}, 'self_collision': True}
    w = NS(settings=settings, real=None, _cfg_rev=0, _safe_key='cached',
        executor=NS(busy=False), runner=NS(state='idle'), queue_state='idle',
        sim=NS(traj=None,jog=None,paused=False), hand_follow=NS(active=False),
        real_jog_follow=NS(busy=False), free={'on': False}, ready_state={'running': False},
        objects=ObjectStore(root / 'objects.json'), _stop_interlocked=lambda:False)
    def update_settings(patch):
        candidate = {**w.settings, **copy.deepcopy(patch)}
        write_json(root / 'settings.json', candidate)
        w.settings = candidate
        w._cfg_rev += 1
        return candidate
    w.update_settings = update_settings
    # 真实 World 提供落位即拦截用的机器人重叠判定；桩里给出同样的契约。
    w.robot_overlap = lambda obj: None
    return w


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='elfin-workspace-unit-', dir=os.environ.get('WORKSPACE_CHECK_TMP'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.world = stub(self.root)
        self.workspace = Workspace(self.world, self.root / 'profiles.json')
        app = FastAPI()
        self.router = create_workspace_router(self.world, profiles_path=self.workspace.path)
        app.include_router(self.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def create(self):
        return self.workspace.save_profile({'name': '测试档案'})['profile']

    def assertConflict(self, fn, status=409):
        with self.assertRaises(HTTPException) as exc:
            fn()
        self.assertEqual(exc.exception.status_code, status)

    def revision(self):
        return self.workspace.scene_status()['revision']

    def template(self, name='workbench'):
        return self.workspace.edit_scene({'template': name, 'revision': self.revision()}, 'template')

    def test_c_frontend_contract_save_export_import_compare_apply_delete(self):
        c, base = self.client, '/api/workspace/profiles'
        self.assertEqual(c.get(base).json()['items'], [])
        result = c.post(base, json={'name': '当前配置'})
        self.assertEqual(result.status_code, 200)
        item = result.json()['profile']
        self.assertEqual(set(item['settings']), PROFILE_KEYS)
        exported = c.get(base+'/'+item['id']).json()
        self.assertNotIn('never-export-this', json.dumps(exported))
        imported = c.post(base+'/import',json={'name': '导入', 'settings': {'sim_speed_pct': 25}}).json()['profile']
        comparison = c.post(base+'/'+imported['id']+'/compare',json={}).json()
        self.assertEqual(comparison['changes'], [{'key':'sim_speed_pct','current':10,'saved':25}])
        applied = c.post(base+'/'+imported['id']+'/apply',json={'revision':comparison['profile']['revision']})
        self.assertEqual(applied.status_code,200)
        self.assertEqual(self.world.settings['sim_speed_pct'],25)
        self.assertEqual(self.world.settings['real_speed_pct'],5)
        self.assertEqual(c.delete(base+'/'+imported['id']).status_code,200)
        self.assertEqual(c.get(base+'/'+imported['id']).status_code,404)

    def test_whitelist_rejects_connections_secrets_real_and_nested_smuggling(self):
        for key in ('mode','robot_ip','auto_connect','api_key','token','real_speed_pct','real_move_cmd',
                    'ai_base_url','custom_limits','fence','self_collision','vel_source','sim_tool'):
            response=self.client.post('/api/workspace/profiles/import',json={'name':'拒绝','settings':{key:'secret'}})
            self.assertEqual(response.status_code,422,key)
            self.assertNotIn('secret',response.text)
        for value in ({'token':'secret'}, [1], True, None, '0.5'):
            self.assertConflict(lambda:self.workspace.save_profile({'name':'拒绝','settings':{'ghost_opacity':value}},True),422)
        self.assertFalse(self.workspace.path.exists())

    def test_invalid_types_ranges_names_unknown_fields_are_all_rejected(self):
        for settings in ({}, {'sim_speed_pct':float('nan')},{'sim_speed_pct':float('inf')},
                         {'sim_speed_pct':True},{'sim_speed_pct':0},{'ghost_mode':'other'},
                         {'tts':1},{'trail_len':2},{'trail_len':40.5},{'log_keep':2.5}, {'tts_rate':1e50}):
            self.assertConflict(lambda:self.workspace.save_profile({'name':'无效','settings':settings},True),422)
        for name in ('', 'a'*65, 'bad\nname', 12):
            self.assertConflict(lambda:self.workspace.save_profile({'name':name}),422)
        self.assertConflict(lambda:self.workspace.save_profile({'name':'无效','settings':{'tts':True}}),422)
        self.assertFalse(self.workspace.path.exists())

    def test_revision_binds_both_profile_disk_and_current_settings(self):
        item=self.create();key=item['id']
        self.world.settings['ghost_opacity']=.6
        self.assertConflict(lambda:self.workspace.apply_profile(key,{'revision':item['revision']}))
        compared=self.workspace.compare_profile(key)['profile']
        self.world._cfg_rev+=1
        self.assertConflict(lambda:self.workspace.apply_profile(key,{'revision':compared['revision']}))
        compared=self.workspace.compare_profile(key)['profile']
        raw=json.loads(self.workspace.path.read_text(encoding='utf-8'))
        raw['items'][key]['settings']['ghost_opacity']=.9
        write_json(self.workspace.path,raw)
        self.assertConflict(lambda:self.workspace.apply_profile(key,{'revision':compared['revision']}))
        for revision in (None, True, 1, ''):
            self.assertConflict(lambda:self.workspace.apply_profile(key,{'revision':revision}))
        self.assertEqual(self.world.settings['ghost_opacity'],.6)

    def test_all_nonidle_states_reject_without_writes(self):
        item=self.create()
        changes=[(self.world.settings,'mode','real'),(self.world,'real',NS(stream_up=False,cmd_up=False)),
                 (self.world.executor,'busy',True),(self.world.runner,'state','paused'),
                 (self.world,'queue_state','running'),(self.world.sim,'traj',{}),
                 (self.world.sim,'jog',{}),(self.world.sim,'paused',True),
                 (self.world.hand_follow,'active',True),(self.world.real_jog_follow,'busy',True),
                 (self.world.free,'on',True),(self.world.ready_state,'running',True),
                 (self.world,'_stop_interlocked',lambda:True)]
        for target,key,value in changes:
            old=target[key] if isinstance(target,dict) else getattr(target,key)
            if isinstance(target,dict):target[key]=value
            else:setattr(target,key,value)
            self.assertConflict(lambda:self.workspace.apply_profile(item['id'],{'revision':item['revision']}))
            self.assertConflict(lambda:self.template())
            if isinstance(target,dict):target[key]=old
            else:setattr(target,key,old)
        self.assertFalse((self.root/'settings.json').exists())
        self.assertEqual(self.world.objects.list(),[])

    def test_corrupt_store_never_replaced_or_leaks_fields(self):
        item=self.create();raw=json.loads(self.workspace.path.read_text(encoding='utf-8'))
        raw['items'][item['id']]['settings']['api_key']='poisoned-secret'
        write_json(self.workspace.path,raw);before=self.workspace.path.read_bytes()
        self.assertConflict(lambda:self.workspace.profiles())
        self.assertConflict(lambda:self.create())
        self.assertEqual(before,self.workspace.path.read_bytes())
        self.workspace.path.write_text('{broken',encoding='utf-8')
        self.assertConflict(lambda:self.workspace.delete_profile(item['id']))
        self.assertEqual(self.workspace.path.read_text(encoding='utf-8'),'{broken')

    def test_atomic_profile_save_failure_preserves_file_and_no_temp_leaks(self):
        self.create();before=self.workspace.path.read_bytes()
        with patch('backend.core.storage.os.replace',side_effect=OSError('disk failure')):
            self.assertConflict(lambda:self.create(),503)
        self.assertEqual(before,self.workspace.path.read_bytes())
        self.assertEqual(list(self.root.glob('*.tmp')),[])

    def test_atomic_apply_failure_keeps_settings(self):
        item=self.workspace.save_profile({'name':'导入','settings':{'sim_speed_pct':99}},True)['profile']
        before=copy.deepcopy(self.world.settings)
        with patch('backend.core.storage.os.replace',side_effect=OSError('disk failure')):
            self.assertConflict(lambda:self.workspace.apply_profile(item['id'],{'revision':item['revision']}),503)
        self.assertEqual(before,self.world.settings)
        self.assertFalse((self.root/'settings.json').exists())

    def test_scene_template_array_undo_redo_and_branch(self):
        first=self.template();self.assertEqual(len(first['objects']),1)
        second=self.workspace.edit_scene({'id':first['objects'][0]['id'],'counts':[2,2,1],
            'spacing':[700,500,0],'revision':first['revision']},'array')
        self.assertEqual(len(second['objects']),4)
        self.assertEqual(len({o['id'] for o in second['objects']}),4)
        self.workspace.history('undo',{'revision':self.revision()})
        self.assertEqual(len(self.world.objects.list()),1)
        self.workspace.history('undo',{'revision':self.revision()})
        self.assertEqual(self.world.objects.list(),[])
        self.workspace.history('redo',{'revision':self.revision()})
        self.workspace.history('redo',{'revision':self.revision()})
        self.assertEqual(len(self.world.objects.list()),4)
        self.workspace.history('undo',{'revision':self.revision()});self.workspace.edit_scene({'spec':{'kind':'box','xyz':[2000,0,50]},'revision':self.revision()},'add')
        self.assertConflict(lambda:self.workspace.history('redo',{'revision':self.revision()}))

    def test_scene_overlap_is_a_clear_atomic_api_error_not_a_partial_edit(self):
        first=self.workspace.edit_scene({'spec':{'kind':'box','xyz':[0,0,50],'size':[100,100,100]},'revision':self.revision()},'add')
        disk=self.world.objects.path.read_bytes();before=self.world.objects.list()
        self.assertConflict(lambda:self.workspace.edit_scene({'spec':{'kind':'box','xyz':[20,0,50],'size':[100,100,100]},'revision':first['revision']},'add'),422)
        self.assertEqual(self.world.objects.path.read_bytes(),disk)
        self.assertEqual(self.world.objects.list(),before)

    def test_external_memory_disk_config_and_physics_changes_block_history(self):
        for mutate in (lambda:self.world.objects.update(self.world.objects.list()[0]['id'],{'name':'外部'}),
                       lambda:write_json(self.world.objects.path,[]),
                       lambda:setattr(self.world,'_cfg_rev',self.world._cfg_rev+1),
                       lambda:self.world.objects.items[0]['xyz'].__setitem__(0,123)):
            self.world.objects.clear()  # External-change cases do not overlap prior fixtures.
            self.template();before=self.revision();mutate()
            current=self.world.objects.list();disk=self.world.objects.path.read_bytes()
            self.assertConflict(lambda:self.workspace.history('undo',{'revision':before}))
            self.assertConflict(lambda:self.workspace.history('undo',{'revision':self.revision()}))
            self.assertEqual(self.world.objects.list(),current);self.assertEqual(self.world.objects.path.read_bytes(),disk)
            # Resolve an external disk-only edit by actually reopening that
            # file. A stale in-memory replace must not erase external changes.
            self.world.objects = ObjectStore(self.world.objects.path)
        self.world.objects.clear()
        self.template();self.assertEqual(len(self.workspace.undo_stack),1)

    def test_disk_only_external_edit_cannot_be_overwritten_even_after_refresh(self):
        self.template();write_json(self.world.objects.path,[])
        self.assertConflict(lambda:self.template('pallet'))
        self.assertEqual(json.loads(self.world.objects.path.read_text(encoding='utf-8')),[])

    def test_scene_write_failure_keeps_history_and_objects(self):
        self.template();before=self.world.objects.list();revision=self.revision()
        with patch('backend.core.storage.os.replace',side_effect=OSError('disk failure')):
            self.assertConflict(lambda:self.workspace.history('undo',{'revision':revision}),503)
        self.assertEqual(self.world.objects.list(),before);self.assertEqual(self.revision(),revision)
        self.assertEqual(len(self.workspace.undo_stack),1)

    def test_array_limits_nan_and_unknown_fields_no_partial_writes(self):
        first=self.template();oid=first['objects'][0]['id'];before=self.revision()
        for counts in ([0,1,1],[True,1,2],[12,12,12],[2,1], [1,1,1]):
            self.assertConflict(lambda:self.workspace.edit_scene({'id':oid,'counts':counts,'spacing':[1,0,0],'revision':before},'array'),422)
        for spacing in ([float('nan'),0,0], [float('inf'),0,0], [True,0,0]):
            self.assertConflict(lambda:self.workspace.edit_scene({'id':oid,'counts':[2,1,1],'spacing':spacing,'revision':before},'array'),422)
        self.assertEqual(self.revision(),before)

    def test_router_scene_contract_and_missing_revision(self):
        base='/api/workspace/scene';c=self.client
        revision=c.get(base).json()['revision']
        self.assertEqual(c.post(base+'/template',json={'template':'pallet'}).status_code,422)
        result=c.post(base+'/template',json={'template':'pallet','revision':revision})
        self.assertEqual(result.status_code,200)
        result=c.post(base+'/undo',json={'revision':result.json()['revision']})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['objects'],[])

    def alignment_objects(self):
        self.world.objects.replace([
            {'id':'box','kind':'box','xyz':[25,-25,300],'size':[20,30,80],'gravity':False,'floor':True},
            {'id':'sphere','kind':'sphere','xyz':[150,75,400],'size':[30,30,30],'gravity':True,'floating':True},
            {'id':'cylinder','kind':'cylinder','xyz':[-75,25,500],'size':[20,20,100]},
        ])

    def test_align_ground_geometry_batch_history_and_preserve_flags(self):
        self.alignment_objects();before=self.world.objects.list()
        result=self.workspace.edit_scene({'ids':['box','sphere','cylinder'],'ground':True,'revision':self.revision()},'align')
        self.assertEqual([row['xyz'][2] for row in result['objects']],[40,30,50])
        for old,new in zip(before,result['objects']):
            self.assertEqual({key:value for key,value in old.items() if key!='xyz'},
                             {key:value for key,value in new.items() if key!='xyz'})
        self.assertEqual(len(self.workspace.undo_stack),1)
        self.workspace.history('undo',{'revision':result['revision']})
        self.assertEqual(self.world.objects.list(),before)
        redone=self.workspace.history('redo',{'revision':self.revision()})
        self.assertEqual(redone['objects'],result['objects'])

    def test_align_grid_js_rounding_subset_and_noop(self):
        self.alignment_objects();before=self.world.objects.list()
        result=self.workspace.edit_scene({'ids':['box','cylinder'],'axis':['x','y'],'step':50,'revision':self.revision()},'align')
        self.assertEqual(result['objects'][0]['xyz'],[50,0,300])
        self.assertEqual(result['objects'][2]['xyz'],[-50,50,500])
        self.assertEqual(result['objects'][1],before[1])
        disk=self.world.objects.path.read_bytes()
        again=self.workspace.edit_scene({'ids':['box','cylinder'],'axis':['x','y'],'step':50,'revision':result['revision']},'align')
        self.assertEqual(again['revision'],result['revision'])
        self.assertEqual(self.world.objects.path.read_bytes(),disk)
        self.assertEqual(len(self.workspace.undo_stack),1)

    def test_align_invalid_batch_is_all_or_nothing(self):
        self.alignment_objects();revision=self.revision();before=self.world.objects.list()
        invalid=[{'ids':[],'ground':True},{'ids':['box','box'],'ground':True},
                 {'ids':[True],'ground':True},{'ids':['box'],'ground':1},
                 {'ids':['box'],'ground':True,'axis':['x'],'step':50},
                 {'ids':['box'],'axis':[],'step':50},{'ids':['box'],'axis':['x','x'],'step':50},
                 {'ids':['box'],'axis':['bad'],'step':50},{'ids':['box'],'axis':['x'],'step':0},
                 {'ids':['box'],'axis':['x'],'step':True},{'ids':['box'],'axis':['x'],'step':float('nan')},
                 {'ids':['box'],'axis':['x'],'step':float('inf')},
                 {'ids':['box'],'axis':['x'],'step':1e-320}]
        for payload in invalid:
            self.assertConflict(lambda:self.workspace.edit_scene({**payload,'revision':revision},'align'),422)
        self.assertConflict(lambda:self.workspace.edit_scene({'ids':['box','missing'],'ground':True,'revision':revision},'align'),404)
        self.assertEqual(self.world.objects.list(),before);self.assertEqual(self.revision(),revision)
        self.assertEqual(self.workspace.undo_stack,[])
        self.world.objects.update('box',{'xyz':[1000000,0,300]})
        current=self.revision()
        self.assertConflict(lambda:self.workspace.edit_scene({'ids':['box'],'axis':['x'],'step':600000,'revision':current},'align'),422)
        self.assertEqual(self.revision(),current)

    def test_align_router_revision_real_idle_external_change_and_write_failure(self):
        self.alignment_objects();c=self.client;url='/api/workspace/scene/align'
        revision=self.router.workspace.scene_status()['revision']
        payload={'ids':['box','sphere'],'ground':True,'revision':revision}
        self.assertEqual(c.post(url,json={'ids':['box'],'ground':True}).status_code,422)
        self.world.real=NS(cmd_up=False,stream_up=False)
        self.assertEqual(c.post(url,json=payload).status_code,409);self.world.real=None
        self.world.executor.busy=True
        self.assertEqual(c.post(url,json=payload).status_code,409);self.world.executor.busy=False
        before=self.world.objects.list();disk=self.world.objects.path.read_bytes()
        with patch('backend.core.storage.os.replace',side_effect=OSError('disk failure')):
            self.assertEqual(c.post(url,json=payload).status_code,503)
        self.assertEqual(self.world.objects.list(),before)
        self.assertEqual(self.world.objects.path.read_bytes(),disk)
        self.assertEqual(self.router.workspace.undo_stack,[])
        result=c.post(url,json=payload)
        self.assertEqual(result.status_code,200);self.assertTrue(result.json()['can_undo'])
        self.assertEqual(c.post(url,json=payload).status_code,409)
        self.world.objects.update('box',{'name':'外部修改'})
        fresh=self.router.workspace.scene_status()['revision']
        self.assertEqual(c.post('/api/workspace/scene/undo',json={'revision':fresh}).status_code,409)
        write_json(self.world.objects.path,[])
        fresh=self.router.workspace.scene_status()['revision']
        self.assertEqual(c.post(url,json={**payload,'revision':fresh}).status_code,409)


def real_world_check():
    from _offline_guard import require_offline_copy
    require_offline_copy()
    from backend.core.world import World
    from backend.core import config
    w=World()
    assert w.real is None and config.no_real()
    service=Workspace(w)
    item=service.save_profile({'name':'实际World偏好','settings':{'sim_speed_pct':23}},True)['profile']
    service.apply_profile(item['id'],{'revision':item['revision']})
    assert w.sim.speed_pct == 23 and config.get_settings()['sim_speed_pct'] == 23
    assert json.loads(config.settings_path().read_text(encoding='utf-8'))['sim_speed_pct'] == 23
    state=service.edit_scene({'template':'pallet','revision':service.scene_status()['revision']},'template')
    assert len(w.objects.list()) == 1
    aligned=service.edit_scene({'ids':[state['objects'][0]['id']],'axis':['x'],'step':300,'revision':state['revision']},'align')
    assert w.objects.list()[0]['xyz'][0] == 600
    service.history('undo',{'revision':aligned['revision']})
    assert w.objects.list() == state['objects']
    service.history('undo',{'revision':service.scene_status()['revision']})
    assert w.objects.list() == [] and w.real is None
    print('Real World/config/ObjectStore integration in offline copy: PASS')


if __name__ == '__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(WorkspaceTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    if '--stub' not in sys.argv:
        real_world_check()
