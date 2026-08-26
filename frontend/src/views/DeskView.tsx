import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { LogoMark } from '../components/LogoMark';
import { IconBack, IconPencil, IconPlus, IconTrash } from '../components/Icons';
import { StickyBoard } from './StickyBoard';
import { StudyChat, type PendingTurn } from './StudyChat';
import { displayTitle } from '../lib/titles';
import { createPacer } from '../lib/pacer';
import {
  LIBRARY_SCOPE,
  askStudyStream,
  clearStudyChat,
  createStudy,
  createSticky,
  deleteSticky,
  deleteStudy,
  getStudy,
  getStudyChat,
  listPapers,
  listStickies,
  listStudies,
  listModels,
  renameStudy,
  setStudyPapers,
  updateSticky,
  type ModelCatalog,
  type PaperMeta,
  type Sticky,
  type StickyColor,
  type Study,
  type StudyPaper,
  type StudyTurn,
} from '../api';

/**
 * The desk — a page, not a panel.
 *
 * ⚠ **It is a place to work on papers without opening them.** The reader asked
 * for a surface that serves reading "without seeing them": the chat answers
 * from a scope, every citation expands in place, and the papers themselves stay
 * shut unless you choose otherwise. That is why this is a route rather than an
 * overlay on the reader — an overlay implies the document underneath is the
 * subject, and here it is not.
 *
 * Three columns, each answering a different question:
 *   left   — what am I working on?      (studies, and the papers in one)
 *   centre — what do they say?          (the chat)
 *   right  — what do I think?           (sticky notes)
 */

let clientSeq = 0;

