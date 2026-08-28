import { useCallback, useEffect, useRef, useState } from 'react';
import { LogoMark } from '../components/LogoMark';
import { IconBack, IconPencil, IconPlus, IconTrash } from '../components/Icons';
import { UserMenuInline } from '../components/UserMenu';
import { NoteWall } from './NoteWall';
import { PaperPicker } from './PaperPicker';
import { StickyBoard } from './StickyBoard';
import { StudyChat, type PendingTurn } from './StudyChat';
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
 * The desk — a place to work on papers without opening them.
 *
 * Two pages, because there are two kinds of thought here:
 *
 *   'study' — a scope, its chat, and the notes belonging to that conversation
 *   'notes' — the universal board: what outlived the question that produced it
 *
 * ⚠ **Chat notes are scoped to the conversation, not to papers.** Switching
 * study switches the board. A note that should follow the reader everywhere
 * goes on the universal board instead — either of them can put it there, and
 * the assistant can pin to both from any chat.
 *
 * ⚠ **The assistant writes and edits notes; only the reader deletes one.** That
 * is structural, not a rule the model is asked to keep: there is no delete tool
 * and `study_agent` does not import the repository's delete.
 */

let clientSeq = 0;

type NotePatch = { body?: string; color?: StickyColor; pinned?: boolean };
type Board = 'chat' | 'universal';

