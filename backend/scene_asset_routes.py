"""Static GLB inspection and explicit simulation placement, no hardware IO."""
import asyncio
import base64
import binascii
from pathlib import Path
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response
from .core.scene_assets import SceneAssetStore, inspect_glb, MAX_BYTES
from .workspace_routes import Workspace


def create_scene_asset_router(world, *, workspace=None):
    router=APIRouter(prefix='/api/scene-assets')
    store=SceneAssetStore(Path(world.objects.path).parent/'scene_assets')
    workspace=workspace or Workspace(world)

    async def checked(payload):
        if not isinstance(payload,dict) or set(payload)-{'glb','metadata','object_id','revision'}:
            raise ValueError('资源请求字段无效')
        encoded=payload.get('glb')
        if not isinstance(encoded,str) or len(encoded)>4*((MAX_BYTES+2)//3):raise ValueError('模型大小超过1MiB')
        try:data=base64.b64decode(encoded,validate=True)
        except (ValueError,binascii.Error) as exc:raise ValueError('模型编码损坏') from exc
        info=await asyncio.wait_for(asyncio.to_thread(inspect_glb,data,payload.get('metadata')),2.)
        return data,info

    @router.post('/inspect')
    async def inspect(payload:dict=Body(...)):
        try:
            data,info=await checked(payload)
            return {'ok':True,'asset':info,'revision':workspace.scene_revision()}
        except (ValueError,asyncio.TimeoutError) as exc:
            return {'ok':False,'why':str(exc) or '模型检查超时，请简化后重试'}

    @router.post('/import')
    async def import_asset(payload:dict=Body(...)):
        try:
            workspace.require_idle()
            data,info=await checked(payload)
            # Cancellation/timeouts leave only a discarded pure validation job.
            # Recheck task ownership before the short synchronous transaction.
            workspace.require_idle()
            oid=payload.get('object_id')
            import re
            if not isinstance(oid,str) or not re.fullmatch(r'[a-f0-9]{12}',oid):raise ValueError('导入请求缺少稳定物体标识')
            existing=next((item for item in world.objects.list() if item['id']==oid),None)
            if existing:
                if existing.get('asset_id') and store.read_glb(existing['asset_id'])==data and {k:v for k,v in store.get(existing['asset_id']).items() if k!='asset_id'}==info:
                    return {'ok':True,'object':existing,'reused':True}
                raise ValueError('物体标识已被其他资源使用，请重新导入')
            with workspace._lock,world.objects._lock:
                current=workspace._scene_check(payload.get('revision'))
                old_scene=world.objects.list()
                before=set(store.path.glob('*.json'))
                asset=store.commit(data,info,checked=True)
                try:
                    obj=world.objects.sanitize({'id':oid,'name':info['name'],'kind':'box','size':info['size_mm'],
                        'xyz':[250.,0.,info['size_mm'][2]/2.], 'asset_id':asset['asset_id'],
                        'gravity':False,'collide':info['role']=='obstacle','floating':False})
                    after=[*old_scene,obj]
                    if len(after)>1000:raise ValueError('现场物体已达1000个上限')
                    history_valid=workspace.history_revision==current
                    workspace._replace(after)
                    if not history_valid:workspace.undo_stack.clear()
                    workspace.undo_stack.append({'before':old_scene,'after':after})
                    workspace.undo_stack=workspace.undo_stack[-32:];workspace.redo_stack.clear()
                except BaseException:
                    candidate=store._file(asset['asset_id'])
                    if candidate not in before:candidate.unlink(missing_ok=True)
                    raise
            world.plans.clear()
            world.log.add('event',f'已导入场景资源：{info["name"]}（保守箱体代理，不是可抓取资格）')
            return {'ok':True,'asset':asset,'object':obj}
        except (ValueError,OSError,asyncio.TimeoutError) as exc:
            return {'ok':False,'why':str(exc) or '模型导入超时，当前场景未改变'}

    @router.get('/{asset_id}/model.glb')
    async def model(asset_id:str):
        try:return Response(store.read_glb(asset_id),media_type='model/gltf-binary',headers={'Cache-Control':'private, max-age=31536000, immutable','X-Content-Type-Options':'nosniff'})
        except ValueError as exc:raise HTTPException(404,'资源不存在或缓存损坏') from exc

    return router
