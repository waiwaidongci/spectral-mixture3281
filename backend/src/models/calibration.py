from sqlalchemy import String,Text,JSON,ForeignKey,UniqueConstraint,Integer,Boolean
from sqlalchemy.orm import Mapped,mapped_column
from ..config.database import Base
from ..utils.clock import now

class CalibrationBatch(Base):
    __tablename__="calibration_batches"
    __table_args__=(UniqueConstraint("library_id","version"),)
    id:Mapped[int]=mapped_column(primary_key=True)
    library_id:Mapped[int]=mapped_column(ForeignKey("spectral_libraries.id"))
    title:Mapped[str]=mapped_column(String(160),default="")
    version:Mapped[int|None]=mapped_column(Integer,nullable=True,default=None)
    base_version:Mapped[int]=mapped_column(Integer,default=1)
    kind:Mapped[str]=mapped_column(String(24),default="calibration")
    status:Mapped[str]=mapped_column(String(24),default="computing")
    endmembers:Mapped[list]=mapped_column(JSON,default=list)
    endmember_hash:Mapped[str]=mapped_column(String(64),default="")
    item_total:Mapped[int]=mapped_column(Integer,default=0)
    item_completed:Mapped[int]=mapped_column(Integer,default=0)
    item_failed:Mapped[int]=mapped_column(Integer,default=0)
    failure_reason:Mapped[str]=mapped_column(Text,default="")
    created_by:Mapped[str]=mapped_column(String(100),default="")
    created_at:Mapped[str]=mapped_column(String(64),default=now)
    published_at:Mapped[str]=mapped_column(String(64),default="")

class CalibrationItem(Base):
    __tablename__="calibration_items"
    __table_args__=(UniqueConstraint("batch_id","run_id"),)
    id:Mapped[int]=mapped_column(primary_key=True)
    batch_id:Mapped[int]=mapped_column(ForeignKey("calibration_batches.id"))
    run_id:Mapped[int]=mapped_column(ForeignKey("unmix_runs.id"))
    status:Mapped[str]=mapped_column(String(24),default="pending")
    source_hash:Mapped[str]=mapped_column(String(64),default="")
    result_hash:Mapped[str]=mapped_column(String(64),default="")
    result:Mapped[dict]=mapped_column(JSON,default=dict)
    changes:Mapped[dict]=mapped_column(JSON,default=dict)
    reused:Mapped[bool]=mapped_column(Boolean,default=False)
    error:Mapped[str]=mapped_column(Text,default="")
    created_at:Mapped[str]=mapped_column(String(64),default=now)
    updated_at:Mapped[str]=mapped_column(String(64),default=now)

class RunRevision(Base):
    __tablename__="run_revisions"
    __table_args__=(UniqueConstraint("batch_id","run_id"),)
    id:Mapped[int]=mapped_column(primary_key=True)
    run_id:Mapped[int]=mapped_column(ForeignKey("unmix_runs.id"))
    batch_id:Mapped[int|None]=mapped_column(ForeignKey("calibration_batches.id"),nullable=True,default=None)
    kind:Mapped[str]=mapped_column(String(24),default="calibration")
    library_version:Mapped[int]=mapped_column(Integer,default=1)
    status:Mapped[str]=mapped_column(String(24),default="effective")
    source_hash:Mapped[str]=mapped_column(String(64),default="")
    input_hash:Mapped[str]=mapped_column(String(64),default="")
    snapshot:Mapped[dict]=mapped_column(JSON,default=dict)
    result:Mapped[dict]=mapped_column(JSON,default=dict)
    created_at:Mapped[str]=mapped_column(String(64),default=now)
