import { useState } from 'react';
import { resolveReferenceStream, addReferenceToLibrary, getPaperProgress, type ReferenceEntry, type ResolveQueueState } from '../api';
import type { ReferenceIndex } from '../lib/references';

interface RowState {
  entry: ReferenceEntry;
  resolving: boolean;
  /** Set while the lookup is waiting its turn in the shared 1 req/s line;
   * null once it fires (or when there was no wait). */
  queue: ResolveQueueState | null;
  adding: boolean;
  error: string | null;
  addResult: { id: string; status: string; already_existed: boolean } | null;
  progress: { status: string; job_status?: string | null } | null;
}

/**
 * A "[12]" (or "[5, 2, 35]") marker from the paper's OWN bibliography,
 * expandable in place — distinct from CitationRef.tsx, which resolves an
 * AI answer's own `[[P2:41]]` citation into a block this app already has.
 * This one resolves the paper's citation into a DIFFERENT paper the app may
 * not have yet, via Semantic Scholar, and can queue it for ingestion.
 *
 * Reuses the .cite-wrap/.cite-chip/.cite-peek shell CitationRef established
 * (same click-to-toggle interaction, same visual language) — only the peek
 * body differs, since each number in a multi-citation bracket is its own
 * independent lookup + add.
 */
export function BibCitationRef({
  paperId,
  numbers,
  refIndex,
  onOpenPaper,
}: {
  paperId: string;
  numbers: number[];
  refIndex: ReferenceIndex;
  /** Switches the reader to a different paper — undefined only when the
   * reader shell hasn't wired one in, in which case "Open" simply doesn't
   * render rather than being a dead button. */
  onOpenPaper?: (documentId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  // Seeded once from refIndex at mount. Safe: a BibCitationRef only ever
  // exists because remarkCitationRefs already required every number in
  // `numbers` to be in refIndex.numbers, and refIndex.byNumber is built from
  // the exact same fetch — so every number here has an entry from the start.
  const [rows, setRows] = useState<Map<number, RowState>>(() => {
    const m = new Map<number, RowState>();
    for (const n of numbers) {
      const entry = refIndex.byNumber.get(n);
      if (entry) m.set(n, { entry, resolving: false, queue: null, adding: false, error: null, addResult: null, progress: null });
    }
    return m;
  });

  const patchRow = (n: number, patch: Partial<RowState>) =>
    setRows((prev) => {
      const cur = prev.get(n);
      if (!cur) return prev;
      const next = new Map(prev);
      next.set(n, { ...cur, ...patch });
      return next;
    });

  const resolveOne = async (n: number) => {
    const row = rows.get(n);
    // 'resolved' and 'no_match' are final (see resolve_reference's own
    // docstring); 'unavailable' and the initial 'pending' both mean "worth
    // trying" — the same distinction the backend makes about what to cache.
    if (!row || row.resolving || row.entry.resolve_status === 'resolved' || row.entry.resolve_status === 'no_match') return;
    patchRow(n, { resolving: true, queue: null, error: null });
    try {
      const updated = await resolveReferenceStream(paperId, n, (queue) => patchRow(n, { queue }));
      patchRow(n, { resolving: false, queue: null, entry: updated });
    } catch (e) {
      patchRow(n, { resolving: false, queue: null, error: (e as Error).message || 'Could not resolve this reference' });
    }
  };

  const toggle = () => {
    if (open) { setOpen(false); return; }
    setOpen(true);
    for (const n of numbers) resolveOne(n);
  };

  // Local to this chip, not the app-wide upload overlay: adding a cited
  // paper is a background action taken while still reading THIS paper, not
  // a navigation away from it (see BibCitationRef's own design note in the
  // implementation plan on why handleArticleImport/submitImportUrl in
  // App.tsx are the wrong thing to reuse here).
  const pollAdded = (n: number, docId: string) => {
    const tick = async () => {
      try {
        const p = await getPaperProgress(docId);
        patchRow(n, { progress: { status: p.status, job_status: p.job_status } });
        if (p.status !== 'complete' && p.status !== 'failed') setTimeout(tick, 2000);
      } catch {
        // Stop polling silently. The document still exists and finishes (or
        // fails) server-side either way; the reader can check the library.
      }
    };
    tick();
  };

  const addOne = async (n: number) => {
    const row = rows.get(n);
    if (!row || row.adding) return;
    patchRow(n, { adding: true, error: null });
    try {
      const result = await addReferenceToLibrary(paperId, n);
      patchRow(n, { adding: false, addResult: result });
      if (!result.already_existed && result.status !== 'failed') pollAdded(n, result.id);
    } catch (e) {
      patchRow(n, { adding: false, error: (e as Error).message || 'Could not add this reference' });
    }
  };

  return (
    <span className="cite-wrap">
      <button
        type="button"
        className={`cite-chip${open ? ' is-open' : ''}`}
        onClick={toggle}
        title={numbers.length === 1 ? `Reference [${numbers[0]}]` : `References [${numbers.join(', ')}]`}
      >
        [{numbers.join(', ')}]
      </button>
      {open && (
        <span className="cite-peek bib-ref-peek">
          {[...rows.entries()].map(([n, row]) => (
            <BibRefRow key={n} number={n} row={row} onResolve={() => resolveOne(n)} onAdd={() => addOne(n)} onOpenPaper={onOpenPaper} />
          ))}
        </span>
      )}
    </span>
  );
}

function BibRefRow({
  number, row, onResolve, onAdd, onOpenPaper,
}: {
  number: number;
  row: RowState;
  onResolve: () => void;
  onAdd: () => void;
  onOpenPaper?: (documentId: string) => void;
}) {
  const { entry } = row;
  const alreadyThere = entry.already_in_library || row.addResult?.already_existed;
  const openId = entry.existing_document_id || row.addResult?.id;

  return (
    <span className="bib-ref-row">
      <span className="bib-ref-num">[{number}]</span>
      <span className="bib-ref-body">
        <span className="cite-peek-muted">{entry.raw_text}</span>
        {row.resolving && (
          row.queue ? (
            // Semantic Scholar allows the whole box one lookup per second, so
            // simultaneous readers are served in order. Say so, with the
            // place in line, rather than leaving a spinner that looks stuck.
            <span className="bib-ref-status bib-ref-queued">
              In the queue — {row.queue.position === 1 ? 'next up' : `#${row.queue.position}`}, the link will open shortly…
            </span>
          ) : (
            <span className="bib-ref-status">Looking it up…</span>
          )
        )}
        {row.error && <span className="cite-peek-error">{row.error}</span>}
        {entry.resolve_status === 'no_match' && !row.resolving && (
          <span className="bib-ref-status">
            No confident match. Search for it:{' '}
            <ManualSearchLinks query={entry.search_query || entry.raw_text} />
          </span>
        )}
        {entry.resolve_status === 'unavailable' && !row.resolving && (
          <span className="bib-ref-status">
            Resolution unavailable right now.{' '}
            <button type="button" className="bib-ref-retry" onClick={onResolve}>Retry</button>
          </span>
        )}
        {entry.resolve_status === 'resolved' && (
          <span className="bib-ref-resolved">
            → {entry.resolved_title}
            {entry.resolved_authors ? ` — ${entry.resolved_authors}` : ''}
            {entry.resolved_year ? ` (${entry.resolved_year})` : ''}
            {entry.resolved_pdf_url && (
              <a
                className="bib-ref-link"
                href={entry.resolved_pdf_url}
                target="_blank" rel="noopener noreferrer"
                title="Open the open-access PDF Semantic Scholar found"
              >
                PDF ↗
              </a>
            )}
            {/* A match with nothing to fetch (no open-access PDF and no
                arXiv id) cannot be added — say so and hand over the landing
                pages, instead of an "Add to library" that always refuses. */}
            {!entry.resolved_pdf_url && !alreadyThere && (
              <span className="bib-ref-status">
                No open-access PDF to add.{' '}
                {entry.s2_url && (
                  <a href={entry.s2_url} target="_blank" rel="noopener noreferrer">Semantic Scholar ↗</a>
                )}
                {entry.s2_url && ' · '}
                <ManualSearchLinks query={entry.search_query || entry.resolved_title || entry.raw_text} />
              </span>
            )}
            {alreadyThere ? (
              <span className="bib-ref-status">
                Already in your library.
                {openId && onOpenPaper && (
                  <button type="button" className="bib-ref-open" onClick={() => onOpenPaper(openId)}>Open →</button>
                )}
              </span>
            ) : row.addResult ? (
              <span className="bib-ref-status">
                {row.progress?.status === 'complete'
                  ? 'Added.'
                  : row.progress?.status === 'failed'
                    ? 'Failed to process — check the library for details.'
                    : `${row.progress?.job_status || row.addResult.status}…`}
                {row.progress?.status === 'complete' && onOpenPaper && (
                  <button type="button" className="bib-ref-open" onClick={() => onOpenPaper(row.addResult!.id)}>Open →</button>
                )}
              </span>
            ) : entry.resolved_pdf_url ? (
              <button type="button" className="bib-ref-add" onClick={onAdd} disabled={row.adding}>
                {row.adding ? 'Adding…' : 'Add to library'}
              </button>
            ) : null}
          </span>
        )}
      </span>
    </span>
  );
}

/** Google Scholar first — it is the search that finds a paper from a bare
 *  title nearly every time — then Semantic Scholar. Both get the title, never
 *  the whole citation: the site search answered "No Papers Found" to that. */
function ManualSearchLinks({ query }: { query: string }) {
  const q = encodeURIComponent(query);
  return (
    <>
      <a href={`https://scholar.google.com/scholar?q=${q}`} target="_blank" rel="noopener noreferrer">Google Scholar ↗</a>
      {' · '}
      <a href={`https://www.semanticscholar.org/search?q=${q}`} target="_blank" rel="noopener noreferrer">Semantic Scholar ↗</a>
    </>
  );
}
