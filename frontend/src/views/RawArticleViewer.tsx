import { useEffect, useRef } from 'react';
import type { PaperMeta } from '../api';
import { getRawFileUrl } from '../api';
import { IconBack } from '../components/Icons';
import { UserMenuInline } from '../components/UserMenu';
import { displayTitle } from '../lib/titles';
import { readRawFrameAnchor, scrollRawFrameToAnchor } from '../lib/rawFrameSync';

interface Props {
  paper: PaperMeta;
  onBack: () => void;
  /** Hands back the passage the reader is looking at, so the structured
   * reader can open at the same place. */
  onReadStructured: (paper: PaperMeta, anchor: string) => void;
  /** Passages from the structured reader to open at, nearest first. */
  anchors?: string[] | null;
}

/**
 * Sibling to PdfViewer.tsx, for a doc_kind='article' document's raw HTML
 * snapshot (see backend services/article_crawl.py) instead of a PDF.
 *
 * Deliberately much simpler than PdfViewer: no page/zoom controls, no
 * page-picker state — just an iframe pointed at GET /papers/{id}/raw, which
 * serves the one sanitized page directly. The page's own hyperlinks are
 * preserved as ordinary clickable links in the extracted article content
 * itself (see article_extraction.py's include_links=True) rather than
 * anything this viewer needs to handle.
 *
 * An article has no pages, so position is kept in step with the structured
 * reader by text instead — see lib/rawFrameSync.ts for why reaching into
 * the iframe's document is sound here.
 */
export function RawArticleViewer({ paper, onBack, onReadStructured, anchors }: Props) {
  const rawUrl = getRawFileUrl(paper.id);
  const frameRef = useRef<HTMLIFrameElement>(null);

  // On load rather than on mount: the document has to exist before there is
  // anything to scroll. Images inside it can still be loading, which moves
  // the target — so re-run once more shortly after, when heights have
  // settled, rather than leaving the reader near-but-not-at the passage.
  useEffect(() => {
    if (!anchors || !anchors.length) return;
    const frame = frameRef.current;
    if (!frame) return;
    const settle = setTimeout(() => scrollRawFrameToAnchor(frame, anchors), 600);
    const onLoad = () => scrollRawFrameToAnchor(frame, anchors);
    frame.addEventListener('load', onLoad);
    // Already loaded (a cached snapshot can beat this effect to it).
    if (frame.contentDocument?.readyState === 'complete') onLoad();
    return () => {
      clearTimeout(settle);
      frame.removeEventListener('load', onLoad);
    };
  }, [anchors, rawUrl]);

  return (
    <div className="h-screen flex flex-col overflow-hidden" style={{ background: 'var(--bg)' }}>
      <header
        className="shrink-0 px-3 sm:px-6 h-13 py-2.5 flex items-center gap-2 sm:gap-4"
        style={{ borderBottom: '1px solid var(--border)' }}
      >
        <button
          onClick={onBack}
          className="shrink-0 flex items-center gap-1.5 px-2 py-1.5 rounded-md text-[12.5px]"
          style={{ color: 'var(--muted)' }}
          onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--fg)')}
          onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--muted)')}
        >
          <IconBack className="w-3.5 h-3.5" />
          <span>Library</span>
        </button>
        <span className="hidden sm:block shrink-0 h-4 w-px" style={{ background: 'var(--border)' }} />
        <span
          className="hidden sm:inline font-serif text-[14px] tracking-tight truncate"
          style={{ color: 'var(--fg)' }}
          title={paper.original_filename}
        >
          {displayTitle(paper)} — raw snapshot
        </span>

        <div className="ml-auto min-w-0 flex items-center gap-2">
          <span className="sm:hidden font-serif text-[13px] truncate max-w-[100px]" style={{ color: 'var(--fg)' }}>
            {displayTitle(paper)}
          </span>
          {/* A real new-tab open of the exact same authenticated, CSP-protected
              URL the iframe below already shows — safe for the same reason
              the iframe is (see backend's _raw_html_headers). */}
          <a
            href={rawUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[11.5px] px-3 py-1.5 rounded-md flex items-center gap-1.5 no-underline"
            style={{ border: '1px solid var(--border)', color: 'var(--fg)', background: 'var(--bg)' }}
          >
            <span>↗</span> Open in new tab
          </a>
          <button
            onClick={() => onReadStructured(paper, readRawFrameAnchor(frameRef.current))}
            className="text-[12.5px] px-3.5 py-1.5 rounded-md flex items-center gap-1.5"
            style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
          >
            <span className="text-[11px]">☐</span> Read structured
          </button>
          <span className="mx-1 h-4 w-px" style={{ background: 'var(--border)' }} />
          <UserMenuInline />
        </div>
      </header>

      <div className="flex-1 overflow-hidden" style={{ background: '#f0ede8' }}>
        <iframe
          ref={frameRef}
          src={rawUrl}
          title={`Raw snapshot: ${displayTitle(paper)}`}
          className="w-full h-full border-0"
          // No sandbox JS/plugins to grant — the backend already strips
          // <script>/event-handlers at crawl time and serves this with
          // script-src 'none' regardless (see _raw_html_headers). This just
          // matches that same intent at the embed boundary too, in case the
          // sanitizer or the CSP header were ever the only line of defense.
          //
          // ⚠ allow-same-origin is load-bearing beyond that: dropping it
          // would give the snapshot an opaque origin and silently break the
          // position sync above, which reads the frame's own document.
          sandbox="allow-same-origin"
        />
      </div>
    </div>
  );
}
