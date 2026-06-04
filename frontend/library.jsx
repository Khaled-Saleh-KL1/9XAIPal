// Tiny inline SVG icons. Stroke=1.5, currentColor.
const Icon = {
  Search: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  ),
  Plus: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" {...p}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  ),
  Upload: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M12 16V4m0 0-4 4m4-4 4 4" />
      <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
    </svg>
  ),
  Doc: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
    </svg>
  ),
  Pin: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M12 17v5" />
      <path d="M9 3h6l-1 6 3 3v2H7v-2l3-3z" />
    </svg>
  ),
  Sort: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M7 4v16m0 0-3-3m3 3 3-3" />
      <path d="M17 20V4m0 0-3 3m3-3 3 3" />
    </svg>
  ),
  Check: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="m5 12 5 5L20 7" />
    </svg>
  ),
  Send: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  ),
  Arrow: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M5 12h14m-6-6 6 6-6 6" />
    </svg>
  ),
  Back: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M19 12H5m6 6-6-6 6-6" />
    </svg>
  ),
  Grid: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" {...p}>
      <rect x="4" y="4" width="7" height="7" rx="1" />
      <rect x="13" y="4" width="7" height="7" rx="1" />
      <rect x="4" y="13" width="7" height="7" rx="1" />
      <rect x="13" y="13" width="7" height="7" rx="1" />
    </svg>
  ),
  List: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" {...p}>
      <path d="M4 6h16M4 12h16M4 18h16" />
    </svg>
  ),
  Spinner: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" {...p}>
      <circle cx="12" cy="12" r="9" opacity="0.18" />
      <path d="M21 12a9 9 0 0 0-9-9" strokeLinecap="round" />
    </svg>
  ),
};

