"""Ingestion job states and transitions."""

from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    SUMMARIZING = "summarizing"   # High-quality pre-computed section summaries (quality-first personal mode)
    COMPLETE = "complete"
    FAILED = "failed"


# Valid state transitions
TRANSITIONS: dict[JobStatus, list[JobStatus]] = {
    JobStatus.QUEUED: [JobStatus.EXTRACTING, JobStatus.FAILED],
    JobStatus.EXTRACTING: [JobStatus.CHUNKING, JobStatus.FAILED],
    JobStatus.CHUNKING: [JobStatus.EMBEDDING, JobStatus.FAILED],
    JobStatus.EMBEDDING: [JobStatus.SUMMARIZING, JobStatus.COMPLETE, JobStatus.FAILED],
    JobStatus.SUMMARIZING: [JobStatus.COMPLETE, JobStatus.FAILED],
    JobStatus.COMPLETE: [],
    JobStatus.FAILED: [JobStatus.QUEUED],  # Allow retry
}


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    return target in TRANSITIONS.get(current, [])

