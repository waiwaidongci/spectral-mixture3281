import copy
import math
import numpy as np
from sqlalchemy import func
from fastapi import HTTPException
from ..models import (SpectralLibrary,Endmember,UnmixRun,ReplicationNote,
                      CalibrationBatch,CalibrationItem,RunRevision)
from ..constants.status import BatchStatus,ItemStatus,RunStatus
from ..utils.clock import now
from ..utils.fingerprint import fingerprint
from ..utils.serialize import record
from .unmix_algorithm import matrix_diagnostics
from .analysis_service import analyze_unmix
from .audit_service import log
from . import queue_service

ALGORITHM_VERSION="simplex-nullspace-kkt-gaussian-v2"
EFFECTIVE="effective"
SUPERSEDED="superseded"
OPEN=(BatchStatus.COMPUTING,BatchStatus.READY)
ATTACHABLE=(BatchStatus.COMPUTING,BatchStatus.READY,BatchStatus.FAILED)
COMPUTABLE=(BatchStatus.COMPUTING,BatchStatus.READY,BatchStatus.FAILED)
TERMINAL=(ItemStatus.COMPLETED,ItemStatus.FAILED)


def _members_payload(members):
    return sorted([{"code":str(m["code"]),"name":str(m.get("name") or m["code"]),"spectrum":[float(v) for v in m["spectrum"]]} for m in members],key=lambda m:m["code"])

def endmember_hash(members):return fingerprint(_members_payload(members))

def _snapshot_samples(run):return sorted(run.input_snapshot.get("samples",[]),key=lambda s:s["code"])

def _run_snapshot(run,batch):
    """Canonical frozen input for a recalculation: the run's own samples/parameters with the batch endmembers.
    The batch can only publish at base_version+1, so that version is assigned to the frozen snapshot up front."""
    snapshot=copy.deepcopy(run.input_snapshot)
    library=copy.deepcopy(snapshot.get("library",{}))
    library["calibration_version"]=batch.base_version+1
    return {"library":library,
            "endmembers":[{"id":None,"library_id":batch.library_id,**m} for m in _members_payload(batch.endmembers)],
            "samples":_snapshot_samples(run)}

def source_hash(run,batch):
    """Exactly the fingerprint analyze_unmix will compute for this source; reused only when it matches."""
    return fingerprint({"method":ALGORITHM_VERSION,"snapshot":_run_snapshot(run,batch),
        "noise_sigma":run.noise_sigma,"repeats":run.repeats,"seed":run.seed})

def _original_source_hash(run):
    snapshot={"library":run.input_snapshot.get("library",{}),
              "endmembers":sorted(run.input_snapshot.get("endmembers",[]),key=lambda e:e["code"]),
              "samples":_snapshot_samples(run)}
    return fingerprint({"method":ALGORITHM_VERSION,"snapshot":snapshot,
        "noise_sigma":run.noise_sigma,"repeats":run.repeats,"seed":run.seed})

def validate_upload(db,library,data):
    members=_members_payload([m.model_dump() for m in data.endmembers])
    codes=[m["code"] for m in members]
    if len(set(codes))!=len(codes):raise HTTPException(422,"端元编号重复，无法逐一对应复测谱")
    current={e.code for e in db.query(Endmember).filter_by(library_id=library.id).all()}
    if current and current!=set(codes):
        missing=sorted(current-set(codes));extra=sorted(set(codes)-current)
        details=[]
        if missing:details.append("缺少 "+"、".join(missing))
        if extra:details.append("多余 "+"、".join(extra))
        raise HTTPException(422,"复测谱必须覆盖现行全部端元且不多不少（"+"；".join(details)+"）")
    for m in members:
        if len(m["spectrum"])!=len(library.wavelengths):raise HTTPException(422,f"端元 {m['code']} 的波段数与光谱库不一致（应为{len(library.wavelengths)}）")
        if not all(math.isfinite(v) for v in m["spectrum"]):raise HTTPException(422,f"端元 {m['code']} 含非有限数值")
    try:_,diagnostics=matrix_diagnostics(np.array([m["spectrum"] for m in members],dtype=float).T)
    except ValueError as e:raise HTTPException(422,str(e))
    return members,diagnostics