export function DeskView({
  initialScope,
  page,
  onPageChange,
  onBack,
  onOpenPaper,
}: {
  /** Study id, or `library`. */
  initialScope?: string | null;
  /** Which of the desk's two pages is showing. */
  page: 'study' | 'notes';
  onPageChange: (page: 'study' | 'notes') => void;
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
  const [chatNotes, setChatNotes] = useState<Sticky[]>([]);
  const [wallNotes, setWallNotes] = useState<Sticky[]>([]);
  const [library, setLibrary] = useState<PaperMeta[]>([]);
  const [picking, setPicking] = useState(false);
  const [renaming, setRenaming] = useState(false);
  /** A study just created, whose rename box opens once it has loaded. */
  const [pendingRename, setPendingRename] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [model, setModel] = useState<string>(
    () => { try { return localStorage.getItem('pal:model') || ''; } catch { return ''; } },
  );

  /**
   * Whether the chat's note strip is folded away.
   *
   * Remembered across sessions: a reader who hid it wants it hidden tomorrow,
   * and re-showing it every visit is the kind of small tax that makes a surface
   * feel like it is not listening.
   */
  const [boardHidden, setBoardHidden] = useState<boolean>(() => {
    try { return localStorage.getItem('pal:deskBoardHidden') === '1'; } catch { return false; }
  });
  /**
   * Below 820px the rail (scope/paper picker) is hidden by CSS to give the
   * chat room to breathe — see `.desk-grid` in index.css. With nothing to
   * bring it back, "Whole library" / "In this study" simply stop existing on
   * a phone. This opens it as a slide-over instead.
   */
  const [railOpenMobile, setRailOpenMobile] = useState(false);
  const toggleBoard = useCallback(() => {
    setBoardHidden((v) => {
      const next = !v;
      try { localStorage.setItem('pal:deskBoardHidden', next ? '1' : '0'); } catch { /* blocked */ }
      return next;
    });
  }, []);

  const chooseModel = useCallback((name: string) => {
    setModel(name);
    try { localStorage.setItem('pal:model', name); } catch { /* storage blocked */ }
  }, []);

  const isLibrary = scope === LIBRARY_SCOPE;

  // ── Load ─────────────────────────────────────────────────────────────────
  const refreshStudies = useCallback(async () => {
    try { setStudies(await listStudies()); } catch { /* surfaced by the scope load */ }
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

  // Scope switch: the chat, its papers, and its notes all follow.
  useEffect(() => {
    let alive = true;
    setPending(null);
    (async () => {
      try {
        const [detail, chat, notes] = await Promise.all([
          getStudy(scope),
          getStudyChat(scope),
          listStickies('chat', scope),
        ]);
        if (!alive) return;
        setStudy(detail.study);
        setPapers(detail.papers);
        setTurns(chat);
        setChatNotes(notes);
      } catch (e) {
        if (!alive) return;
        setNotice((e as Error).message || 'Could not load that scope');
        // A study deleted in another tab must not strand the page.
        if (scope !== LIBRARY_SCOPE) setScope(LIBRARY_SCOPE);
      }
    })();
    return () => { alive = false; };
  }, [scope]);

  useEffect(() => {
    if (pendingRename && study?.id === pendingRename) {
      setRenaming(true);
      setPendingRename(null);
    }
  }, [pendingRename, study]);

  const refreshChatNotes = useCallback(async () => {
    try { setChatNotes(await listStickies('chat', scope)); } catch { /* keeps the last */ }
  }, [scope]);

  const refreshWall = useCallback(async () => {
    try { setWallNotes(await listStickies('universal')); } catch { /* keeps the last */ }
  }, []);

  useEffect(() => { void refreshWall(); }, [refreshWall]);

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

      let wrote = false;
      try {
        await askStudyStream(
          scope,
          question,
          {
            onStatus: (message) => patch((p) => ({ ...p, status: message })),
            // ⚠ Upsert by id, and clear the status: every call arrives twice,
            // running then done, and once a fetch is on screen the trail IS the
            // activity indicator.
            onStep: (step) => {
              if (step.tool === 'NOTE' && step.state === 'done') wrote = true;
              patch((p) => ({
                ...p,
                status: null,
                steps: p.steps.some((s) => s.id === step.id)
                  ? p.steps.map((s) => (s.id === step.id ? step : s))
                  : [...p.steps, step],
              }));
            },
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
      } finally {
        // The agent may have pinned something. One answer can reach both
        // boards, so both are refreshed — and only when it actually wrote,
        // because a refresh per question would fight an open editor.
        if (wrote) {
          void refreshChatNotes();
          void refreshWall();
        }
      }
    },
    [scope, model, refreshChatNotes, refreshWall],
  );

  const retry = useCallback(() => {
    const q = pending?.question;
    if (q) void ask(q);
  }, [pending, ask]);

  const clearChat = useCallback(async () => {
    if (!window.confirm('Clear this conversation? Its notes and papers stay.')) return;
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
      const created = await createStudy('Untitled study');
      await refreshStudies();
      setScope(created.id);
      onPageChange('study');
      // Deferred until the scope effect has loaded the new study, so the
      // rename input seeds from the right name. The key above makes this
      // belt-and-braces rather than load-bearing, and both are cheap.
      setPendingRename(created.id);
    } catch (e) {
      setNotice((e as Error).message || 'Could not create the study');
    }
  }, [refreshStudies, onPageChange]);

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
    const ok = window.confirm(
      `Delete "${study.name}"?\n\nIts chat goes with it. The papers stay in your ` +
      `library, and its notes move to the universal board — only you delete a note.`,
    );
    if (!ok) return;
    try {
      await deleteStudy(scope);
      setScope(LIBRARY_SCOPE);
      await Promise.all([refreshStudies(), refreshWall()]);
    } catch (e) {
      setNotice((e as Error).message || 'Delete failed');
    }
  }, [isLibrary, study, scope, refreshStudies, refreshWall]);

  const applyPapers = useCallback(
    async (documentIds: string[]) => {
      setPicking(false);
      if (isLibrary) return;
      try {
        setPapers(await setStudyPapers(scope, documentIds));
        await refreshStudies();
      } catch (e) {
        setNotice((e as Error).message || 'Could not update the study');
      }
    },
    [isLibrary, scope, refreshStudies],
  );

  // ── Notes ────────────────────────────────────────────────────────────────
  const addNote = useCallback(
    async (board: Board) => {
      try {
        const created = await createSticky(
          board === 'chat' ? { body: '', board, scope } : { body: '', board },
        );
        if (board === 'chat') setChatNotes((prev) => [created, ...prev]);
        else setWallNotes((prev) => [created, ...prev]);
      } catch (e) {
        setNotice((e as Error).message || 'Could not add the note');
      }
    },
    [scope],
  );

  const saveNote = useCallback(
    async (board: Board, id: string, patch: NotePatch) => {
      const set = board === 'chat' ? setChatNotes : setWallNotes;
      const previous = board === 'chat' ? chatNotes : wallNotes;
      set((prev) => prev.map((n) => (n.id === id ? ({ ...n, ...patch } as Sticky) : n)));
      try {
        const saved = await updateSticky(id, patch);
        set((prev) => prev.map((n) => (n.id === id ? saved : n)));
      } catch (e) {
        set(previous);
        setNotice((e as Error).message || 'Could not save the note');
      }
    },
    [chatNotes, wallNotes],
  );

  const removeNote = useCallback(
    async (board: Board, id: string) => {
      const set = board === 'chat' ? setChatNotes : setWallNotes;
      const previous = board === 'chat' ? chatNotes : wallNotes;
      set((prev) => prev.filter((n) => n.id !== id));
      try {
        await deleteSticky(id);
      } catch (e) {
        set(previous);
        setNotice((e as Error).message || 'Could not delete the note');
      }
    },
    [chatNotes, wallNotes],
  );

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

        <button
          type="button"
          className="desk-rail-toggle"
          onClick={() => setRailOpenMobile(true)}
          title="Studies and papers"
        >
          Papers
        </button>

        <nav className="desk-tabs">
          <button
            type="button"
            className={`desk-tab${page === 'study' ? ' is-on' : ''}`}
            onClick={() => onPageChange('study')}
          >
            Studies
          </button>
          <button
            type="button"
            className={`desk-tab${page === 'notes' ? ' is-on' : ''}`}
            onClick={() => onPageChange('notes')}
          >
            Notes
            {wallNotes.length > 0 && <span className="marg-badge">{wallNotes.length}</span>}
          </button>
        </nav>

        <span className="desk-bar-sub">
          {page === 'study'
            ? 'read across papers without opening them'
            : 'notes that outlived the question'}
        </span>
        <div style={{ marginLeft: 'auto' }}>
          <UserMenuInline />
        </div>
      </header>

      {notice && (
        <div className="lib-notice desk-notice">
          <span>{notice}</span>
          <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss">×</button>
        </div>
      )}

      {page === 'notes' ? (
        <NoteWall
          notes={wallNotes}
          onCreate={() => void addNote('universal')}
          onSave={(id, patch) => void saveNote('universal', id, patch)}
          onDelete={(id) => void removeNote('universal', id)}
        />
      ) : (
        <div className={`desk-grid${boardHidden ? ' is-board-hidden' : ''}`}>
          {/* Only does anything below 820px (see .rail-backdrop) — closes the
              slide-over on an outside tap, same as tapping a scope does. */}
          <div
            className={`rail-backdrop${railOpenMobile ? ' is-on' : ''}`}
            onClick={() => setRailOpenMobile(false)}
          />
          {/* ── Scopes ── */}
          <nav className={`rail thin-scroll${railOpenMobile ? ' is-mobile-open' : ''}`}>
            <div className="rail-head">
              <h2>Studies</h2>
              <button type="button" className="board-add" onClick={newStudy} title="New study">
                <IconPlus className="w-3 h-3" />
              </button>
            </div>

            <button
              type="button"
              className={`rail-row${isLibrary ? ' is-on' : ''}`}
              onClick={() => { setScope(LIBRARY_SCOPE); setRailOpenMobile(false); }}
            >
              <span className="rail-row-name">Whole library</span>
              <span className="rail-row-count">{library.length}</span>
            </button>

            {studies.map((s) => (
              <button
                key={s.id}
                type="button"
                className={`rail-row${scope === s.id ? ' is-on' : ''}`}
                onClick={() => { setScope(s.id); setRailOpenMobile(false); }}
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
                  className="rail-choose"
                  onClick={() => setPicking(true)}
                  title="Choose which papers this study holds"
                >
                  Choose…
                </button>
              )}
            </div>

            {papers.length === 0 && (
              <p className="marg-hint rail-hint">
                {isLibrary ? 'No finished papers yet.' : 'Empty. Use “Choose…” to pick some.'}
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
              </div>
            ))}

            {!isLibrary && papers.length > 0 && (
              <p className="marg-hint rail-hint rail-order-hint">
                P1, P2… is how answers cite them. Reorder in “Choose…”.
              </p>
            )}

            {!isLibrary && study && (
              <div className="rail-actions">
                {renaming ? (
                  /* ⚠ Keyed on the study id. StudyNameInput seeds its draft from
                     `initial` on mount only, and "create then rename" sets
                     renaming before the new study has loaded — so without the
                     key the input opens holding the PREVIOUS study's name and
                     commits it on blur. That is how three studies all ended up
                     called "Whole library". */
                  <StudyNameInput key={study.id} initial={study.name} onCommit={commitRename} />
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
            notes={chatNotes}
            scopeName={scopeName}
            collapsed={boardHidden}
            onToggle={toggleBoard}
            onCreate={() => void addNote('chat')}
            onSave={(id, patch) => void saveNote('chat', id, patch)}
            onDelete={(id) => void removeNote('chat', id)}
          />
        </div>
      )}

      <PaperPicker
        open={picking && !isLibrary}
        library={library}
        chosen={papers}
        onApply={(ids) => void applyPapers(ids)}
        onClose={() => setPicking(false)}
      />
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
