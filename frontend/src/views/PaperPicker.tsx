import { useEffect, useMemo, useRef, useState } from 'react';
import { PaperCover } from './PaperCover';
import { displayTitle } from '../lib/titles';
import type { PaperMeta, StudyPaper } from '../api';

/**
 * Choosing which papers a study holds — and in which order.
 *
 * ⚠ **Order is not decoration here.** Answers cite papers as P1/P2/P3, and the
 * number comes from this list's order. So the dialog has two halves: what is in
 * the study, in order and re-orderable, and what else the library holds. A flat
 * checkbox list hid both facts — you could not see the numbering at all, and
 * you could not tell the chosen from the unchosen without reading every box.
 *
 * ⚠ **Covers, not titles alone.** The reason the library grew thumbnails
 * applies twice over here: a study is assembled by recognising papers, and half
 * of these are still called `2607.24653v2`.
 */
export function PaperPicker({
  open,
  library,
  chosen,
  onApply,
  onClose,
}: {
  open: boolean;
  library: PaperMeta[];
  /** Current members, in citation order. */
  chosen: StudyPaper[];
  onApply: (documentIds: string[]) => void;
  onClose: () => void;
}) {
  // Held locally and applied on Done: membership is written whole-collection,
  // so a live write per checkbox would be one request per click and would
  // renumber the study under the reader mid-edit.
  const [ids, setIds] = useState<string[]>([]);
  const [query, setQuery] = useState('');
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setIds(chosen.map((p) => p.id));
    setQuery('');
    searchRef.current?.focus({ preventScroll: true });
  }, [open, chosen]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const byId = useMemo(
    () => new Map(library.map((m) => [m.id, m])),
    [library],
  );

  const available = useMemo(() => {
    const q = query.trim().toLowerCase();
    return library.filter(
      (m) => !ids.includes(m.id) && (!q || displayTitle(m).toLowerCase().includes(q)),
    );
  }, [library, ids, query]);

  if (!open) return null;

  const add = (id: string) => setIds((prev) => [...prev, id]);
  const remove = (id: string) => setIds((prev) => prev.filter((x) => x !== id));
  const move = (index: number, delta: number) =>
    setIds((prev) => {
      const next = [...prev];
      const to = index + delta;
      if (to < 0 || to >= next.length) return prev;
      [next[index], next[to]] = [next[to], next[index]];
      return next;
    });

  const dirty =
    ids.length !== chosen.length || ids.some((id, i) => chosen[i]?.id !== id);

  return (
    <div className="picker-scrim" onClick={onClose}>
      <div
        className="picker"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Choose the papers in this study"
      >
        <header className="picker-head">
          <div>
            <h2>Papers in this study</h2>
            <p className="picker-sub">
              The order is the numbering answers cite: P1 is the first here.
            </p>
          </div>
          <button type="button" className="marg-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <div className="picker-body">
          {/* ── Chosen, in order ── */}
          <section className="picker-col">
            <div className="picker-col-head">
              <h3>In the study</h3>
              <span className="picker-count">{ids.length}</span>
              {ids.length > 0 && (
                <button type="button" className="picker-clear" onClick={() => setIds([])}>
                  Remove all
                </button>
              )}
            </div>

            {ids.length === 0 ? (
              <p className="marg-hint picker-hint">
                Nothing yet. Add papers from the right: a study of one is fine,
                and two is where the assistant starts comparing.
              </p>
            ) : (
              <ol className="picker-chosen">
                {ids.map((id, i) => {
                  const meta = byId.get(id);
                  return (
                    <li key={id} className="picker-row is-chosen">
                      <span className="picker-num">P{i + 1}</span>
                      {meta && (
                        <PaperCover
                          paperId={id}
                          title={displayTitle(meta)}
                          ready
                          className="is-thumb"
                        />
                      )}
                      <span className="picker-name">
                        {meta ? displayTitle(meta) : 'A paper no longer in the library'}
                      </span>
                      <span className="picker-order">
                        <button
                          type="button"
                          onClick={() => move(i, -1)}
                          disabled={i === 0}
                          title="Move up"
                          aria-label={`Move ${meta ? displayTitle(meta) : 'this paper'} up`}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          onClick={() => move(i, 1)}
                          disabled={i === ids.length - 1}
                          title="Move down"
                          aria-label={`Move ${meta ? displayTitle(meta) : 'this paper'} down`}
                        >
                          ↓
                        </button>
                      </span>
                      <button
                        type="button"
                        className="picker-drop"
                        onClick={() => remove(id)}
                        title="Remove from the study"
                        aria-label="Remove from the study"
                      >
                        ×
                      </button>
                    </li>
                  );
                })}
              </ol>
            )}
          </section>

          {/* ── The rest of the library ── */}
          <section className="picker-col">
            <div className="picker-col-head">
              <h3>Your library</h3>
              <span className="picker-count">{available.length}</span>
              {available.length > 0 && (
                <button
                  type="button"
                  className="picker-clear"
                  onClick={() => setIds((prev) => [...prev, ...available.map((m) => m.id)])}
                >
                  Add all
                </button>
              )}
            </div>

            <input
              ref={searchRef}
              className="picker-search"
              value={query}
              placeholder="Find a paper…"
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Find a paper"
            />

            {available.length === 0 ? (
              <p className="marg-hint picker-hint">
                {library.length === 0
                  ? 'Your library is empty.'
                  : query
                  ? 'Nothing matches.'
                  : 'Every paper is already in this study.'}
              </p>
            ) : (
              <ul className="picker-available">
                {available.map((m) => (
                  <li key={m.id}>
                    <button type="button" className="picker-row" onClick={() => add(m.id)}>
                      <PaperCover
                        paperId={m.id}
                        title={displayTitle(m)}
                        ready={m.status === 'complete'}
                        className="is-thumb"
                      />
                      <span className="picker-name">
                        {displayTitle(m)}
                        {m.status !== 'complete' && (
                          <span className="picker-warn"> · still processing</span>
                        )}
                      </span>
                      <span className="picker-plus" aria-hidden="true">+</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        <footer className="picker-foot">
          <span className="picker-foot-note">
            {dirty ? 'Not saved yet.' : 'Up to date.'}
          </span>
          <button type="button" className="picker-cancel" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="chat-send"
            onClick={() => onApply(ids)}
            disabled={!dirty}
          >
            Save
          </button>
        </footer>
      </div>
    </div>
  );
}