def _counts(db,batch_id):
    rows=db.query(CalibrationItem.status,func.count(CalibrationItem.id)).filter_by(batch_id=batch_id).group_by(CalibrationItem.status).all()
    counts={s:n for s,n in rows}
    return sum(counts.values()),counts.get(ItemStatus.COMPLETED,0),counts.get(ItemStatus.FAILED,0)

def _refresh_batch(db,batch):
    """Derive batch status from its items; published/superseded batches are terminal."""
    total,completed,failed=_counts(db,batch.id)
    batch.item_total=total;batch.item_completed=completed;batch.item_failed=failed
    if batch.status not in COMPUTABLE:return
    if not total:return
    remaining=db.query(CalibrationItem).filter(CalibrationItem.batch_id==batch.id,CalibrationItem.status.notin_(TERMINAL)).count()
    if remaining:batch.status=BatchStatus.COMPUTING
    elif failed:
        errors=[]
        for item in db.query(CalibrationItem).filter_by(batch_id=batch.id,status=ItemStatus.FAILED).order_by(CalibrationItem.run_id):
            if item.error and item.error not in errors:errors.append(item.error)
        batch.status=BatchStatus.FAILED
        batch.failure_reason=("；".join(errors))[:1500]
    else:
        batch.status=BatchStatus.READY;batch.failure_reason=""

def _new_item(db,batch,run):
    item=CalibrationItem(batch_id=batch.id,run_id=run.id,status=ItemStatus.PENDING,source_hash=source_hash(run,batch))
    db.add(item);db.flush();return item

def create_batch(db,library_id,data,user):
    library=db.query(SpectralLibrary).filter_by(id=library_id).with_for_update().first()
    if not library:raise HTTPException(404,"光谱库不存在")
    members,diagnostics=validate_upload(db,library,data)
    base=library.calibration_version or 1
    batch=CalibrationBatch(library_id=library.id,title=data.title or "",base_version=base,
        endmembers=members,endmember_hash=endmember_hash(members),created_by=user.name,
        status=BatchStatus.COMPUTING,created_at=now())
    db.add(batch);db.flush()
    if not diagnostics["supported"]:
        batch.status=BatchStatus.FAILED;batch.item_total=0
        batch.failure_reason="复测端元未通过可辨识性检查："+diagnostics["reason"]
        log(db,user.email,"calibration.rejected",batch.id);db.commit();return _batch_detail(db,batch)
    runs=db.query(UnmixRun).filter_by(library_id=library.id).order_by(UnmixRun.id).all()
    item_ids=[_new_item(db,batch,run).id for run in runs]
    _refresh_batch(db,batch)
    if not runs:batch.status=BatchStatus.READY
    log(db,user.email,"calibration.upload",batch.id);db.commit()
    try:
        for item_id in item_ids:queue_service.enqueue_item(item_id)
    except Exception:
        batch.status=BatchStatus.FAILED;batch.failure_reason="队列不可用，复测批次未提交计算；可稍后重试";db.commit()
    db.refresh(batch);return _batch_detail(db,batch)

def attach_run(db,library,run):
    """Attach a newly created run to every open batch of the current calibration."""
    batches=db.query(CalibrationBatch).filter(CalibrationBatch.library_id==library.id,
        CalibrationBatch.status.in_(ATTACHABLE),
        CalibrationBatch.base_version==(library.calibration_version or 1)).order_by(CalibrationBatch.id).all()
    items=[]
    for batch in batches:
        if db.query(CalibrationItem).filter_by(batch_id=batch.id,run_id=run.id).first():continue
        items.append(_new_item(db,batch,run))
        if batch.status==BatchStatus.READY:batch.status=BatchStatus.COMPUTING
        _refresh_batch(db,batch)
    return items

