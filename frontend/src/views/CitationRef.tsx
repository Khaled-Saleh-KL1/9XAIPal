import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { MARKDOWN_REMARK, MARKDOWN_REHYPE } from '../lib/markdown';
import { getChunk, type StudyCitation } from '../api';

/**
 * A `[[P2:41]]` marker, expandable in place.
 *
 * ⚠ **This is the whole point of the desk.** The reader asked to work across
 * papers "without seeing them" — which only holds if a claim can be checked
 * where it is made. Clicking the chip fetches that block and shows the paper's
 * own words inline; leaving the desk to verify one sentence would defeat the
 * surface.
 *
 * The block is fetched on first expand and kept after that. A study answer
 * routinely carries a dozen citations, and prefetching all of them would be a
 * dozen requests for text that mostly never gets opened.
 */
export function CitationRef({
  cite,
  onOpenPaper,
}: {
  cite: StudyCitation;
  /** Open the paper at this block in the reader. */
  onOpenPaper?: (documentId: string, sequenceId: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const toggle = async () => {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (text !== null || loading) return;
    setLoading(true);
    try {
      const chunk = await getChunk(cite.document_id, cite.sequence_id);
      setText(chunk.content_markdown || chunk.plain_text || '(this block is empty)');
    } catch (e) {
      setError((e as Error).message || 'Could not load that block');
    } finally {
      setLoading(false);
    }
  };

  return (
    <span className="cite-wrap">
      <button
        type="button"
        className={`cite-chip${open ? ' is-open' : ''}`}
        onClick={toggle}
        title={`${cite.label} — block ${cite.sequence_id}`}
      >
        P{cite.paper}:{cite.sequence_id}
      </button>
      {open && (
        <span className="cite-peek">
          <span className="cite-peek-head">
            <span className="cite-peek-src">{cite.label}</span>
            {onOpenPaper && (
              <button
                type="button"
                className="cite-peek-open"
                onClick={() => onOpenPaper(cite.document_id, cite.sequence_id)}
              >
                open in reader →
              </button>
            )}
          </span>
          <span className="cite-peek-body">
            {loading && <span className="cite-peek-muted">Loading…</span>}
            {error && <span className="cite-peek-error">{error}</span>}
            {text !== null && (
              <ReactMarkdown remarkPlugins={MARKDOWN_REMARK} rehypePlugins={MARKDOWN_REHYPE}>
                {text}
              </ReactMarkdown>
            )}
          </span>
        </span>
      )}
    </span>
  );
}
