/**
 * Fine-grained ingestion stage → progress fraction, shared by the library
 * grid, deep-linked paper loads, and the upload overlay.
 *
 * This used to be copy-pasted in three places, and they drifted: the overlay
 * had its own evenly-spaced calculation keyed off `kind` (paper vs book)
 * that assumed a paper is done at "chunking", while the backend actually
 * decides completion purely from INGEST_PROFILE (see
 * backend/app/extraction/pipeline_sync.py::_is_fast_ingest), so on any
 * deployment running the "full" profile, a paper goes through embedding and
 * summarizing just like a book, and the overlay's steps would show "done"
 * while the library card (which used this same map) still showed 78%.
 * One shared map removes the possibility of the two disagreeing again.
 */
export const STAGE_PROGRESS: Record<string, number> = {
  queued: 0.06,
  extracting: 0.3,
  chunking: 0.55,
  embedding: 0.78,
  summarizing: 0.92,
  complete: 1,
  failed: 0,
};

/**
 * `status` is the document's coarse status (or an already-merged
 * job_status/status stage string); `jobStatus`, when passed separately, is
 * preferred since it is more fine-grained.
 *
 * `subFraction` (0-1), when given, is real progress *within* the extracting
 * stage (pages extracted / total pages, see ingestion_jobs.progress_fraction)
 * and is blended into the queued→extracting band instead of the flat 0.3, so
 * a long extraction actually moves instead of sitting still for minutes.
 */
export function stageProgress(
  status: string | null | undefined,
  jobStatus?: string | null,
  subFraction?: number | null,
): number {
  if (status === 'complete') return 1;
  const stage = (jobStatus || status || '').toLowerCase();
  if (stage === 'extracting' && typeof subFraction === 'number' && Number.isFinite(subFraction)) {
    const lo = STAGE_PROGRESS.queued;
    const hi = STAGE_PROGRESS.extracting;
    const clamped = Math.max(0, Math.min(1, subFraction));
    return lo + (hi - lo) * clamped;
  }
  return STAGE_PROGRESS[stage] ?? 0.08;
}