def enqueue_items(db,items):
    if not items:return
    try:
        for item in items:queue_service.enqueue_item(item.id)
    except Exception:
        for item in items:
            batch=db.get(CalibrationBatch,item.batch_id)
            if batch and batch.status in OPEN:
                batch.status=BatchStatus.FAILED;batch.failure_reason="队列不可用，复测批次未提交计算；可稍后重试"
        db.commit()

def _previous_result(db,run):
    """The result that a batch will actually replace: the run's currently effective result."""
    revision=effective_revision(db,run)
    return revision.result if revision and revision.result else (run.result or {})

def _changes(previous_result,result,members):
    codes=[m["code"] for m in _members_payload(members)]
    old={};old_members=[]
    previous_result=previous_result or {}
    if previous_result.get("samples"):
        old={s["code"]:s for s in previous_result["samples"]}
        old_members=[e["code"] for e in previous_result.get("endmembers",[])]
    rows=[];max_delta=0.;total_delta=0.;n=0
    for sample in result["samples"]:
        previous=old.get(sample["code"])
        if not previous:continue
        before={e:w for e,w in zip(old_members,previous["point_fit"]["weights"])}
        after={e:w for e,w in zip(codes,sample["point_fit"]["weights"])}
        delta={code:after.get(code,0.)-before.get(code,0.) for code in sorted(set(before)|set(after))}
        sample_max=max((abs(v) for v in delta.values()),default=0.)
        max_delta=max(max_delta,sample_max);total_delta+=sum(abs(v) for v in delta.values());n+=len(delta)
        rows.append({"code":sample["code"],"name":sample.get("name",""),"before":before,"after":after,"delta":delta,"max_abs_delta":sample_max})
    return {"endmembers":[{"code":m["code"],"name":m["name"]} for m in _members_payload(members)],
            "previous_endmembers":old_members,
            "removed_codes":[c for c in old_members if c not in codes],
            "added_codes":[c for c in codes if c not in old_members],
            "samples":rows,
            "max_abs_delta":max_delta if rows else None,
            "mean_abs_delta":(total_delta/n) if n else None}

def process_item(db,item_id):
    claimed=db.query(CalibrationItem).filter_by(id=item_id,status=ItemStatus.PENDING).update(
        {"status":ItemStatus.RUNNING,"error":"","updated_at":now()},synchronize_session=False)
    db.commit()
    item=db.query(CalibrationItem).filter_by(id=item_id).with_for_update().first()
    if not item or not claimed:return item
    batch=db.get(CalibrationBatch,item.batch_id);run=db.get(UnmixRun,item.run_id)
    if not batch or not run:
        item.status=ItemStatus.FAILED;item.error="对应研究或批次不存在";item.updated_at=now();db.commit();return item
    if batch.status not in COMPUTABLE:
        item.status=ItemStatus.FAILED;item.error="批次已结束，本次重算不再采用";item.updated_at=now();db.commit();return item
    expected=source_hash(run,batch)
    if expected!=item.source_hash:item.source_hash=expected
    try:
        reusable=db.query(CalibrationItem).filter(CalibrationItem.source_hash==item.source_hash,
            CalibrationItem.status==ItemStatus.COMPLETED,CalibrationItem.id!=item.id,
            CalibrationItem.result_hash!="").order_by(CalibrationItem.id).first()
        if (reusable and reusable.result and reusable.result.get("input_hash")==item.source_hash
                and fingerprint(reusable.result)==reusable.result_hash):
            result=copy.deepcopy(reusable.result);item.reused=True
        else:
            result=analyze_unmix(_run_snapshot(run,batch),run.noise_sigma,run.repeats,run.seed);item.reused=False
        item.result=result;item.result_hash=fingerprint(result)
        item.changes=_changes(_previous_result(db,run),result,batch.endmembers)
        item.status=ItemStatus.COMPLETED;item.error=""
        log(db,"worker","calibration.item.completed",f"{batch.id}:{run.id}")
    except Exception as e:
        item.status=ItemStatus.FAILED;item.error=str(e);item.result={};item.changes={};item.result_hash=""
        log(db,"worker","calibration.item.failed",f"{batch.id}:{run.id}")
    item.updated_at=now();_refresh_batch(db,batch);db.commit();return item

