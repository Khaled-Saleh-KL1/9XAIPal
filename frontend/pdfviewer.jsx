// ── PDF Viewer ──────────────────────────────────────────────────────────────
// Renders a convincing multi-page academic PDF viewer for any paper in the library.

const PDF_PAGE_W = 680;   // px at 100% zoom
const PDF_PAGE_H = 960;   // px at 100% zoom (A4-ish portrait)

const ZOOM_LEVELS = [0.5, 0.75, 1.0, 1.25, 1.5];

function PdfViewer({ paper, onBack }) {
  const [page, setPage] = React.useState(1);
  const [zoom, setZoom] = React.useState(1.0);
  const [inputPage, setInputPage] = React.useState("1");
  const scrollRef = React.useRef(null);
  const totalPages = paper.pages;

  const goTo = (p) => {
    const clamped = Math.max(1, Math.min(totalPages, p));
    setPage(clamped);
    setInputPage(String(clamped));
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  };

  const zoomIn = () => {
    const idx = ZOOM_LEVELS.indexOf(zoom);
    if (idx < ZOOM_LEVELS.length - 1) setZoom(ZOOM_LEVELS[idx + 1]);
  };
  const zoomOut = () => {
    const idx = ZOOM_LEVELS.indexOf(zoom);
    if (idx > 0) setZoom(ZOOM_LEVELS[idx - 1]);
  };

  // keyboard nav
  React.useEffect(() => {
    const handler = (e) => {
      if (e.key === "ArrowRight" || e.key === "ArrowDown") goTo(page + 1);
      if (e.key === "ArrowLeft"  || e.key === "ArrowUp")   goTo(page - 1);
      if (e.key === "+" || e.key === "=") zoomIn();
      if (e.key === "-") zoomOut();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [page, zoom]);

  const scaledW = PDF_PAGE_W * zoom;
  const scaledH = PDF_PAGE_H * zoom;

  return (
    <div
      className="h-screen flex flex-col overflow-hidden"
      style={{ background: "var(--bg)" }}
      data-screen-label="04 PDF Viewer"
    >
      {/* ── Toolbar ── */}
      <header
        className="shrink-0 h-12 px-4 flex items-center gap-3"
        style={{ borderBottom: "1px solid var(--border)", background: "var(--bg)" }}
      >
        {/* back */}
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 px-2 py-1.5 rounded text-[12.5px]"
          style={{ color: "var(--muted)" }}
          onMouseEnter={(e) => (e.currentTarget.style.color = "var(--fg)")}
          onMouseLeave={(e) => (e.currentTarget.style.color = "var(--muted)")}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
            <path d="M19 12H5m6 6-6-6 6-6" />
          </svg>
          Library
        </button>

        <span className="h-4 w-px" style={{ background: "var(--border)" }} />

        {/* file name */}
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4 shrink-0" style={{ color: "var(--muted)" }}>
            <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" />
          </svg>
          <span className="text-[13px] font-medium truncate" style={{ color: "var(--fg)" }}>
            {paper.title.slice(0, 48)}{paper.title.length > 48 ? "…" : ""}.pdf
          </span>
          <span className="text-[11px] font-mono shrink-0" style={{ color: "var(--muted)" }}>
            {paper.venue} · {paper.pages}p
          </span>
        </div>

        {/* page navigation */}
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => goTo(page - 1)}
            disabled={page === 1}
            className="w-7 h-7 rounded flex items-center justify-center"
            style={{
              color: page === 1 ? "var(--faint)" : "var(--muted)",
              background: "var(--bg-2)",
              border: "1px solid var(--border)",
            }}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
              <path d="M15 18l-6-6 6-6" />
            </svg>
          </button>
          <div className="flex items-center gap-1 text-[12px]" style={{ color: "var(--fg)" }}>
            <input
              value={inputPage}
              onChange={(e) => setInputPage(e.target.value)}
              onBlur={() => goTo(parseInt(inputPage, 10) || page)}
              onKeyDown={(e) => e.key === "Enter" && goTo(parseInt(inputPage, 10) || page)}
              className="w-9 text-center rounded px-1 py-0.5 text-[12px] font-mono"
              style={{
                background: "var(--bg-2)",
                border: "1px solid var(--border)",
                color: "var(--fg)",
                outline: "none",
              }}
            />
            <span style={{ color: "var(--muted)" }}>/</span>
            <span className="font-mono w-6 text-center" style={{ color: "var(--muted)" }}>{totalPages}</span>
          </div>
          <button
            onClick={() => goTo(page + 1)}
            disabled={page === totalPages}
            className="w-7 h-7 rounded flex items-center justify-center"
            style={{
              color: page === totalPages ? "var(--faint)" : "var(--muted)",
              background: "var(--bg-2)",
              border: "1px solid var(--border)",
            }}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
              <path d="M9 18l6-6-6-6" />
            </svg>
          </button>
        </div>

        <span className="h-4 w-px" style={{ background: "var(--border)" }} />

        {/* zoom */}
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={zoomOut}
            disabled={zoom === ZOOM_LEVELS[0]}
            className="w-7 h-7 rounded flex items-center justify-center text-[16px] font-light"
            style={{
              color: zoom === ZOOM_LEVELS[0] ? "var(--faint)" : "var(--muted)",
              background: "var(--bg-2)",
              border: "1px solid var(--border)",
            }}
          >−</button>
          <span
            className="text-[11px] font-mono w-10 text-center"
            style={{ color: "var(--fg)" }}
          >
            {Math.round(zoom * 100)}%
          </span>
          <button
            onClick={zoomIn}
            disabled={zoom === ZOOM_LEVELS[ZOOM_LEVELS.length - 1]}
            className="w-7 h-7 rounded flex items-center justify-center text-[16px] font-light"
            style={{
              color: zoom === ZOOM_LEVELS[ZOOM_LEVELS.length - 1] ? "var(--faint)" : "var(--muted)",
              background: "var(--bg-2)",
              border: "1px solid var(--border)",
            }}
          >+</button>
        </div>

        <span className="h-4 w-px" style={{ background: "var(--border)" }} />

        {/* open in structured reader */}
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded text-[12px]"
          style={{ background: "var(--accent)", color: "var(--accent-fg)" }}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="w-3 h-3">
            <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" /><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
          </svg>
          Read structured
        </button>
      </header>

      {/* ── Page canvas ── */}
      <div
        ref={scrollRef}
        className="flex-1 min-h-0 overflow-auto thin-scroll flex items-start justify-center py-10"
        style={{ background: "oklch(0.30 0.008 70)" }}
      >
        <div
          style={{
            width: scaledW,
            height: scaledH,
            flexShrink: 0,
            position: "relative",
          }}
        >
          <PdfPage paper={paper} pageNum={page} totalPages={totalPages} zoom={zoom} />
        </div>
      </div>

      {/* ── Bottom page strip ── */}
      <div
        className="shrink-0 h-10 flex items-center justify-center gap-2"
        style={{ borderTop: "1px solid var(--border)", background: "var(--bg-2)" }}
      >
        <div className="flex items-center gap-1">
          {Array.from({ length: Math.min(totalPages, 12) }).map((_, i) => (
            <button
              key={i}
              onClick={() => goTo(i + 1)}
              className="w-1.5 h-1.5 rounded-full transition-all"
              style={{
                background: page === i + 1 ? "var(--accent)" : "var(--border-strong)",
                transform: page === i + 1 ? "scale(1.4)" : "scale(1)",
              }}
            />
          ))}
          {totalPages > 12 && (
            <span className="text-[10px] font-mono ml-1" style={{ color: "var(--muted)" }}>
              +{totalPages - 12} more
            </span>
          )}
        </div>
        <span className="text-[10.5px] font-mono" style={{ color: "var(--muted)" }}>
          <kbd className="kbd">←</kbd><kbd className="kbd">→</kbd> navigate · <kbd className="kbd">+</kbd><kbd className="kbd">−</kbd> zoom
        </span>
      </div>
    </div>
  );
}

// ── PDF Page renderer ─────────────────────────────────────────────────────────

function PdfPage({ paper, pageNum, totalPages, zoom }) {
  const w = PDF_PAGE_W * zoom;
  const h = PDF_PAGE_H * zoom;
  const scale = zoom;

  return (
    <div
      style={{
        width: w,
        height: h,
        background: "#ffffff",
        boxShadow: "0 4px 32px rgba(0,0,0,0.5), 0 1px 4px rgba(0,0,0,0.3)",
        position: "relative",
        overflow: "hidden",
      }}
    >
      {/* scaled inner content */}
      <div
        style={{
          transform: `scale(${scale})`,
          transformOrigin: "top left",
          width: PDF_PAGE_W,
          height: PDF_PAGE_H,
          padding: "56px 64px",
          boxSizing: "border-box",
          fontFamily: "Georgia, 'Times New Roman', serif",
          color: "#1a1a1a",
        }}
      >
        {pageNum === 1 && <TitlePage paper={paper} />}
        {pageNum === 2 && <AbstractPage paper={paper} />}
        {pageNum >= 3 && pageNum < totalPages && <BodyPage paper={paper} pageNum={pageNum} />}
        {pageNum === totalPages && <ReferencesPage paper={paper} />}

        {/* page number footer */}
        <div style={{
          position: "absolute",
          bottom: 28,
          left: 0,
          right: 0,
          textAlign: "center",
          fontSize: 11,
          color: "#888",
          fontFamily: "Georgia, serif",
        }}>
          {pageNum}
        </div>
      </div>
    </div>
  );
}

// ── Page templates ────────────────────────────────────────────────────────────

function TitlePage({ paper }) {
  return (
    <div style={{ paddingTop: 32 }}>
      {/* venue badge */}
      <div style={{ fontSize: 11, color: "#888", letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 32 }}>
        {paper.venue}
      </div>
      {/* title */}
      <div style={{ fontSize: 26, lineHeight: 1.25, fontWeight: "bold", color: "#111", marginBottom: 24, letterSpacing: "-0.01em" }}>
        {paper.title}
      </div>
      {/* authors */}
      <div style={{ fontSize: 14, color: "#444", marginBottom: 8 }}>
        {paper.authors.split(", ").join("¹,  ")}¹
      </div>
      <div style={{ fontSize: 11, color: "#888", marginBottom: 40 }}>
        ¹ Research Institute for Machine Intelligence · {paper.venue.split(" ")[0]}
      </div>
      {/* divider */}
      <div style={{ height: 1, background: "#e0e0e0", marginBottom: 36 }} />
      {/* abstract */}
      <div style={{ fontSize: 12, fontWeight: "bold", textTransform: "uppercase", letterSpacing: "0.06em", color: "#555", marginBottom: 12 }}>
        Abstract
      </div>
      <div style={{ fontSize: 13.5, lineHeight: 1.65, color: "#222", textAlign: "justify", marginBottom: 20 }}>
        We present a systematic investigation into structured document parsing for long-form academic texts.
        Our approach decomposes research papers into semantically coherent structural chunks — individual
        paragraphs, display equations, and captioned figures — and indexes each unit independently in a
        local vector store. This formulation yields a {paper.pages}-page treatment of {paper.tags.join(", ")},
        with empirical evaluation on standard benchmarks demonstrating consistent improvements over
        sliding-window baselines.
      </div>
      <div style={{ fontSize: 13.5, lineHeight: 1.65, color: "#222", textAlign: "justify" }}>
        Our key insight is that the atomic unit of comprehension for a human reader is not a fixed-length
        token window but a structural boundary — and that these boundaries are reliably recoverable from
        PDF layout signals without end-to-end training. The resulting system processes documents entirely
        on-device, with no network dependency at inference time.
      </div>
      {/* tags */}
      <div style={{ marginTop: 28, display: "flex", gap: 8, flexWrap: "wrap" }}>
        {paper.tags.map((t) => (
          <span key={t} style={{ fontSize: 11, padding: "3px 8px", border: "1px solid #ccc", borderRadius: 3, color: "#555" }}>
            {t}
          </span>
        ))}
      </div>
    </div>
  );
}

function AbstractPage({ paper }) {
  return (
    <div>
      <SectionHeader number="1" title="Introduction" />
      <BodyText>
        The intersection of {paper.tags[0] || "machine learning"} and document understanding has attracted
        considerable attention in recent years. Prior work has largely treated retrieval as a secondary
        concern — a preprocessing step before a more sophisticated reasoning module. We argue that this
        ordering reflects a fundamental misunderstanding of the problem structure.
      </BodyText>
      <BodyText>
        A research paper is not a bag of sentences. It is a hierarchically structured artifact with
        explicit semantic boundaries: sections, subsections, paragraphs, equations, figures. A reader
        navigates these boundaries deliberately, and an intelligent reading assistant should do the same.
        We formalize this intuition and demonstrate that it leads to measurable improvements.
      </BodyText>

      <SectionHeader number="2" title="Related Work" />
      <BodyText>
        <strong>Document chunking.</strong> Early retrieval-augmented generation systems (Lewis et al., 2020;
        Guu et al., 2020) segment documents by fixed token count. Subsequent work (Shi et al., 2023) has
        shown that this strategy underperforms on documents with heterogeneous content — precisely the
        situation that obtains in academic papers.
      </BodyText>
      <BodyText>
        <strong>Layout-aware parsing.</strong> LayoutLM (Xu et al., 2020) and its successors incorporate
        spatial signals from PDFs, but are trained end-to-end on labeled corpora. Our approach requires
        no labeled data — structural boundaries are inferred from heuristic layout rules that generalize
        across venues and formatting styles.
      </BodyText>

      <TwoColFigure label="Table 1" caption={`Benchmark results across three datasets. ${paper.title.split(" ").slice(0, 4).join(" ")} consistently outperforms fixed-window baselines.`} />
    </div>
  );
}

function BodyPage({ paper, pageNum }) {
  const sections = [
    { num: "3", title: "Method" },
    { num: "3.1", title: "Structural Boundary Detection" },
    { num: "3.2", title: "Retrieval Objective" },
    { num: "4", title: "Experiments" },
    { num: "4.1", title: "Experimental Setup" },
    { num: "4.2", title: "Main Results" },
    { num: "5", title: "Analysis" },
    { num: "6", title: "Ablation Study" },
  ];
  const s = sections[(pageNum - 3) % sections.length];

  return (
    <div>
      <SectionHeader number={s.num} title={s.title} />
      <BodyText>
        We adopt a pipeline architecture consisting of three sequential modules: a boundary detector
        operating on the raw PDF coordinate stream, a chunk classifier that assigns a structural type
        (paragraph, equation, figure, heading) to each recovered unit, and a dense retrieval model
        that embeds each chunk independently.
      </BodyText>
      <BodyText>
        The boundary detector applies a cascade of geometric heuristics. Horizontal gaps exceeding
        1.4× the median inter-line spacing are treated as paragraph breaks; vertical coordinate
        resets signal column boundaries; font-size discontinuities signal heading levels.
        This procedure recovers {">"}96% of manually annotated boundaries on our held-out evaluation set.
      </BodyText>

      {pageNum % 2 === 0 && (
        <>
          <MathBlock
            label={`(${pageNum - 1})`}
            content="ℒ(θ) = − Σ log p_θ(cᵢ | qᵢ) + λ · Ω(θ)"
            caption={`Objective function for the ${s.title.toLowerCase()} module, where Ω(θ) is an L₂ regularizer.`}
          />
          <BodyText>
            The temperature hyperparameter λ is set to 0.07 following Wu et al. (2018). We found this
            value to be robust across all evaluation domains; sensitivity analysis in §6 confirms
            that performance degrades gracefully for λ ∈ [0.03, 0.15].
          </BodyText>
        </>
      )}

      {pageNum % 3 === 0 && (
        <FigurePlaceholder
          label={`Figure ${pageNum - 1}`}
          caption={`Qualitative results for ${paper.tags[0] || "the proposed"} approach on a representative document from the evaluation corpus.`}
        />
      )}

      <BodyText>
        Table {pageNum - 2} summarizes our main experimental results. Our method outperforms the
        strongest baseline by {(pageNum * 1.7 + 2.3).toFixed(1)} points on the primary metric,
        with improvements that are consistent across all three evaluation domains. The gains are
        most pronounced on documents with dense mathematical content — a regime where fixed-window
        segmentation is most likely to split equations across chunk boundaries.
      </BodyText>
    </div>
  );
}

function ReferencesPage({ paper }) {
  const refs = [
    { key: "Guu et al., 2020", title: "REALM: Retrieval-Augmented Language Model Pre-Training.", venue: "ICML 2020." },
    { key: "Karpukhin et al., 2020", title: "Dense Passage Retrieval for Open-Domain Question Answering.", venue: "EMNLP 2020." },
    { key: "Lewis et al., 2020", title: "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks.", venue: "NeurIPS 2020." },
    { key: "Shi et al., 2023", title: "REPLUG: Retrieval-Augmented Black-Box Language Models.", venue: "NAACL 2023." },
    { key: "Wu et al., 2018", title: "Unsupervised Feature Learning via Non-Parametric Instance Discrimination.", venue: "CVPR 2018." },
    { key: "Xu et al., 2020", title: "LayoutLM: Pre-training of Text and Layout for Document Image Understanding.", venue: "KDD 2020." },
    { key: `${paper.authors.split(",")[0]} et al., 2024`, title: `Advances in ${paper.tags[0] || "document understanding"} for scientific literature.`, venue: `Workshop at ${paper.venue}.` },
  ];

  return (
    <div>
      <SectionHeader number="" title="References" />
      <div style={{ columns: 1 }}>
        {refs.map((r, i) => (
          <div key={i} style={{ marginBottom: 12, fontSize: 12, lineHeight: 1.5, color: "#333", paddingLeft: 20, textIndent: -20 }}>
            <span style={{ color: "#555" }}>{r.key}. </span>
            {r.title} <em style={{ color: "#777" }}>{r.venue}</em>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Mini components for page content ─────────────────────────────────────────

function SectionHeader({ number, title }) {
  return (
    <div style={{ fontSize: 15, fontWeight: "bold", color: "#111", marginBottom: 10, marginTop: 20 }}>
      {number && <span style={{ marginRight: 6 }}>{number}</span>}{title}
    </div>
  );
}

function BodyText({ children }) {
  return (
    <p style={{ fontSize: 13, lineHeight: 1.65, color: "#222", textAlign: "justify", marginBottom: 11 }}>
      {children}
    </p>
  );
}

function MathBlock({ content, label, caption }) {
  return (
    <div style={{ margin: "18px 0", padding: "14px 20px", background: "#f8f8f8", borderLeft: "3px solid #e0e0e0", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
      <div style={{ fontFamily: "Georgia, serif", fontSize: 14, fontStyle: "italic", color: "#1a1a1a" }}>
        {content}
      </div>
      <div style={{ fontSize: 12, color: "#888" }}>{label}</div>
    </div>
  );
}

function FigurePlaceholder({ label, caption }) {
  return (
    <div style={{ margin: "18px 0" }}>
      <div style={{
        height: 120,
        background: "repeating-linear-gradient(135deg, #f0f0f0, #f0f0f0 8px, #e8e8e8 8px, #e8e8e8 16px)",
        borderRadius: 4,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 11,
        color: "#aaa",
        fontFamily: "monospace",
      }}>
        {label} · placeholder
      </div>
      <div style={{ fontSize: 11.5, color: "#666", marginTop: 8, lineHeight: 1.5 }}>
        <strong>{label}.</strong> {caption}
      </div>
    </div>
  );
}

function TwoColFigure({ label, caption }) {
  return (
    <div style={{ margin: "18px 0" }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        {[0, 1].map((i) => (
          <div key={i} style={{
            height: 80,
            background: "repeating-linear-gradient(135deg, #f4f4f4, #f4f4f4 6px, #ececec 6px, #ececec 12px)",
            borderRadius: 3,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 10,
            color: "#bbb",
            fontFamily: "monospace",
          }}>
            col {i + 1}
          </div>
        ))}
      </div>
      <div style={{ fontSize: 11.5, color: "#666", marginTop: 8, lineHeight: 1.5 }}>
        <strong>{label}.</strong> {caption}
      </div>
    </div>
  );
}

// ── Raw Files Drawer ─────────────────────────────────────────────────────────
// A slide-in panel that lists all uploaded files.

function RawFilesDrawer({ open, onClose, onViewPdf }) {
  const fileSizes = ["4.2 MB", "9.1 MB", "6.4 MB", "5.7 MB", "14.3 MB", "7.8 MB"];
  const [query, setQuery] = React.useState("");
  const [downloading, setDownloading] = React.useState(null);

  const filtered = React.useMemo(() => {
    if (!query.trim()) return LIBRARY.map((p, i) => ({ paper: p, idx: i }));
    const q = query.toLowerCase();
    return LIBRARY
      .map((p, i) => ({ paper: p, idx: i }))
      .filter(({ paper: p }) =>
        p.title.toLowerCase().includes(q) ||
        p.authors.toLowerCase().includes(q) ||
        p.venue.toLowerCase().includes(q)
      );
  }, [query]);

  // Simulate a download by generating a text blob with paper metadata
  const handleDownload = (paper, i) => {
    setDownloading(paper.id);
    setTimeout(() => {
      const content = [
        `Title:   ${paper.title}`,
        `Authors: ${paper.authors}`,
        `Venue:   ${paper.venue}`,
        `Pages:   ${paper.pages}`,
        `Added:   ${paper.added}`,
        `Tags:    ${paper.tags.join(", ")}`,
        `Size:    ${fileSizes[i]}`,
        "",
        "[This is a demo — in production the original PDF binary would be served here.]",
      ].join("\n");
      const blob = new Blob([content], { type: "application/pdf" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${paper.title.slice(0, 48).replace(/\s+/g, "-").toLowerCase()}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
      setDownloading(null);
    }, 600);
  };

  // close on Escape
  React.useEffect(() => {
    const handler = (e) => { if (e.key === "Escape" && open) onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  return (
    <>
      {/* backdrop */}
      {open && (
        <div
          className="fixed inset-0 z-20"
          style={{ background: "rgba(0,0,0,0.18)", backdropFilter: "blur(2px)" }}
          onClick={onClose}
        />
      )}

      {/* drawer */}
      <div
        className="fixed top-0 right-0 h-screen z-30 flex flex-col"
        style={{
          width: 440,
          background: "var(--bg)",
          borderLeft: "1px solid var(--border)",
          transform: open ? "translateX(0)" : "translateX(100%)",
          transition: "transform 260ms cubic-bezier(0.4, 0, 0.2, 1)",
          boxShadow: open ? "-8px 0 32px rgba(0,0,0,0.12)" : "none",
        }}
      >
        {/* ── Header ── */}
        <div
          className="px-5 h-14 flex items-center justify-between shrink-0"
          style={{ borderBottom: "1px solid var(--border)" }}
        >
          <div className="flex items-center gap-2">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4" style={{ color: "var(--muted)" }}>
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
            </svg>
            <span className="text-[14px] font-medium" style={{ color: "var(--fg)" }}>Raw Files</span>
            <span
              className="text-[11px] font-mono px-1.5 py-0.5 rounded"
              style={{ background: "var(--bg-3)", color: "var(--muted)", border: "1px solid var(--border)" }}
            >
              {filtered.length} / {LIBRARY.length}
            </span>
          </div>
          <button
            onClick={onClose}
            className="w-7 h-7 rounded flex items-center justify-center text-[18px] font-light"
            style={{ color: "var(--muted)", background: "var(--bg-2)", border: "1px solid var(--border)" }}
          >×</button>
        </div>

        {/* ── Search bar ── */}
        <div className="px-4 py-3 shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="relative">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
              className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 pointer-events-none"
              style={{ color: "var(--muted)" }}>
              <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
            </svg>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by title, author, or venue…"
              className="w-full pl-8 pr-8 py-2 rounded-md text-[12.5px]"
              style={{
                background: "var(--bg-2)",
                border: "1px solid var(--border)",
                color: "var(--fg)",
                outline: "none",
              }}
            />
            {query && (
              <button
                onClick={() => setQuery("")}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[14px] leading-none"
                style={{ color: "var(--muted)" }}
              >×</button>
            )}
          </div>
        </div>

        {/* ── Path info bar ── */}
        <div
          className="px-5 py-2 text-[11px] flex items-center gap-2 shrink-0"
          style={{ background: "var(--bg-2)", borderBottom: "1px solid var(--border)", color: "var(--muted)" }}
        >
          <span>Stored at</span>
          <span className="font-mono" style={{ color: "var(--fg-2)" }}>~/.9xaipal/raw/</span>
          <span className="ml-auto">1.2 GB total</span>
        </div>

        {/* ── File list ── */}
        <div className="flex-1 min-h-0 overflow-y-auto thin-scroll">
          {filtered.length === 0 && (
            <div className="px-5 py-12 text-center text-[13px]" style={{ color: "var(--muted)" }}>
              No files match "{query}".
            </div>
          )}
          {filtered.map(({ paper, idx }) => (
            <div
              key={paper.id}
              className="px-5 py-4 flex items-start gap-3.5"
              style={{ borderBottom: "1px solid var(--border)" }}
            >
              {/* PDF icon */}
              <div
                className="w-9 h-11 rounded flex flex-col items-center justify-end pb-1 shrink-0 mt-0.5"
                style={{ background: "var(--bg-2)", border: "1px solid var(--border)" }}
              >
                <span className="text-[7.5px] font-mono uppercase tracking-wider" style={{ color: "var(--accent)" }}>pdf</span>
              </div>

              {/* file info */}
              <div className="flex-1 min-w-0">
                <div
                  className="text-[12.5px] font-medium leading-tight"
                  style={{ color: "var(--fg)", wordBreak: "break-word" }}
                >
                  {paper.title.slice(0, 44)}{paper.title.length > 44 ? "…" : ""}.pdf
                </div>
                <div className="flex items-center gap-2 mt-1 text-[11px] font-mono" style={{ color: "var(--muted)" }}>
                  <span>{fileSizes[idx]}</span>
                  <span className="opacity-40">·</span>
                  <span>{paper.pages}p</span>
                  <span className="opacity-40">·</span>
                  <span>{paper.added}</span>
                </div>
                {paper.progress > 0 && (
                  <div className="flex items-center gap-2 mt-2">
                    <div className="flex-1 h-[2px] rounded-full overflow-hidden" style={{ background: "var(--bg-3)" }}>
                      <div className="h-full" style={{
                        width: `${paper.progress * 100}%`,
                        background: paper.progress === 1 ? "var(--ok)" : "var(--accent)",
                      }} />
                    </div>
                    <span className="text-[10px] font-mono shrink-0" style={{ color: "var(--muted)" }}>
                      {paper.progress === 1 ? "indexed" : `${Math.round(paper.progress * 100)}%`}
                    </span>
                  </div>
                )}

                {/* action buttons */}
                <div className="flex items-center gap-2 mt-3">
                  {/* Open */}
                  <button
                    onClick={() => { onClose(); onViewPdf(paper); }}
                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded text-[11.5px]"
                    style={{ background: "var(--bg-2)", border: "1px solid var(--border)", color: "var(--fg-2)" }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = "var(--accent)";
                      e.currentTarget.style.color = "var(--accent-fg)";
                      e.currentTarget.style.borderColor = "var(--accent)";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = "var(--bg-2)";
                      e.currentTarget.style.color = "var(--fg-2)";
                      e.currentTarget.style.borderColor = "var(--border)";
                    }}
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
                      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" />
                    </svg>
                    Open
                  </button>

                  {/* Download */}
                  <button
                    onClick={() => handleDownload(paper, idx)}
                    disabled={downloading === paper.id}
                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded text-[11.5px]"
                    style={{
                      background: "var(--bg-2)",
                      border: "1px solid var(--border)",
                      color: downloading === paper.id ? "var(--muted)" : "var(--fg-2)",
                      opacity: downloading === paper.id ? 0.7 : 1,
                    }}
                    onMouseEnter={(e) => {
                      if (downloading === paper.id) return;
                      e.currentTarget.style.borderColor = "var(--border-strong)";
                      e.currentTarget.style.color = "var(--fg)";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.borderColor = "var(--border)";
                      e.currentTarget.style.color = "var(--fg-2)";
                    }}
                  >
                    {downloading === paper.id ? (
                      <>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" className="w-3.5 h-3.5" style={{ animation: "spin 0.8s linear infinite" }}>
                          <circle cx="12" cy="12" r="9" opacity="0.2" />
                          <path d="M21 12a9 9 0 0 0-9-9" />
                        </svg>
                        Saving…
                      </>
                    ) : (
                      <>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
                          <path d="M12 16V4m0 12-4-4m4 4 4-4" /><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
                        </svg>
                        Download
                      </>
                    )}
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* ── Footer ── */}
        <div
          className="px-5 py-3 shrink-0 flex items-center gap-2"
          style={{ borderTop: "1px solid var(--border)", background: "var(--bg-2)" }}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5 shrink-0" style={{ color: "var(--muted)" }}>
            <circle cx="12" cy="12" r="10" /><path d="M12 16v-4m0-4h.01" />
          </svg>
          <span className="text-[11px]" style={{ color: "var(--muted)" }}>
            Files are stored locally and never uploaded. <kbd className="kbd">Esc</kbd> to close.
          </span>
        </div>
      </div>

      {/* spin keyframe (inline) */}
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </>
  );
}

Object.assign(window, { PdfViewer, RawFilesDrawer });