export function DeskView({
  initialScope,
  onBack,
  onOpenPaper,
}: {
  /** Study id, or `library`. */
  initialScope?: string | null;
  onBack: () => void;
  /** Open a paper in the reader, optionally at a block. */
  onOpenPaper: (documentId: string, sequenceId?: number) => void;
}) {
  const [studies, setStudies] = useState<Study[]>([]);
  const [scope, setScope] = useState<string>(initialScope || LIBRARY_SCOPE);
  const [study, setStudy] = useState<Study | null>(null);
  const [papers, setPapers] = useState<StudyPaper[]>([]);
  const [turns, setTurns] = useState<StudyTurn[]>([]);
  const [pending, setPending] = useState<PendingTurn | null>(null);
  const [stickies, setStickies] = useState<Sticky[]>([]);
  const [library, setLibrary] = useState<PaperMeta[]>([]);
  const [picking, setPicking] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [model, setModel] = useState<string>(
    () => { try { return localStorage.getItem('pal:model') || ''; } catch { return ''; } },
  );

  const chooseModel = useCallback((name: string) => {
    setModel(name);
    try { localStorage.setItem('pal:model', name); } catch { /* storage blocked */ }
  }, []);

  const isLibrary = scope === LIBRARY_SCOPE;

  // ── Load ─────────────────────────────────────────────────────────────────
  const refreshStudies = useCallback(async () => {
    try { setStudies(await listStudies()); } catch { /* shown by the scope load */ }
  }, []);

  useEffect(() => { void refreshStudies(); }, [refreshStudies]);
  useEffect(() => { listPapers().then(setLibrary).catch(() => {}); }, []);
  useEffect(() => {
    listModels()
      .then((c) => {
        setCatalog(c);
        // Re-validate the remembered model: one can vanish from Ollama between
        // sessions, and a stale name silently fails at generation time.
        setModel((m) => (c.models.some((x) => x.name === m) ? m : c.default));
      })
      .catch(() => {});
  }, []);

  // Scope switch: everything below is derived from it, so it all reloads.
  useEffect(() => {
    let alive = true;
    setPending(null);
    (async () => {
      try {
        const [detail, chat] = await Promise.all([getStudy(scope), getStudyChat(scope)]);
        if (!alive) return;
        setStudy(detail.study);
        setPapers(detail.papers);
        setTurns(chat);
      } catch (e) {
        if (!alive) return;
        setNotice((e as Error).message || 'Could not load that scope');
        // A study deleted in another tab must not strand the page.
        if (scope !== LIBRARY_SCOPE) setScope(LIBRARY_SCOPE);
      }
    })();
    return () => { alive = false; };
  }, [scope]);

  // Stickies follow the scope's papers, plus every unscoped note.
  const paperIds = useMemo(() => papers.map((p) => p.id), [papers]);
  const refreshStickies = useCallback(async () => {
    try {
      setStickies(await listStickies(isLibrary ? undefined : paperIds));
    } catch { /* the board renders empty */ }
  }, [isLibrary, paperIds]);
  useEffect(() => { void refreshStickies(); }, [refreshStickies]);

  // ── Asking ───────────────────────────────────────────────────────────────
  const ask = useCallback(
    async (question: string) => {
      const draft: PendingTurn = {
        clientId: `p-${++clientSeq}`,
        question,
        answer: '',
        status: null,
        steps: [],
        error: null,
      };
      setPending(draft);
      const patch = (fn: (p: PendingTurn) => PendingTurn) =>
        setPending((p) => (p && p.clientId === draft.clientId ? fn(p) : p));

      // Tokens arrive in bursts; the pacer paints them at a readable rate.
      const pacer = createPacer((revealed) =>
        patch((p) => ({ ...p, answer: revealed, status: null })),
      );

      try {
        await askStudyStream(
          scope,
          question,
          {
            onStatus: (message) => patch((p) => ({ ...p, status: message })),
            // ⚠ Upsert by id: every call arrives twice, running then done.
            // ⚠ And clear the status line. Once a fetch is on screen the trail
            // IS the activity indicator, and the phase message that preceded it
            // ("Reading the index of 3 papers…") would otherwise sit under four
            // finished rows still claiming to be what is happening now. The
            // next real phase — "Writing the answer…" — sets it again.
            onStep: (step) =>
              patch((p) => ({
                ...p,
                status: null,
                steps: p.steps.some((s) => s.id === step.id)
                  ? p.steps.map((s) => (s.id === step.id ? step : s))
                  : [...p.steps, step],
              })),
            onToken: (text) => pacer.push(text),
          },
          model,
        );
        await pacer.finish();
        // Refetch rather than splicing: the server owns the turn's final shape
        // (citations resolved against the study's current membership).
        setTurns(await getStudyChat(scope));
        setPending(null);
      } catch (e) {
        pacer.cancel();
        patch((p) => ({ ...p, error: (e as Error).message || 'Could not answer that.' }));
      }
    },
    [scope, model],
  );

  const retry = useCallback(() => {
    const q = pending?.question;
    if (q) void ask(q);
  }, [pending, ask]);

  const clearChat = useCallback(async () => {
    if (!window.confirm('Clear this conversation? The papers and notes stay.')) return;
    try {
      await clearStudyChat(scope);
      setTurns([]);
    } catch (e) {
      setNotice((e as Error).message || 'Could not clear the chat');
    }
  }, [scope]);

  // ── Studies ──────────────────────────────────────────────────────────────
  const newStudy = useCallback(async () => {
    try {
      const created = await createStudy('New study');
      await refreshStudies();
      setScope(created.id);
      setRenaming(true);
    } catch (e) {
      setNotice((e as Error).message || 'Could not create the study');
    }
  }, [refreshStudies]);

  const commitRename = useCallback(
    async (name: string) => {
      setRenaming(false);
      const clean = name.trim();
      if (!study || isLibrary || !clean || clean === study.name) return;
      setStudy({ ...study, name: clean });
      try {
        await renameStudy(scope, clean);
        await refreshStudies();
      } catch (e) {
        setNotice((e as Error).message || 'Rename failed');
      }
    },
    [study, isLibrary, scope, refreshStudies],
  );

  const removeStudy = useCallback(async () => {
    if (isLibrary || !study) return;
    if (!window.confirm(`Delete "${study.name}"? Its chat goes with it. The papers stay in your library.`)) return;
    try {
      await deleteStudy(scope);
      setScope(LIBRARY_SCOPE);
      await refreshStudies();
    } catch (e) {
      setNotice((e as Error).message || 'Delete failed');
    }
  }, [isLibrary, study, scope, refreshStudies]);

  const togglePaper = useCallback(
    async (documentId: string) => {
      if (isLibrary) return;
      const next = paperIds.includes(documentId)
        ? paperIds.filter((id) => id !== documentId)
        : [...paperIds, documentId];
      try {
        setPapers(await setStudyPapers(scope, next));
        await refreshStudies();
      } catch (e) {
        setNotice((e as Error).message || 'Could not update the study');
      }
    },
    [isLibrary, paperIds, scope, refreshStudies],
  );

  // ── Stickies ─────────────────────────────────────────────────────────────
  const addSticky = useCallback(async () => {
    try {
      // A new note starts unscoped even inside a study: scoping is a decision,
      // and pre-filling it would quietly hide the note from every other desk.
      const created = await createSticky({ body: '' });
      setStickies((prev) => [created, ...prev]);
    } catch (e) {
      setNotice((e as Error).message || 'Could not add the note');
    }
  }, []);

  const saveSticky = useCallback(
    async (id: string, patch: { body?: string; color?: StickyColor; pinned?: boolean; document_ids?: string[] }) => {
      const previous = stickies;
      setStickies((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } as Sticky : s)));
      try {
        const saved = await updateSticky(id, patch);
        setStickies((prev) => prev.map((s) => (s.id === id ? saved : s)));
      } catch (e) {
        setStickies(previous);
        setNotice((e as Error).message || 'Could not save the note');
      }
    },
    [stickies],
  );

  const removeSticky = useCallback(async (id: string) => {
    const previous = stickies;
    setStickies((prev) => prev.filter((s) => s.id !== id));
    try {
      await deleteSticky(id);
    } catch (e) {
      setStickies(previous);
      setNotice((e as Error).message || 'Could not delete the note');
    }
  }, [stickies]);

  const scopeName = study?.name || 'Whole library';

  return (
    <div className="desk">
      <header className="desk-bar">
        <button className="reader-back" onClick={onBack}>
          <IconBack className="w-3.5 h-3.5" />
          <span>Library</span>
        </button>
        <span className="reader-sep" />
        <LogoMark />
        <span className="desk-bar-title">Desk</span>
        <span className="desk-bar-sub">
          read across papers without opening them
        </span>
      </header>

      {notice && (
        <div className="lib-notice desk-notice">
          <span>{notice}</span>
          <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss">×</button>
        </div>
      )}

      <div className="desk-grid">
        {/* ── Scopes ── */}
        <nav className="rail thin-scroll">
          <div className="rail-head">
            <h2>Studies</h2>
            <button type="button" className="board-add" onClick={newStudy} title="New study">
              <IconPlus className="w-3 h-3" />
            </button>
          </div>

          <button
            type="button"
            className={`rail-row${isLibrary ? ' is-on' : ''}`}
            onClick={() => setScope(LIBRARY_SCOPE)}
          >
            <span className="rail-row-name">Whole library</span>
            <span className="rail-row-count">{library.length}</span>
          </button>

          {studies.map((s) => (
            <button
              key={s.id}
              type="button"
              className={`rail-row${scope === s.id ? ' is-on' : ''}`}
              onClick={() => setScope(s.id)}
            >
              <span className="rail-row-name">{s.name}</span>
              <span className="rail-row-count">{s.paper_count}</span>
            </button>
          ))}

          <div className="rail-head rail-head-papers">
            <h2>{isLibrary ? 'Every paper' : 'In this study'}</h2>
            {!isLibrary && (
              <button
                type="button"
                className="board-add"
                onClick={() => setPicking((v) => !v)}
                title="Choose papers"
              >
                <IconPlus className="w-3 h-3" />
              </button>
            )}
          </div>

          {!isLibrary && picking && (
            <div className="rail-picker">
              {library.map((m) => (
                <label key={m.id} className="sticky-scope-row">
                  <input
                    type="checkbox"
                    checked={paperIds.includes(m.id)}
                    onChange={() => void togglePaper(m.id)}
                  />
                  <span>{displayTitle(m)}</span>
                </label>
              ))}
              {library.length === 0 && <p className="marg-hint">Your library is empty.</p>}
            </div>
          )}

          {papers.length === 0 && !picking && (
            <p className="marg-hint rail-hint">
              {isLibrary
                ? 'No finished papers yet.'
                : 'No papers yet — use + to choose some.'}
            </p>
          )}

          {papers.map((p) => (
            <div key={p.id} className="rail-paper">
              <button
                type="button"
                className="rail-paper-open"
                onClick={() => onOpenPaper(p.id)}
                title="Open in the reader"
              >
                <span className="rail-paper-num">P{p.paper}</span>
                <span className="rail-paper-name">{p.title}</span>
              </button>
              {!isLibrary && (
                <button
                  type="button"
                  className="rail-paper-drop"
                  onClick={() => void togglePaper(p.id)}
                  title="Remove from this study"
                  aria-label="Remove from this study"
                >
                  ×
                </button>
              )}
            </div>
          ))}

          {!isLibrary && study && (
            <div className="rail-actions">
              {renaming ? (
                <StudyNameInput initial={study.name} onCommit={commitRename} />
              ) : (
                <button type="button" onClick={() => setRenaming(true)}>
                  <IconPencil className="w-3 h-3" /> Rename
                </button>
              )}
              <button type="button" className="is-danger" onClick={removeStudy}>
                <IconTrash className="w-3 h-3" /> Delete
              </button>
            </div>
          )}
        </nav>

        <StudyChat
          scopeName={scopeName}
          papers={papers}
          turns={turns}
          pending={pending}
          onAsk={ask}
          onRetry={retry}
          onClear={clearChat}
          onOpenPaper={(documentId, sequenceId) => onOpenPaper(documentId, sequenceId)}
          catalog={catalog}
          model={model}
          onModelChange={chooseModel}
        />

        <StickyBoard
          stickies={stickies}
          papers={papers}
          onCreate={addSticky}
          onSave={saveSticky}
          onDelete={removeSticky}
        />
      </div>
    </div>
  );
}

function StudyNameInput({
  initial,
  onCommit,
}: {
  initial: string;
  onCommit: (name: string) => void;
}) {
  const [draft, setDraft] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { ref.current?.focus(); ref.current?.select(); }, []);
  return (
    <input
      ref={ref}
      className="rail-rename"
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => onCommit(draft)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') { e.preventDefault(); onCommit(draft); }
        if (e.key === 'Escape') { e.preventDefault(); onCommit(initial); }
      }}
      aria-label="Study name"
    />
  );
}