def _item_payload(item,run):
    return {"id":item.id,"batch_id":item.batch_id,"run_id":item.run_id,
        "run_name":run.name if run else "","run_status":run.status if run else "",
        "status":item.status,"reused":bool(item.reused),"source_hash":item.source_hash,
        "result_hash":item.result_hash,"changes":item.changes or {},
        "failure_reason":item.error,"updated_at":item.updated_at}

def _batch_detail(db,batch):
    items=db.query(CalibrationItem).filter_by(batch_id=batch.id).order_by(CalibrationItem.run_id).all()
    run_ids=[i.run_id for i in items]
    runs={r.id:r for r in db.query(UnmixRun).filter(UnmixRun.id.in_(run_ids)).all()} if run_ids else {}
    payload=record(batch)
    payload["progress"]=((batch.item_completed+batch.item_failed)/batch.item_total) if batch.item_total else 1.
    payload["progress_percent"]=round(payload["progress"]*100,1)
    payload["items"]=[_item_payload(i,runs.get(i.run_id)) for i in items]
    payload["failure_items"]=[{"run_id":i.run_id,"run_name":runs[i.run_id].name if i.run_id in runs else "","reason":i.error} for i in items if i.status==ItemStatus.FAILED]
    payload["can_publish"]=batch.status==BatchStatus.READY
    return payload

def list_batches(db,library_id):
    library=db.get(SpectralLibrary,library_id)
    if not library:raise HTTPException(404,"光谱库不存在")
    rows=[]
    for batch in db.query(CalibrationBatch).filter_by(library_id=library_id).order_by(CalibrationBatch.id.desc()):
        payload=_batch_detail(db,batch)
        payload["current_calibration_version"]=library.calibration_version or 1
        payload["stale"]=batch.base_version!=(library.calibration_version or 1) and batch.status in OPEN
        rows.append(payload)
    return rows

def get_batch(db,batch_id):
    batch=db.get(CalibrationBatch,batch_id)
    if not batch:raise HTTPException(404,"校准批次不存在")
    library=db.get(SpectralLibrary,batch.library_id)
    payload=_batch_detail(db,batch)
    payload["current_calibration_version"]=library.calibration_version if library else None
    payload["stale"]=bool(library and batch.base_version!=(library.calibration_version or 1) and batch.status in OPEN)
    return payload

def effective_revision(db,run,lock=False):
    query=db.query(RunRevision).filter_by(run_id=run.id,status=EFFECTIVE)
    if lock:query=query.with_for_update()
    return query.order_by(RunRevision.id.desc()).first()

def original_revision(db,run):
    return db.query(RunRevision).filter_by(run_id=run.id,kind="original").order_by(RunRevision.id).first()

def _snapshot_version(run):
    return run.input_snapshot.get("library",{}).get("calibration_version") or run.calibration_version or 1

def ensure_original_revision(db,run):
    revision=original_revision(db,run)
    if revision:return revision
    if not run.result:return None
    revision=RunRevision(run_id=run.id,batch_id=None,kind="original",library_version=_snapshot_version(run),
        status=EFFECTIVE,source_hash=_original_source_hash(run),
        input_hash=run.result.get("input_hash",""),snapshot=run.input_snapshot,result=run.result,created_at=run.created_at or now())
    db.add(revision);db.flush()
    db.query(ReplicationNote).filter_by(run_id=run.id,result_id=None).update({"result_id":revision.id})
    return revision

def complete_original_run(db,run,result):
    """Idempotent completion of the original worker job; never overrides a newer calibration result."""
    revision=original_revision(db,run)
    if revision:
        if run.status!=RunStatus.COMPLETED:run.status=RunStatus.COMPLETED;run.error=""
        return revision
    active=effective_revision(db,run)
    status=SUPERSEDED if active and active.kind=="calibration" else EFFECTIVE
    revision=RunRevision(run_id=run.id,batch_id=None,kind="original",library_version=_snapshot_version(run),
        status=status,source_hash=_original_source_hash(run),
        input_hash=result.get("input_hash",""),snapshot=run.input_snapshot,result=result)
    db.add(revision);db.flush()
    db.query(ReplicationNote).filter_by(run_id=run.id,result_id=None).update({"result_id":revision.id})
    if status==EFFECTIVE:
        run.result=result;run.calibration_version=revision.library_version
        run.status=RunStatus.COMPLETED;run.error=""
    return revision

