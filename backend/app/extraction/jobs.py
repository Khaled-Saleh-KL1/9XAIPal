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
    # CHUNKING -> SUMMARIZING is the paper-only skip path: when the embedding
    # pass is skipped, the pipeline dispatches generate_section_summaries
    # directly, so the job never passes through EMBEDDING.
    # CHUNKING -> COMPLETE is the fast-ingest path (INGEST_PROFILE=fast): for a
    # paper, extraction IS the pipeline — nothing is dispatched afterwards, so
    # the job terminates the moment the chunks are persisted.
    JobStatus.CHUNKING: [
        JobStatus.EMBEDDING, JobStatus.SUMMARIZING, JobStatus.COMPLETE, JobStatus.FAILED,
    ],
    JobStatus.EMBEDDING: [JobStatus.SUMMARIZING, JobStatus.COMPLETE, JobStatus.FAILED],
    JobStatus.SUMMARIZING: [JobStatus.COMPLETE, JobStatus.FAILED],
    JobStatus.COMPLETE: [],
    JobStatus.FAILED: [JobStatus.QUEUED],  # Allow retry
}


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    return target in TRANSITIONS.get(current, [])

