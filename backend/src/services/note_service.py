from fastapi import HTTPException
from ..models import UnmixRun,ReplicationNote,RunRevision
from .calibration_service import ensure_original_revision
from ..utils.serialize import record
from .audit_service import log

def list_notes(db):
    rows=[]
    for note in db.query(ReplicationNote).order_by(ReplicationNote.id.desc()):
        revision=db.get(RunRevision,note.result_id) if note.result_id else None
        rows.append(dict(record(note),result_kind=revision.kind if revision else "original",result_library_version=revision.library_version if revision else None,result_input_hash=revision.input_hash if revision else "",batch_id=revision.batch_id if revision and revision.kind=="calibration" else None))
    return rows
def create_note(db,data,user):
    run=db.get(UnmixRun,data.run_id)
    if not run or run.status!="completed":raise HTTPException(409,"只能对已完成解混研究提交复配建议")
    revision=None
    if data.result_id is not None:
        revision=db.query(RunRevision).filter_by(id=data.result_id,run_id=run.id).first()
        if not revision:raise HTTPException(422,"该结果版本不属于此研究")
    if revision is None:
        revision=db.query(RunRevision).filter_by(run_id=run.id,status="effective").order_by(RunRevision.id.desc()).first()
        if revision is None:revision=ensure_original_revision(db,run)
    payload=data.model_dump();payload.pop("result_id",None)
    row=ReplicationNote(**payload,result_id=revision.id if revision else None,owner=user.name,status="submitted")
    db.add(row);db.flush();log(db,user.email,"replication.submit",row.id);db.commit();return record(row)
def review_note(db,nid,data,user):
    row=db.get(ReplicationNote,nid)
    if not row:raise HTTPException(404,"复配建议不存在")
    if row.status!="submitted":raise HTTPException(409,"已审阅建议不可重复审批")
    row.status=data.status;row.reviewer=user.name;row.review_note=data.review_note
    log(db,user.email,"replication."+data.status,row.id);db.commit();return record(row)
