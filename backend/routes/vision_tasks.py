"""Read-only measured finding and inspection routes."""
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from ..application.vision_tasks import VisionTasks,VisionTaskError

def create_vision_tasks_router(vision):
 service=VisionTasks(vision);router=APIRouter(prefix='/api/vision/tasks',tags=['vision-tasks']);router.tasks=service
 @router.get('/state')
 async def state():return service.state()
 def endpoint(method):
  async def handler(request:Request):
   try:return getattr(service,method)(await request.json())
   except VisionTaskError as e:return JSONResponse({'ok':False,'why':str(e)},status_code=e.status)
   except (ValueError,TypeError):return JSONResponse({'ok':False,'why':'请求格式无效'},status_code=422)
  return handler
 for method in ('preview','inspect','run','cancel'):router.add_api_route('/'+method,endpoint(method),methods=['POST'])
 return router