def publish_batch(db,batch_id,user):
    pre=db.get(CalibrationBatch,batch_id)
    if not pre:raise HTTPException(404,"校准批次不存在")
    library=db.query(SpectralLibrary).filter_by(id=pre.library_id).with_for_update().first()
    batch=db.query(CalibrationBatch).filter_by(id=batch_id).with_for_update().first()
    if batch.status==BatchStatus.PUBLISHED:raise HTTPException(409,"该批次已切换为现行校准")
    if batch.status==BatchStatus.SUPERSEDED:
        raise HTTPException(409,"批次已被更新的校准取代，不能切换："+(batch.failure_reason or ""))
    if batch.base_version!=(library.calibration_version or 1) and batch.status!=BatchStatus.READY:
        raise HTTPException(409,"批次基于旧校准 v%d，不能切换"%batch.base_version)
    if batch.status==BatchStatus.FAILED:
        raise HTTPException(409,"批次未全部重算成功，不能切换："+(batch.failure_reason or "存在失败研究"))
    if batch.status!=BatchStatus.READY:
        raise HTTPException(409,"批次仍在计算，全部研究重算成功后才能切换")
    items=db.query(CalibrationItem).filter_by(batch_id=batch.id).order_by(CalibrationItem.run_id).with_for_update().all()
    stale=[]
    for item in items:
        if item.status!=ItemStatus.COMPLETED:continue
        run=db.get(UnmixRun,item.run_id)
        if run is None or item.source_hash!=source_hash(run,batch):stale.append(item)
    if stale:
        reasons=[f"研究 #{i.run_id}：来源已变化，需重新计算" for i in stale]
        raise HTTPException(409,"有研究来源在研究参数变更后未重算，不能切换；"+"；".join(reasons)[:1200])
    unfinished=[i for i in items if i.status!=ItemStatus.COMPLETED]
    if unfinished:
        reasons=[f"研究 #{i.run_id}：{i.error or ('仍在计算' if i.status in (ItemStatus.PENDING,ItemStatus.RUNNING) else '未完成')}" for i in unfinished]
        raise HTTPException(409,"全部研究重算成功后才能切换；未完成："+"；".join(reasons)[:1200])
    current=library.calibration_version or 1
    if batch.base_version!=current:
        batch.status=BatchStatus.SUPERSEDED
        batch.failure_reason=f"批次基于校准 v{batch.base_version}，现行已是 v{current}；拒绝倒退现行校准"
        log(db,user.email,"calibration.publish.rejected",batch.id);db.commit()
        raise HTTPException(409,batch.failure_reason)
    version=current+1
    # Atomic compare-and-swap: the first publisher to bump the version wins.
    # A concurrent late batch whose base is now old updates 0 rows and is rejected,
    # so publishing can never move the current calibration backwards.
    claimed=db.query(SpectralLibrary).filter_by(id=library.id,calibration_version=current).update(
        {"calibration_version":version},synchronize_session=False)
    if not claimed:
        db.rollback()
        winner=db.get(SpectralLibrary,library.id)
        batch=db.query(CalibrationBatch).filter_by(id=batch_id).with_for_update().first()
        if batch.status==BatchStatus.PUBLISHED:raise HTTPException(409,"该批次已切换为现行校准")
        batch.status=BatchStatus.SUPERSEDED
        batch.failure_reason=f"切换期间现行校准已更新至 v{winner.calibration_version if winner else '?'}；本批基于 v{batch.base_version}，迟到的完成不回退现行校准"
        log(db,user.email,"calibration.publish.rejected",batch.id);db.commit()
        raise HTTPException(409,batch.failure_reason)
    db.expire(library);db.refresh(library)
    members=_members_payload(batch.endmembers)
    existing={e.code:e for e in db.query(Endmember).filter_by(library_id=library.id).with_for_update().all()}
    codes={m["code"] for m in members}
    for m in members:
        row=existing.get(m["code"])
        if row:row.name=m["name"];row.spectrum=m["spectrum"]
        else:db.add(Endmember(library_id=library.id,code=m["code"],name=m["name"],spectrum=m["spectrum"]))
    for code,row in existing.items():
        if code not in codes:db.delete(row)
    for item in items:
        run=db.query(UnmixRun).filter_by(id=item.run_id).with_for_update().first()
        if not run:continue
        previous=ensure_original_revision(db,run)
        existing_revision=db.query(RunRevision).filter_by(run_id=run.id,batch_id=batch.id).first()
        if existing_revision:continue
        active=effective_revision(db,run)
        item.changes=_changes(active.result if active and active.result else (run.result or {}),item.result or {},batch.endmembers)
        for old in db.query(RunRevision).filter_by(run_id=run.id,status=EFFECTIVE).all():
            if old.batch_id!=batch.id or old.kind!="calibration":old.status=SUPERSEDED
        revision=RunRevision(run_id=run.id,batch_id=batch.id,kind="calibration",library_version=version,
            status=EFFECTIVE,source_hash=item.source_hash,input_hash=(item.result or {}).get("input_hash",""),
            snapshot=_run_snapshot(run,batch),result=item.result or {})
        db.add(revision);db.flush()
        # Keep run.input_snapshot as the run's own creation snapshot: a still-running
        # original job must compute from its frozen input, never from the retested endmembers.
        run.result=item.result or {};run.calibration_version=version
        run.status=RunStatus.COMPLETED;run.error=""
        item.updated_at=now()
    batch.version=version;batch.status=BatchStatus.PUBLISHED;batch.published_at=now();_refresh_batch(db,batch)
    others=db.query(CalibrationBatch).filter(CalibrationBatch.library_id==library.id,
        CalibrationBatch.id!=batch.id,CalibrationBatch.base_version<version,
        CalibrationBatch.status.in_(OPEN)).all()
    for other in others:
        other.status=BatchStatus.SUPERSEDED
        other.failure_reason=f"批次 #{batch.id} 已切换现行校准至 v{version}；本批基于 v{other.base_version}，不再切换"
    log(db,user.email,"calibration.publish",batch.id);db.commit()
    return _batch_detail(db,batch)

