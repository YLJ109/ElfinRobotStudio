"""Explicit data backup and new-directory recovery; no device calls."""
import json
import asyncio
from fastapi import APIRouter,Request,Body,HTTPException
from fastapi.responses import JSONResponse
from .application.data_backup import DataBackup,MAX_BYTES,validate_bundle

def create_data_backup_router(resources,*,require_idle):
    router=APIRouter(prefix='/api/backup',tags=['data-backup'])
    backup=DataBackup(resources)
    operation=asyncio.Lock()
    async def capture():
        # Copy live references on their owning loop; only the detached payload
        # crosses into validation/JSON/disk workers.
        raw=backup.capture()
        return await asyncio.to_thread(backup.normalize_export,raw)
    @router.get('/export')
    async def export():
        require_idle()
        async with operation:
            value=await capture();require_idle()
            return await asyncio.to_thread(JSONResponse,value,headers={'Content-Disposition':'attachment; filename="elfin-data-backup.json"','Cache-Control':'no-store'})
    @router.post('/preview')
    async def preview(request:Request):
        require_idle()
        data=bytearray()
        async for chunk in request.stream():
            if len(data)+len(chunk)>MAX_BYTES:raise HTTPException(413,'备份不能超过32MiB')
            data.extend(chunk)
        try:
            async with operation:
                bundle=await asyncio.to_thread(json.loads,data)
                checked=await asyncio.to_thread(validate_bundle,bundle)
                current=await capture();require_idle()
                return await asyncio.to_thread(backup.preview,bundle,current_data=current['data'],checked_data=checked)
        except (ValueError,KeyError,TypeError,OverflowError) as exc:raise ValueError('备份检查失败：'+str(exc)) from exc
    @router.post('/restore-copy')
    async def restore(value:dict=Body(...)):
        require_idle()
        if set(value)!={'token'}:raise ValueError('请提供本次差异预览标识')
        async with operation:
            current=await capture();require_idle()
            return await asyncio.to_thread(backup.restore,value['token'],current_bundle=current)
    @router.post('/activation-plan')
    async def activate(value:dict=Body(...)):
        require_idle()
        if set(value)!={'copy_id'}:raise ValueError('恢复副本标识无效')
        try:
            async with operation:path=await asyncio.to_thread(backup.recovery_path,value['copy_id'])
            require_idle()
        except (OSError,KeyError,TypeError,ValueError) as exc:raise ValueError('恢复副本无法使用，请重新导入') from exc
        return {'ok':True,'data_dir':str(path),'copy_id':value['copy_id'],
                'manifest_digest':backup.recovery_digest(value['copy_id']),'requires_restart':True}
    router.backup=backup
    return router
