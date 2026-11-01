"""Retired studio compatibility: read existing recordings only; never invoke World."""
from pathlib import Path
from fastapi import APIRouter, HTTPException
from .core import config
from .core.task_studio import RecordingStore

RETIRED_REASON = '工艺入口已移除，请在程序中编排运动'


def create_task_router(world):
    router = APIRouter(prefix='/api/studio')
    data_dir = Path(getattr(world, '_data_dir', config.DATA_DIR))

    def recordings():
        try:
            return RecordingStore(data_dir / 'studio_recordings.json')
        except (OSError, ValueError):
            raise HTTPException(503, detail='历史录制读取失败，原数据未修改')

    @router.get('')
    async def status():
        store = recordings()
        return {'ok': True, 'recordings': store.list(), 'recording': store.status(),
                'readonly': True, 'deprecated': True, 'templates': []}

    @router.get('/recordings/{key}')
    async def get_recording(key: str):
        try:
            return {'ok': True, 'item': recordings().get(key), 'readonly': True}
        except ValueError:
            raise HTTPException(404, detail='录制轨迹不存在')

    async def retired(legacy_path: str = ''):
        raise HTTPException(409, detail=RETIRED_REASON)

    router.add_api_route('', retired, methods=['POST', 'PUT', 'DELETE', 'PATCH'])
    router.add_api_route('/{legacy_path:path}', retired, methods=['POST', 'PUT', 'DELETE', 'PATCH'])
    return router
