import csv,io
import numpy as np
from fastapi import HTTPException
from ..models import SpectralLibrary,Endmember,MixtureSample,UnmixRun,ReplicationNote,RunRevision
from ..utils.serialize import record
from .unmix_algorithm import matrix_diagnostics
from .audit_service import log
from . import queue_service
from . import calibration_service
queue=queue_service.queue

def _run_payload(db,run):
    payload=record(run)
    revision=calibration_service.effective_revision(db,run)
    if revision and revision.result:
        payload["result"]=revision.result
        payload["current_result_id"]=revision.id
        payload["current_library_version"]=revision.library_version
        payload["current_result_kind"]=revision.kind
        payload["current_batch_id"]=revision.batch_id
    else:
        payload["current_result_id"]=None
        payload["current_library_version"]=run.calibration_version
        payload["current_result_kind"]=None
        payload["current_batch_id"]=None
    return payload

def list_runs(db):
    rows=[];backfilled=False
    for run in db.query(UnmixRun).order_by(UnmixRun.id.desc()):
        if run.status=="completed" and run.result and not db.query(RunRevision).filter_by(run_id=run.id).first():
            calibration_service.ensure_original_revision(db,run);backfilled=True
        rows.append(_run_payload(db,run))
    if backfilled:db.commit()
    return rows
def get_run(db,rid):
    row=db.get(UnmixRun,rid)
    if not row:raise HTTPException(404,"解混运行不存在")
    return row

def create_snapshot(db,library,sample_ids):
    members=db.query(Endmember).filter_by(library_id=library.id).order_by(Endmember.code).all()
    samples=db.query(MixtureSample).filter(MixtureSample.id.in_(sample_ids),MixtureSample.library_id==library.id).order_by(MixtureSample.code).all()
    if len(samples)!=len(sample_ids):raise HTTPException(422,"样本重复、不存在或不属于所选光谱库")
    return {"library":record(library),"endmembers":[record(e) for e in members],"samples":[record(s) for s in samples]}

def create_run(db,data,user):
    library=db.get(SpectralLibrary,data.library_id)
    if not library:raise HTTPException(404,"光谱库不存在")
    if len(set(data.sample_ids))!=len(data.sample_ids) or len(data.sample_ids)*data.repeats>480:raise HTTPException(422,"样本不可重复，样本数×重复数不得超过480")
    library=db.query(SpectralLibrary).filter_by(id=library.id).with_for_update().first()
    snapshot=create_snapshot(db,library,data.sample_ids)
    try:
        _,d=matrix_diagnostics(np.array([e['spectrum'] for e in snapshot['endmembers']]).T)
        if not d['supported']:raise ValueError(d['reason'])
    except ValueError as e:raise HTTPException(422,str(e))
    row=UnmixRun(**data.model_dump(),calibration_version=library.calibration_version or 1,input_snapshot=snapshot,owner=user.name)
    db.add(row);db.flush()
    items=calibration_service.attach_run(db,library,row)
    log(db,user.email,"unmix.enqueue",row.id);db.commit()
    try:queue.rpush(queue_service.RUN_JOBS,str(row.id))
    except Exception:
        row.status="failed";row.error="队列不可用，请重提";db.commit();raise HTTPException(503,row.error)
    calibration_service.enqueue_items(db,items)
    payload=_run_payload(db,row);payload["attached_batches"]=sorted({i.batch_id for i in items});return payload

def export_run(db,rid,result_id=None):
    row=get_run(db,rid)
    if not db.query(RunRevision).filter_by(run_id=rid).first() and row.result:
        calibration_service.ensure_original_revision(db,row);db.commit()
    revisions=db.query(RunRevision).filter_by(run_id=rid).order_by(RunRevision.id.desc()).all()
    if result_id is not None:
        revision=next((r for r in revisions if r.id==result_id),None)
        if not revision:raise HTTPException(422,"该结果版本不属于此研究")
    else:revision=revisions[0] if revisions else None
    if not revision:raise HTTPException(403,"仅获批完成研究可导出")
    approved=db.query(ReplicationNote).filter_by(run_id=rid,result_id=revision.id,status="approved").first()
    if row.status!="completed" or not revision.result or not approved:
        if result_id is None:
            raise HTTPException(403,"现行结果尚未获批，不能导出；原获批结果请按结果版本下载，新结果需另行审阅")
        raise HTTPException(403,"该结果版本尚未获批，不能导出；新结果需另行审阅")
    result=revision.result
    out=io.StringIO();w=csv.writer(out);w.writerow(['sample','endmember','weight','conditional_q025','conditional_q975','rss','rmse','noise_sigma','repeats','root_seed','input_hash','library_version','result_kind'])
    for s in result['samples']:
        for i,e in enumerate(result['endmembers']):w.writerow([s['code'],e['code'],s['point_fit']['weights'][i],s['lower'][i],s['upper'][i],s['point_fit']['rss'],s['point_fit']['rmse'],result.get('noise_sigma',row.noise_sigma),result.get('repeats',row.repeats),result.get('seed',row.seed),result['input_hash'],revision.library_version,revision.kind])
    return out.getvalue()
