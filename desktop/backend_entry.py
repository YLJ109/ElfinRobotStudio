"""桌面端私有后端入口；不接受渲染器任意命令或外网监听。"""
import os
from pathlib import Path
import sys

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    if getattr(sys,'frozen',False) and not os.environ.get('ELFIN_VISION_WORKER'):
        worker=Path(sys.executable).resolve().parent.parent/'vision/ElfinVision.exe'
        if worker.is_file():os.environ['ELFIN_VISION_WORKER']=str(worker)
    import asyncio
    import json
    import secrets
    import socket
    import uvicorn
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from backend.main import app
    world, cameras = app.state.resources.world, app.state.resources.cameras

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

    # 私有API必须排在静态兜底路由之前。
    private = [route for route in app.router.routes
               if getattr(route, 'path', '') in ('/api/desktop/status', '/api/desktop/shutdown')]
    app.router.routes[:] = private + [route for route in app.router.routes if route not in private]

    # 正确：backend.main只提供API（8710独立启动不得托管网页）；桌面窗口需要的页面由本
    # 进程单独挂载。开发态取自源码树的 web/dist，打包态在PyInstaller的 _MEIPASS/web/dist。
    pages = (Path(sys._MEIPASS) if getattr(sys, 'frozen', False)
             else Path(__file__).resolve().parents[1]) / 'web/dist'
    if (pages / 'index.html').is_file():
        from starlette.staticfiles import StaticFiles
        # 正确：main.py的"/"是API-only的404响应，不摘掉就会在兜底挂载之前截走首页。
        app.router.routes[:] = [route for route in app.router.routes
                                if not (getattr(route, 'path', '') == '/'
                                        and getattr(route, 'name', '') == 'api_only_root')]
        app.mount('/', StaticFiles(directory=str(pages), html=True))
    else:
        print('desktop pages missing: ' + str(pages), flush=True)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    print(json.dumps({'type': 'desktop-listening', 'port': listener.getsockname()[1]}), flush=True)
    server.run(sockets=[listener])


if __name__ == '__main__':
    main()