// ---- Library View ----
function LibraryView({ onOpenPaper, onUpload, onViewPdf, view, setView }) {
  const [over, setOver] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const [sort, setSort] = React.useState("recent");
  const [showRawFiles, setShowRawFiles] = React.useState(false);

  const filtered = React.useMemo(() => {
    let xs = LIBRARY.filter(
      (p) =>
        p.title.toLowerCase().includes(query.toLowerCase()) ||
        p.authors.toLowerCase().includes(query.toLowerCase())
    );
    if (sort === "title") xs = [...xs].sort((a, b) => a.title.localeCompare(b.title));
    if (sort === "pages") xs = [...xs].sort((a, b) => b.pages - a.pages);
    return xs;
  }, [query, sort]);

  const onDrop = (e) => {
    e.preventDefault();
    setOver(false);
    onUpload();
  };

  return (
    <div className="h-screen flex flex-col surface overflow-hidden" data-screen-label="01 Library">

      {/* ── Fixed top bar ── */}
      <header className="border-b border-app shrink-0">
        <div className="max-w-[1240px] mx-auto px-8 h-14 flex items-center gap-6">
          <div className="flex items-center gap-2.5">
            <LogoMark />
            <span className="text-[14px] font-medium tracking-tight">9XAIPal</span>
            <span className="text-[11px] muted font-mono ml-1 px-1.5 py-0.5 rounded surface-2 border border-app">local</span>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <span className="text-[12px] muted">{LIBRARY.length} papers · 1.2 GB on disk</span>
            <span className="mx-2 h-4 w-px" style={{ background: "var(--border)" }} />
            <button
              onClick={() => setShowRawFiles(true)}
              className="flex items-center gap-1.5 text-[12px] px-2.5 py-1.5 rounded-md transition-colors"
              style={{
                color: "var(--fg-2)",
                background: "var(--bg-2)",
                border: "1px solid var(--border)",
              }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = "var(--border-strong)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = "var(--border)"; }}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
                <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
              </svg>
              Raw files
            </button>
            <span className="mx-1 h-4 w-px" style={{ background: "var(--border)" }} />
            <button className="text-[12px] muted">Settings</button>
          </div>
        </div>
      </header>

      {/* ── Fixed upper area: hero + dropzone + controls ── */}
      <div className="shrink-0 border-b border-app">
        <div className="max-w-[1240px] mx-auto px-8 pt-9 pb-5">
          {/* hero row */}
          <div className="flex items-baseline justify-between mb-7">
            <div>
              <h1 className="font-serif text-[38px] leading-[1.05] tracking-[-0.018em] fg">Your library.</h1>
              <p className="muted text-[13.5px] mt-1 max-w-[44ch]">
                Every paper indexed, chunked and embedded on this machine. Nothing leaves.
              </p>
            </div>
            <div className="hidden md:flex items-center gap-1 muted text-[12px]">
              <kbd className="kbd">⌘</kbd><kbd className="kbd">K</kbd>
              <span className="ml-1">to search</span>
            </div>
          </div>

          {/* dropzone */}
          <div
            onDragOver={(e) => { e.preventDefault(); setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={onDrop}
            onClick={onUpload}
            className={`dropzone ${over ? "is-over" : ""} cursor-pointer rounded-xl px-7 py-5 flex items-center gap-6`}
            style={{ background: over ? undefined : "var(--bg-2)" }}
          >
            <div className="w-10 h-10 rounded-full flex items-center justify-center surface border border-app shrink-0">
              <Icon.Upload className="w-4 h-4 fg-2" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="font-serif text-[18px] tracking-tight fg">Drop a PDF to begin.</div>
              <div className="muted text-[12px] mt-0.5">
                Extraction, VLM enhancement, and embedding run entirely on-device.
              </div>
            </div>
            <div className="hidden sm:flex flex-col items-end gap-1.5 shrink-0">
              <div className="text-[10.5px] font-mono muted">PDF · ≤ 80 MB · no upload</div>
              <button
                onClick={(e) => { e.stopPropagation(); onUpload(); }}
                className="bg-accent text-[12.5px] px-3 py-1.5 rounded-md flex items-center gap-1.5"
              >
                <Icon.Plus className="w-3.5 h-3.5" /> Add paper
              </button>
            </div>
          </div>

          {/* controls */}
          <div className="mt-4 flex items-center gap-3">
            <div className="relative flex-1 max-w-[380px]">
              <Icon.Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 muted" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search title, author, tag…"
                className="w-full pl-8 pr-3 py-2 rounded-md surface-2 border border-app text-[12.5px] placeholder:faint"
                style={{ color: "var(--fg)" }}
              />
            </div>
            <div className="flex items-center gap-1 ml-auto">
              <button
                onClick={() => setSort(sort === "recent" ? "title" : sort === "title" ? "pages" : "recent")}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[12px] muted hover:fg"
              >
                <Icon.Sort className="w-3.5 h-3.5" />
                <span>Sort · {sort}</span>
              </button>
              <div className="flex items-center surface-2 border border-app rounded-md p-0.5 ml-1">
                <button
                  onClick={() => setView("grid")}
                  className={`p-1.5 rounded ${view === "grid" ? "fg" : "muted"}`}
                  style={view === "grid" ? { background: "var(--bg)" } : undefined}
                >
                  <Icon.Grid className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => setView("list")}
                  className={`p-1.5 rounded ${view === "list" ? "fg" : "muted"}`}
                  style={view === "list" ? { background: "var(--bg)" } : undefined}
                >
                  <Icon.List className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ── Raw Files Drawer ── */}
      <RawFilesDrawer
        open={showRawFiles}
        onClose={() => setShowRawFiles(false)}
        onViewPdf={(paper) => { setShowRawFiles(false); onViewPdf(paper); }}
      />

      {/* ── Scrollable papers area ── */}
      <main className="flex-1 min-h-0 overflow-y-auto thin-scroll">
        <div className="max-w-[1240px] mx-auto px-8 py-5 pb-8">
          {view === "grid" ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {filtered.map((p) => (
                <PaperCard key={p.id} paper={p} onOpen={() => onOpenPaper(p)} />
              ))}
            </div>
          ) : (
            <div className="rounded-xl border border-app surface-2 divide-app overflow-hidden">
              {filtered.map((p) => (
                <PaperRow key={p.id} paper={p} onOpen={() => onOpenPaper(p)} />
              ))}
            </div>
          )}
          {filtered.length === 0 && (
            <div className="text-center muted text-[13px] py-16">No papers match "{query}".</div>
          )}
        </div>
      </main>
    </div>
  );
}

