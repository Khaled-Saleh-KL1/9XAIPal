import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, m, useReducedMotion } from 'motion/react';
import ReactMarkdown from 'react-markdown';
import { MARKDOWN_REMARK, MARKDOWN_REHYPE } from '../lib/markdown';
import { getChunk, type StudyCitation } from '../api';
import { Pressable, gentle, reducedMotionFade } from '../motion';

/**
 * A `[[P2:41]]` marker, expandable in place.
 *
 * ⚠ **This is the whole point of the desk.** The reader asked to work across
 * papers "without seeing them", which only holds if a claim can be checked
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
  const wrapRef = useRef<HTMLSpanElement>(null);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

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
    <span className="cite-wrap" ref={wrapRef}>
      <Pressable
        as="button"
        type="button"
        intensity="citation"
        className={`cite-chip${open ? ' is-open' : ''}`}
        onClick={toggle}
        aria-expanded={open}
        title={
          cite.page == null
            ? `${cite.label}, block ${cite.sequence_id}`
            : `${cite.label}, page ${cite.page}, block ${cite.sequence_id}`
        }
      >
        {/* ⚠ The paper number stays in the label even when a page is known.
            A desk answer draws on several papers at once, so a bare "p. 7"
            would be ambiguous in exactly the situation the desk exists for;
            "P2 · p. 7" says which paper and where in it. The block number
            moves to the tooltip, since it is a coordinate in this app rather
            than in the document the reader is going to check. */}
        P{cite.paper}
        {cite.page == null ? `:${cite.sequence_id}` : ` · p. ${cite.page}`}
      </Pressable>
      <AnimatePresence initial={false}>
        {open && (
        <m.span
          key="cite-peek"
          className="cite-peek"
          initial={reducedMotion ? { opacity: 0 } : { opacity: 0, scale: 0.9, y: 4 }}
          animate={reducedMotion ? { opacity: 1 } : { opacity: 1, scale: 1, y: 0 }}
          exit={reducedMotion ? { opacity: 0 } : { opacity: 0, scale: 0.96, y: 2, pointerEvents: 'none' }}
          transition={reducedMotion ? reducedMotionFade : gentle}
        >
          <span className="cite-peek-head">
            <span className="cite-peek-src">{cite.label}</span>
            {/* Outside cite-peek-src, not inside it: that span ellipsizes a
                long paper title, and a page number nested in it would be the
                first thing truncated away. */}
            {cite.page != null && <span className="cite-peek-page">p. {cite.page}</span>}
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
        </m.span>
        )}
      </AnimatePresence>
    </span>
  );
}
