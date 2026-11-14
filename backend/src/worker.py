import json,time,traceback
from pathlib import Path
from .config.database import Base,engine,SessionLocal
from .models import UnmixRun
from .constants.status import RunStatus
from .services.queue_service import queue,RUN_JOBS,CALIBRATION_JOBS
from .services.analysis_service import analyze_unmix
from .services.calibration_service import process_item,complete_original_run,recover_work,original_revision
from .services.audit_service import log
Base.metadata.create_all(engine);Path("/artifacts").mkdir(exist_ok=True)
with SessionLocal() as db:
    try:
        runs,items=recover_work(db)
        if runs or items:print(f"recovered {runs} runs and {items} calibration items")
    except Exception:traceback.print_exc();db.rollback()
while True:
    Path("/artifacts/heartbeat").write_text(str(time.time()))
    job=queue.blpop([RUN_JOBS,CALIBRATION_JOBS],timeout=2)
    if not job:continue
    channel,payload=job
    if channel==CALIBRATION_JOBS:
        try:
            with SessionLocal() as db:process_item(db,int(str(payload).split(":")[-1]))
        except Exception:traceback.print_exc()
        continue
    with SessionLocal() as db:
        run=db.get(UnmixRun,int(payload))
        if not run or run.status not in (RunStatus.QUEUED,RunStatus.RUNNING):continue
        claimed=db.query(UnmixRun).filter_by(id=run.id,status=RunStatus.QUEUED).update({"status":RunStatus.RUNNING},synchronize_session=False)
        db.commit()
        if not claimed:
            if run.status==RunStatus.RUNNING and original_revision(db,run):run.status=RunStatus.COMPLETED;db.commit()
            continue
        run=db.get(UnmixRun,int(payload))
        try:
            if original_revision(db,run):
                run.status=RunStatus.COMPLETED;run.error="";db.commit();continue
            result=analyze_unmix(run.input_snapshot,run.noise_sigma,run.repeats,run.seed)
            complete_original_run(db,run,result)
            Path(f"/artifacts/run-{run.id}.json").write_text(json.dumps(result,ensure_ascii=False));log(db,"worker","unmix.completed",run.id)
        except Exception as e:run.status=RunStatus.FAILED;run.error=str(e);traceback.print_exc()
        db.commit()
