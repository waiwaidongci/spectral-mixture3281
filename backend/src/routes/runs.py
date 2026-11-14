from fastapi import APIRouter,Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session
from ..config.database import get_db
from ..controllers.dependencies import current_user,roles
from ..constants.roles import Role
from ..schemas.run import RunInput
from ..services.run_service import list_runs,get_run,create_run,export_run
from ..utils.serialize import record
router=APIRouter(prefix="/runs",tags=["runs"])
@router.get("")
def index(db:Session=Depends(get_db),user=Depends(current_user)):return list_runs(db)
@router.get("/{rid}")
def show(rid:int,db:Session=Depends(get_db),user=Depends(current_user)):return record(get_run(db,rid))
@router.post("",status_code=202)
def create(data:RunInput,db:Session=Depends(get_db),user=Depends(roles(Role.ADMIN,Role.ANALYST))):return create_run(db,data,user)
@router.get("/{rid}/export")
def export(rid:int,result_id:int|None=None,db:Session=Depends(get_db),user=Depends(current_user)):
    suffix=f"-result-{result_id}" if result_id is not None else ""
    return Response(content='﻿'+export_run(db,rid,result_id),media_type="text/csv",headers={"Content-Disposition":f'attachment; filename="spectral-run-{rid}{suffix}.csv"'})
