"""桌面端私有后端入口；不接受渲染器任意命令或外网监听。"""
import os
from pathlib import Path
import sys

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    import asyncio
    import json
    import secrets
    import socket
    import uvicorn
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from backend.main import app, world, cameras

    key = os.environ.get('ELFIN_DESKTOP_KEY', '')
    if len(key) < 32:
        raise RuntimeError('桌面会话凭据缺失')
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=0,
        access_log=False, log_level='warning', timeout_graceful_shutdown=10))

    def authorized(request):
        return secrets.compare_digest(request.headers.get('x-elfin-desktop-key', ''), key)

    @app.get('/api/desktop/status')
    async def desktop_status(request: Request):
        if not authorized(request):
            return JSONResponse({'ok': False}, status_code=403)
        return {'ready': server.started, 'pid': os.getpid()}

    @app.post('/api/desktop/shutdown')
    async def desktop_shutdown(request: Request):
        if not authorized(request):
            return JSONResponse({'ok': False}, status_code=403)
        result = await world.stop_execution('关闭桌面应用')
        if result.get('ok') is not True or result.get('settled') is False:
            return JSONResponse(result, status_code=409)
        await asyncio.to_thread(cameras.close)
        server.should_exit = True
        return {'ok': True, 'settled': True}

    # 正确：backend.main末尾已挂载/静态资源；私有API必须排在该兜底路由之前。
    private = [route for route in app.router.routes
               if getattr(route, 'path', '') in ('/api/desktop/status', '/api/desktop/shutdown')]
    app.router.routes[:] = private + [route for route in app.router.routes if route not in private]

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    print(json.dumps({'type': 'desktop-listening', 'port': listener.getsockname()[1]}), flush=True)
    server.run(sockets=[listener])


if __name__ == '__main__':
    main()
