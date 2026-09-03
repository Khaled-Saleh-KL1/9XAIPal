import type { PaperMeta } from '../api';
import { getRawFileUrl } from '../api';
import { IconBack } from '../components/Icons';
import { UserMenuInline } from '../components/UserMenu';
import { displayTitle } from '../lib/titles';

interface Props {
  paper: PaperMeta;
  onBack: () => void;
  onReadStructured: (paper: PaperMeta) => void;
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
 */
export function RawArticleViewer({ paper, onBack, onReadStructured }: Props) {
  const rawUrl = getRawFileUrl(paper.id);

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
            onClick={() => onReadStructured(paper)}
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
          src={rawUrl}
          title={`Raw snapshot: ${displayTitle(paper)}`}
          className="w-full h-full border-0"
          // No sandbox JS/plugins to grant — the backend already strips
          // <script>/event-handlers at crawl time and serves this with
          // script-src 'none' regardless (see _raw_html_headers). This just
          // matches that same intent at the embed boundary too, in case the
          // sanitizer or the CSP header were ever the only line of defense.
          sandbox="allow-same-origin"
        />
      </div>
    </div>
  );
}