function LogoMark() {
  return (
    <div
      className="w-6 h-6 rounded-md flex items-center justify-center"
      style={{ background: "var(--fg)", color: "var(--bg)" }}
    >
      <span className="font-serif text-[13px] leading-none" style={{ fontWeight: 500 }}>9</span>
    </div>
  );
}

function PaperCard({ paper, onOpen }) {
  return (
    <button
      onClick={onOpen}
      className="text-left rounded-xl border border-app surface-2 p-5 hover:border-app-strong transition-colors group"
    >
      <div className="flex items-start justify-between">
        <div className="w-9 h-11 rounded-sm border border-app flex items-center justify-center surface">
          <Icon.Doc className="w-4 h-4 muted" />
        </div>
        {paper.pinned && <Icon.Pin className="w-3.5 h-3.5 muted" />}
      </div>
      <div className="mt-4 font-serif text-[17px] leading-[1.25] tracking-tight fg group-hover:accent">
        {paper.title}
      </div>
      <div className="mt-1.5 text-[12px] muted">{paper.authors} · {paper.venue}</div>
      <div className="mt-4 flex items-center gap-3 text-[11px] font-mono muted">
        <span>{paper.pages}p</span>
        <span className="opacity-40">·</span>
        <span>{paper.added}</span>
        <span className="ml-auto flex items-center gap-1 flex-wrap">
          {paper.tags.map((t) => (
            <span key={t} className="px-1.5 py-0.5 rounded surface border border-app">{t}</span>
          ))}
        </span>
      </div>
      <div className="mt-4 h-px w-full" style={{ background: "var(--border)" }} />
      <div className="mt-3 flex items-center gap-3">
        <div className="flex-1 h-[3px] rounded-full overflow-hidden" style={{ background: "var(--bg-3)" }}>
          <div
            className="h-full"
            style={{
              width: `${paper.progress * 100}%`,
              background: paper.progress === 1 ? "var(--ok)" : "var(--accent)",
            }}
          />
        </div>
        <span className="text-[10.5px] font-mono muted tabular-nums">
          {paper.progress === 1 ? "read" : `${Math.round(paper.progress * 100)}%`}
        </span>
      </div>
    </button>
  );
}

function PaperRow({ paper, onOpen }) {
  return (
    <button
      onClick={onOpen}
      className="w-full text-left px-5 py-3.5 flex items-center gap-5 hover:bg-[var(--bg-3)] transition-colors"
    >
      <Icon.Doc className="w-4 h-4 muted shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="font-serif text-[15.5px] leading-tight tracking-tight fg truncate">{paper.title}</div>
        <div className="text-[11.5px] muted mt-0.5 truncate">{paper.authors} · {paper.venue}</div>
      </div>
      <div className="hidden md:flex items-center gap-1.5">
        {paper.tags.map((t) => (
          <span key={t} className="text-[10.5px] font-mono muted px-1.5 py-0.5 rounded surface-2 border border-app">{t}</span>
        ))}
      </div>
      <div className="text-[11px] font-mono muted tabular-nums w-10 text-right">{paper.pages}p</div>
      <div className="w-20 flex items-center gap-2">
        <div className="flex-1 h-[3px] rounded-full overflow-hidden" style={{ background: "var(--bg-3)" }}>
          <div
            className="h-full"
            style={{
              width: `${paper.progress * 100}%`,
              background: paper.progress === 1 ? "var(--ok)" : "var(--accent)",
            }}
          />
        </div>
        <span className="text-[10.5px] font-mono muted tabular-nums w-6 text-right">
          {paper.progress === 1 ? "✓" : `${Math.round(paper.progress * 100)}%`}
        </span>
      </div>
      <div className="text-[10.5px] font-mono muted w-14 text-right">{paper.added}</div>
    </button>
  );
}

Object.assign(window, { LibraryView, Icon, LogoMark });
