"""Read-only execution records: deliberately no run/resume/import endpoint."""
from fastapi import APIRouter,HTTPException
from fastapi.responses import JSONResponse

def create_run_journal_router(journal):
    router=APIRouter(prefix='/api/runs',tags=['execution-evidence'])
    @router.get('')
    async def list_runs():return journal.list()
    @router.get('/{run_id}')
    async def get_run(run_id:str):
        try:return dict(ok=True,record=journal.get(run_id),analysis_only=True)
        except ValueError as exc:raise HTTPException(404,str(exc)) from exc
    @router.get('/{run_id}/export')
    async def export_run(run_id:str):
        try:value=journal.get(run_id)
        except ValueError as exc:raise HTTPException(404,str(exc)) from exc
        return JSONResponse(dict(version=1,record=value,analysis_only=True),headers={'Content-Disposition':'attachment; filename="execution-record.json"'})
    return router