def retry_batch(db,batch_id,user):
    pre=db.get(CalibrationBatch,batch_id)
    if not pre:raise HTTPException(404,"校准批次不存在")
    library=db.query(SpectralLibrary).filter_by(id=pre.library_id).with_for_update().first()
    batch=db.query(CalibrationBatch).filter_by(id=batch_id).with_for_update().first()
    if batch.status==BatchStatus.PUBLISHED:raise HTTPException(409,"已切换的批次不能重算")
    if batch.status==BatchStatus.SUPERSEDED:raise HTTPException(409,"批次已被新校准取代，请重新上传复测谱")
    if batch.base_version!=(library.calibration_version or 1):raise HTTPException(409,"批次基于旧校准，不能重算；请重新上传复测谱")
    failed=db.query(CalibrationItem).filter_by(batch_id=batch.id,status=ItemStatus.FAILED).all()
    pending=db.query(CalibrationItem).filter_by(batch_id=batch.id,status=ItemStatus.PENDING).all()
    for item in failed:
        item.status=ItemStatus.PENDING;item.error="";item.result={};item.changes={};item.result_hash="";item.reused=False
    batch.status=BatchStatus.COMPUTING;batch.failure_reason="";_refresh_batch(db,batch)
    log(db,user.email,"calibration.retry",batch.id);db.commit()
    ids=[i.id for i in failed+pending]
    try:
        for item_id in ids:queue_service.enqueue_item(item_id)
    except Exception:raise HTTPException(503,"队列不可用，稍后重试")
    return _batch_detail(db,batch)

