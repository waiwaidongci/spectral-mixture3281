from enum import StrEnum
class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
class BatchStatus(StrEnum):
    COMPUTING = "computing"
    READY = "ready"
    PUBLISHED = "published"
    FAILED = "failed"
    SUPERSEDED = "superseded"
class ItemStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
