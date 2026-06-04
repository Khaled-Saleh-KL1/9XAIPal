const { useState, useEffect } = React;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "dark": false,
  "view": "library",
  "libraryView": "grid",
  "readingFont": "newsreader",
  "accent": "#c2613a"
}/*EDITMODE-END*/;

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [route, setRoute] = useState("library"); // library | processing | reading | pdf
  const [activePaper, setActivePaper] = useState(LIBRARY[0]);
  const [uploadingFile, setUploadingFile] = useState(null);
  const [pdfPaper, setPdfPaper] = useState(null);

  // dark mode
  useEffect(() => {
    document.documentElement.classList.toggle("dark", !!t.dark);
  }, [t.dark]);

  // sync route from tweak (so Tweaks panel can jump between states)
  useEffect(() => {
    if (t.view && t.view !== route) {
      if (t.view === "processing") {
        setUploadingFile({
          name: "structural-chunking-acl25.pdf",
          size: "4.2 MB",
          pages: 14,
        });
      }
      setRoute(t.view);
    }
    // eslint-disable-next-line
  }, [t.view]);

  // reading font
  useEffect(() => {
    const map = {
      newsreader: "'Newsreader', ui-serif, Georgia, serif",
      geist: "'Geist', ui-sans-serif, system-ui, sans-serif",
      mono: "'JetBrains Mono', ui-monospace, monospace",
    };
    document.documentElement.style.setProperty("--reading-font", map[t.readingFont] || map.newsreader);
    document
      .querySelectorAll(".prose-paper")
      .forEach((el) => (el.style.fontFamily = map[t.readingFont] || map.newsreader));
  }, [t.readingFont, route]);

  // accent
  useEffect(() => {
    if (t.accent) {
      document.documentElement.style.setProperty("--accent", t.accent);
    }
  }, [t.accent]);

  const startUpload = () => {
    setUploadingFile({
      name: "structural-chunking-acl25.pdf",
      size: "4.2 MB",
      pages: 14,
    });
    setRoute("processing");
    setTweak("view", "processing");
  };

  const openPaper = (p) => {
    setActivePaper(p);
    setRoute("reading");
    setTweak("view", "reading");
  };

  const openPdf = (p) => {
    setPdfPaper(p);
    setRoute("pdf");
  };

  return (
    <>
      {route === "library" && (
        <LibraryView
          onOpenPaper={openPaper}
          onUpload={startUpload}
          onViewPdf={openPdf}
          view={t.libraryView}
          setView={(v) => setTweak("libraryView", v)}
        />
      )}

      {route === "pdf" && pdfPaper && (
        <PdfViewer
          paper={pdfPaper}
          onBack={() => setRoute("library")}
        />
      )}
      {route === "reading" && (
        <ReadingView
          paper={activePaper}
          onBack={() => { setRoute("library"); setTweak("view", "library"); }}
        />
      )}
      {route === "processing" && uploadingFile && (
        <>
          {/* keep library visible behind the overlay */}
          <LibraryView
            onOpenPaper={openPaper}
            onUpload={() => {}}
            onViewPdf={openPdf}
            view={t.libraryView}
            setView={(v) => setTweak("libraryView", v)}
          />
          <ProcessingOverlay
            file={uploadingFile}
            onDone={() => {
              setActivePaper({
                ...LIBRARY[0],
                title: "Structural Chunking for Long-Form Document Understanding",
              });
              setRoute("reading");
              setTweak("view", "reading");
            }}
            onCancel={() => { setRoute("library"); setTweak("view", "library"); }}
          />
        </>
      )}

      <TweaksPanel title="Tweaks">
        <TweakSection label="State">
          <TweakRadio
            label="Screen"
            value={t.view}
            onChange={(v) => setTweak("view", v)}
            options={[
              { value: "library", label: "Library" },
              { value: "processing", label: "Processing" },
              { value: "reading", label: "Reading" },
            ]}
          />
          {t.view === "library" && (
            <TweakRadio
              label="Layout"
              value={t.libraryView}
              onChange={(v) => setTweak("libraryView", v)}
              options={[
                { value: "grid", label: "Grid" },
                { value: "list", label: "List" },
              ]}
            />
          )}
        </TweakSection>

        <TweakSection label="Appearance">
          <TweakToggle
            label="Dark mode"
            value={t.dark}
            onChange={(v) => setTweak("dark", v)}
          />
          <TweakColor
            label="Accent"
            value={t.accent}
            onChange={(v) => setTweak("accent", v)}
            options={["#c2613a", "#7a5cf0", "#2f8f6c", "#1f1f1f"]}
          />
          <TweakSelect
            label="Reading font"
            value={t.readingFont}
            onChange={(v) => setTweak("readingFont", v)}
            options={[
              { value: "newsreader", label: "Newsreader (serif)" },
              { value: "geist", label: "Geist (sans)" },
              { value: "mono", label: "JetBrains Mono" },
            ]}
          />
        </TweakSection>
      </TweaksPanel>
    </>
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