def list_revisions(db,run_id):
    run=db.get(UnmixRun,run_id)
    if not run:raise HTTPException(404,"解混运行不存在")
    ensure_original_revision(db,run);db.commit()
    rows=[]
    for revision in db.query(RunRevision).filter_by(run_id=run_id).order_by(RunRevision.id):
        batch=db.get(CalibrationBatch,revision.batch_id) if revision.batch_id else None
        rows.append({"id":revision.id,"run_id":revision.run_id,"batch_id":revision.batch_id,"kind":revision.kind,
            "library_version":revision.library_version,"status":revision.status,"input_hash":revision.input_hash,
            "created_at":revision.created_at,"is_effective":revision.status==EFFECTIVE,
            "endmembers":[{"code":e.get("code"),"name":e.get("name")} for e in (revision.result.get("endmembers") or [])],
            "batch":{"id":batch.id,"title":batch.title,"version":batch.version,"status":batch.status} if batch else None})
    return rows

def revision_result(db,run_id,result_id):
    run=db.query(UnmixRun).filter_by(id=run_id).first()
    if not run:raise HTTPException(404,"解混运行不存在")
    revision=db.query(RunRevision).filter_by(id=result_id,run_id=run_id).first()
    if not revision:raise HTTPException(404,"结果版本不存在")
    payload=record(run);payload["result"]=revision.result
    payload["current_result_id"]=revision.id;payload["current_library_version"]=revision.library_version
    payload["current_result_kind"]=revision.kind;payload["current_batch_id"]=revision.batch_id
    payload["revisions"]=_revision_summaries(db,run_id)
    return payload

def _revision_summaries(db,run_id):
    rows=[]
    for revision in db.query(RunRevision).filter_by(run_id=run_id).order_by(RunRevision.id):
        rows.append({"id":revision.id,"kind":revision.kind,"library_version":revision.library_version,
            "status":revision.status,"batch_id":revision.batch_id,"created_at":revision.created_at,
            "input_hash":revision.input_hash,"is_effective":revision.status==EFFECTIVE})
    return rows

def recover_work(db):
    """Requeue interrupted worker jobs. Idempotent: jobs are keyed by existing rows."""
    runs=db.query(UnmixRun).filter(UnmixRun.status.in_((RunStatus.QUEUED,RunStatus.RUNNING))).all()
    run_ids=[]
    for run in runs:
        if original_revision(db,run):run.status=RunStatus.COMPLETED;continue
        run.status=RunStatus.QUEUED;run_ids.append(run.id)
    for batch in db.query(CalibrationBatch).filter(CalibrationBatch.status.in_(COMPUTABLE)).all():
        library=db.get(SpectralLibrary,batch.library_id)
        if not library or batch.base_version!=(library.calibration_version or 1):
            batch.status=BatchStatus.SUPERSEDED
            batch.failure_reason=f"批次基于 v{batch.base_version}，恢复时现行已是 v{library.calibration_version if library else '?'}；不再切换"
    db.flush()
    for item in db.query(CalibrationItem).filter(CalibrationItem.status.in_((ItemStatus.PENDING,ItemStatus.RUNNING))).all():
        batch=db.get(CalibrationBatch,item.batch_id)
        if not batch or batch.status not in COMPUTABLE:
            item.status=ItemStatus.FAILED;item.error=item.error or "批次已结束，本次重算不再采用"
            continue
        if item.status==ItemStatus.RUNNING:
            item.status=ItemStatus.PENDING;item.updated_at=now()
        _refresh_batch(db,batch)
    item_ids=[i.id for i in db.query(CalibrationItem).filter_by(status=ItemStatus.PENDING).all()]
    run_ids=[r.id for r in db.query(UnmixRun).filter_by(status=RunStatus.QUEUED).all()]
    db.commit()
    for run_id in run_ids:queue_service.enqueue_run(run_id)
    for item_id in item_ids:queue_service.enqueue_item(item_id)
    return len(run_ids),len(item_ids)
