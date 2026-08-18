import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import { MARKDOWN_REMARK, MARKDOWN_REHYPE } from '../lib/markdown';
import type { DocBlock } from '../api';

/**
 * One structural block of the paper, rendered as article prose.
 *
 * Every block carries `data-seq` and `data-chunk-id`: that is how a text
 * selection is traced back to a chunk, and how a note finds the element it
 * should sit beside. Do not remove them.
 */

function Md({ children, className = '' }: { children: string; className?: string }) {
  return (
    <div className={`md-body ${className}`}>
      <ReactMarkdown remarkPlugins={MARKDOWN_REMARK} rehypePlugins={MARKDOWN_REHYPE}>
        {children}
      </ReactMarkdown>
    </div>
  );
}

function InlineMd({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={MARKDOWN_REMARK}
      rehypePlugins={MARKDOWN_REHYPE}
      components={{ p: ({ children: c }) => <span>{c}</span> }}
    >
      {children}
    </ReactMarkdown>
  );
}

interface Props {
  block: DocBlock;
  /** True when a note is anchored here but its quote could not be re-located. */
  blockTinted: boolean;
  /** True when this block is the anchor of the note the reader is focused on. */
  active: boolean;
  /** Set when this block carries one of the reader's bookmarks. */
  bookmarkTitle: string | null;
  /**
   * Open the composer on a block that cannot be reached by highlighting.
   * Figures are images, a KaTeX equation is a tree of spans that drag-selects
   * into gibberish, and a table drag-selects into a column of orphaned cell
   * values with no header attached — all three need an explicit affordance.
   */
  onAsk: (block: DocBlock, kind: 'figure' | 'equation' | 'table') => void;
  /** Clicking the ribbon lifts the bookmark off this block. */
  onClearBookmark: (seq: number) => void;
  registerRef: (seq: number, el: HTMLElement | null) => void;
}

function ArticleBlockImpl({
  block,
  blockTinted,
  active,
  bookmarkTitle,
  onAsk,
  onClearBookmark,
  registerRef,
}: Props) {
  const seq = block.sequence_order;
  const isBookmarked = bookmarkTitle !== null;
  const common = {
    'data-seq': String(seq),
    'data-chunk-id': block.id,
    id: `blk-${seq}`,
    ref: (el: HTMLElement | null) => registerRef(seq, el),
    className: [
      'article-block',
      blockTinted ? 'is-tinted' : '',
      active ? 'is-active' : '',
      isBookmarked ? 'is-bookmarked' : '',
    ].filter(Boolean).join(' '),
  };

  /**
   * The bookmark's presence in the text itself.
   *
   * A tinted background alone is easy to miss when you return to a paper days
   * later and are scrolling fast; a ribbon in the margin is a shape, and shapes
   * survive peripheral vision. It doubles as the control that removes the mark,
   * which is where you would reach for it anyway.
   */
  const ribbon = isBookmarked ? (
    <button
      type="button"
      className="article-bookmark-flag"
      onClick={() => onClearBookmark(seq)}
      title={`${bookmarkTitle} — click to remove this bookmark`}
      aria-label="Remove this bookmark"
    >
      <svg viewBox="0 0 12 16" width="12" height="16" aria-hidden="true">
        <path d="M1 1h10v14l-5-4-5 4z" />
      </svg>
    </button>
  ) : null;

  if (block.structural_type === 'heading') {
    const level = block.heading_path?.length ?? 1;
    const text = block.plain_text || block.content_markdown.replace(/^#+\s*/, '');
    return (
      <section {...common}>
        {ribbon}
        <h2 className={`article-h article-h${Math.min(level, 3)}`}>{text}</h2>
      </section>
    );
  }

  if (block.structural_type === 'figure') {
    return (
      <figure {...common}>
        {ribbon}
        <div className="article-figure">
          {block.image_url ? (
            <img src={block.image_url} alt={block.plain_text || 'figure'} loading="lazy" />
          ) : (
            <div className="article-figure-missing">Figure image unavailable</div>
          )}
          <button
            type="button"
            className="article-figure-ask"
            onClick={() => onAsk(block, 'figure')}
            title="Ask about this figure"
          >
            Ask about this figure
          </button>
        </div>
        {block.plain_text && (
          <figcaption className="article-caption">{block.plain_text}</figcaption>
        )}
      </figure>
    );
  }

  if (block.structural_type === 'math') {
    const body = block.content_markdown.trim();
    const wrapped = body.startsWith('$$') ? body : `$$\n${body}\n$$`;
    return (
      <section {...common}>
        {ribbon}
        <div className="article-math">
          <Md>{wrapped}</Md>
          <button
            type="button"
            className="article-math-ask"
            onClick={() => onAsk(block, 'equation')}
            title="Ask about this equation"
          >
            Ask about this equation
          </button>
        </div>
      </section>
    );
  }

  if (block.structural_type === 'table') {
    const json = block.table_json;
    /**
     * A table is a unit, and it gets its own scroll box.
     *
     * A results table is routinely wider than a column of prose — ten model
     * variants across six metrics does not fit, and the previous
     * `width: 100%` made it *fit anyway* by crushing every column until the
     * headers wrapped one letter per line. The box below scrolls instead: the
     * table lays out at its natural width and the reader pans it, the way they
     * would in the PDF. Vertical too, so a fifty-row table does not push the
     * rest of the paper off screen.
     *
     * `tabIndex` is not decoration — a scroll container that only responds to
     * a trackpad is unreachable by keyboard, and this one can hold the numbers
     * the whole paper is about.
     */
    const body = json?.headers && json?.rows ? (
      <table>
        <thead>
          <tr>
            {json.headers.map((h, i) => (
              <th key={i}><InlineMd>{h}</InlineMd></th>
            ))}
          </tr>
        </thead>
        <tbody>
          {json.rows.map((row, ri) => (
            <tr key={ri}>
              {row.map((cell, ci) => (
                <td key={ci}><InlineMd>{cell}</InlineMd></td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    ) : (
      // MinerU recovered no structure, so this is its raw <table> HTML or a
      // markdown grid. Same scroller either way — the CSS targets the <table>.
      <Md>{block.content_markdown}</Md>
    );

    return (
      <section {...common}>
        {ribbon}
        <div className="article-table">
          <div
            className="article-table-scroll"
            tabIndex={0}
            role="region"
            aria-label={block.plain_text ? `Table: ${block.plain_text}` : 'Table'}
          >
            {body}
          </div>
          <button
            type="button"
            className="article-table-ask"
            onClick={() => onAsk(block, 'table')}
            title="Ask about this table"
          >
            Ask about this table
          </button>
        </div>
      </section>
    );
  }

  if (block.structural_type === 'code') {
    const body = block.content_markdown || block.plain_text || '';
    const fenced = body.includes('```') ? body : `\`\`\`\n${body}\n\`\`\``;
    return (
      <section {...common}>
        {ribbon}
        <div className="article-code"><Md>{fenced}</Md></div>
      </section>
    );
  }

  if (block.structural_type === 'footnote') {
    return (
      <aside {...common}>
        {ribbon}
        <div className="article-footnote">
          {block.plain_text || block.content_markdown}
        </div>
      </aside>
    );
  }

  return (
    <section {...common}>
      {ribbon}
      <Md>{block.content_markdown || block.plain_text}</Md>
    </section>
  );
}

// Blocks are numerous and static; without memo, every keystroke in the
// composer would re-render the entire paper.
export const ArticleBlock = memo(ArticleBlockImpl);
