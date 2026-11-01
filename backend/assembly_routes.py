"""Bounded simulation recipe tasks; no additional motion owner."""
from fastapi import APIRouter,Body
from .application.assembly_tasks import AssemblyTasks


def create_assembly_router(world):
    service=AssemblyTasks(world);router=APIRouter(prefix='/api/assembly');router.assembly=service
    @router.get('/state')
    async def state():return service.state()
    for name in ('setup','preview','inspect','run','cancel'):
        def endpoint(method):
            async def call(payload:dict=Body(...)):
                try:
                    import inspect
                    value=method(payload)
                    return await value if inspect.isawaitable(value) else value
                except (ValueError,RuntimeError) as exc:return {'ok':False,'why':str(exc)}
            return call
        router.add_api_route('/'+name,endpoint(getattr(service,name)),methods=['POST'])
    return router
