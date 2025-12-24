from __future__ import annotations

import enum

if hasattr(enum, "StrEnum"):
    StrEnum = enum.StrEnum
else:

    class StrEnum(str, enum.Enum):
        pass


class ArtifactKind(StrEnum):
    TEXT = "text"
    THUMB = "thumb"
    TEXT_VEC = "text_vec"
    IMG_VEC = "img_vec"
    BM25 = "bm25"


class ArtifactStatus(StrEnum):
    MISSING = "missing"
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    SKIPPED = "skipped"
    ERROR = "error"
    CANCELLED = "cancelled"


class TaskKind(StrEnum):
    TEXT = "text"
    PDF = "pdf"
    THUMB = "thumb"
    BM25 = "bm25"
    TEXT_VEC = "text_vec"
    IMG_VEC = "img_vec"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    ERROR = "error"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class JobStatus(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    RUNNING = "running"
    PAUSED = "paused"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
