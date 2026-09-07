/**
 * A diagram the model drew, rendered from a ```mermaid fenced block.
 *
 * Every AI surface in the app reaches this through the shared markdown
 * pipeline (see lib/markdown.ts), so an architecture sketch works the same in
 * a margin note, the desk, a book chat and the research answer.
 *
 * ⚠ mermaid is ~1MB and most answers contain no diagram, so it is imported
 * lazily on first use and the module promise is cached — the same reason
 * pdf.js is lazy in App.tsx. Nothing is downloaded until a model actually
 * draws something.
 *
 * ⚠ The diagram source is MODEL OUTPUT, which means it is untrusted and
 * frequently invalid. Two consequences:
 *   - securityLevel 'strict' (mermaid's default, set explicitly here so it
 *     cannot drift): no click handlers, no HTML labels, so the SVG this
 *     produces carries no script surface even though it is injected as raw
 *     markup.
 *   - a syntax error must not blank the answer around it. `mermaid.parse`
 *     is called first and a failure falls back to showing the source as a
 *     plain code block, which is what the reader would have seen before
 *     diagrams existed at all.
 */
import { useEffect, useRef, useState } from 'react';

type MermaidApi = {
  initialize: (config: Record<string, unknown>) => void;
  parse: (text: string) => Promise<unknown>;
  render: (id: string, text: string) => Promise<{ svg: string }>;
};

let mermaidPromise: Promise<MermaidApi> | null = null;

/** Load (once) and configure mermaid for the viewer's current theme. */
function loadMermaid(dark: boolean): Promise<MermaidApi> {
  if (!mermaidPromise) {
    mermaidPromise = import('mermaid').then((m) => m.default as unknown as MermaidApi);
  }
  return mermaidPromise.then((mermaid) => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      theme: dark ? 'dark' : 'default',
      // The reader's own serif/sans stack, so a diagram does not arrive in a
      // font nothing else on the page uses.
      fontFamily: 'inherit',
    });
    return mermaid;
  });
}

let seq = 0;

export function MermaidDiagram({ source }: { source: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const idRef = useRef(`mmd-${++seq}`);

  useEffect(() => {
    let alive = true;
    const dark =
      document.documentElement.dataset.theme === 'dark' ||
      (!document.documentElement.dataset.theme &&
        window.matchMedia?.('(prefers-color-scheme: dark)').matches);

    void (async () => {
      try {
        const mermaid = await loadMermaid(!!dark);
        // parse first: render() on invalid source can leave an error node in
        // the DOM as a side effect, which is not something to do to a page
        // mid-answer.
        await mermaid.parse(source);
        const { svg: out } = await mermaid.render(idRef.current, source);
        if (alive) setSvg(out);
      } catch {
        if (alive) setFailed(true);
      }
    })();

    return () => {
      alive = false;
    };
  }, [source]);

  if (failed) {
    // Not an error message: a model that wrote broken mermaid still wrote
    // something the reader may want to see, and an apology in its place is
    // strictly less useful than the source.
    return (
      <pre className="text-[12px] overflow-x-auto rounded-lg p-3"
           style={{ background: 'var(--bg-2)', color: 'var(--fg-2)' }}>
        <code>{source}</code>
      </pre>
    );
  }

  if (!svg) {
    return (
      <div className="text-[12px] py-2" style={{ color: 'var(--muted)' }}>
        drawing…
      </div>
    );
  }

  return (
    <div
      className="my-2 overflow-x-auto"
      /* The model's text never reaches the DOM as HTML — this is mermaid's own
         SVG output, and mermaid itself runs it through DOMPurify before
         returning it for every securityLevel except 'loose' (see
         `serializeSvg` in mermaid's renderer: `else if (!isLooseSecurityLevel)
         { code = DOMPurify.sanitize(code, …) }`). We pass 'strict' above, and
         mermaid ships DOMPurify as a direct dependency, so that branch is the
         one that runs. Sanitizing again here would strip the SVG. */
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
