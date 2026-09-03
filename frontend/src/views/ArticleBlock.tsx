import { memo, useLayoutEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { MARKDOWN_REMARK, MARKDOWN_REHYPE, MARKDOWN_LINK_COMPONENT } from '../lib/markdown';
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
      <ReactMarkdown
        remarkPlugins={MARKDOWN_REMARK}
        rehypePlugins={MARKDOWN_REHYPE}
        components={MARKDOWN_LINK_COMPONENT}
      >
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
      components={{ ...MARKDOWN_LINK_COMPONENT, p: ({ children: c }) => <span>{c}</span> }}
    >
      {children}
    </ReactMarkdown>
  );
}

/**
 * A formula whose LaTeX transcription KaTeX can't parse (garbled OCR, not
 * something we can safely auto-repair without risking silently wrong math —
 * see strip_leaked_sup_run's sibling reasoning in the backend) falls back to
 * the page crop MinerU already captured for every equation, so the reader
 * sees the real notation instead of raw TeX source.
 *
 * Checks the actual rendered output (a .katex-error span) rather than a
 * separate katex.renderToString probe — the app's own `katex` package and
 * rehype-katex's private dependency copy are on different versions, so a
 * direct `import katex from 'katex'` here would bundle a second full copy
 * of the library. useLayoutEffect runs before paint, so a formula that does
 * need the image fallback is never visible as broken raw text first.
 */
function MathBlock({ wrapped, imageUrl }: { wrapped: string; imageUrl: string | null }) {
  const ref = useRef<HTMLDivElement>(null);
  const [broken, setBroken] = useState(false);

  useLayoutEffect(() => {
    setBroken(!!ref.current?.querySelector('.katex-error'));
  }, [wrapped]);

  if (broken && imageUrl) {
    return (
      <img
        className="article-math-fallback-img"
        src={imageUrl}
        alt="Equation as it appears on the page (its transcription could not be rendered)"
      />
    );
  }
  return (
    <div ref={ref}>
      <Md>{wrapped}</Md>
    </div>
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
   * values with no header attached, so all three need an explicit affordance.
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
      title={`${bookmarkTitle}: click to remove this bookmark`}
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
          <figcaption className="article-caption"><InlineMd>{block.plain_text}</InlineMd></figcaption>
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
          <MathBlock wrapped={wrapped} imageUrl={block.image_url} />
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
     * A results table is routinely wider than a column of prose: ten model
     * variants across six metrics does not fit, and the previous
     * `width: 100%` made it *fit anyway* by crushing every column until the
     * headers wrapped one letter per line. The box below scrolls instead: the
     * table lays out at its natural width and the reader pans it, the way they
     * would in the PDF. Vertical too, so a fifty-row table does not push the
     * rest of the paper off screen.
     *
     * `tabIndex` is not decoration: a scroll container that only responds to
     * a trackpad is unreachable by keyboard, and this one can hold the numbers
     * the whole paper is about.
     */
    /**
     * ⚠ `table_json.headers` is routinely EMPTY on real papers, and the header
     * row arrives as `rows[0]` instead.
     *
     * MinerU emits `<table><tr><td>…` with no `<thead>`, so the parser that
     * fills `table_json` has nothing to put in `headers`. Rendering that
     * literally gives an empty `<thead>` and a header row that behaves like
     * data: not bold, not tinted, and, since the sticky rule applies to `th`,
     * scrolling a long table loses the column names entirely, which is the
     * exact failure the scroll box exists to prevent.
     *
     * A first row promoted in error costs one row of bold text. A header that
     * scrolls away costs the reader the meaning of every number below it.
     */
    const rows = json?.rows ?? [];
    const headerCells = json?.headers?.length ? json.headers : rows[0];
    const bodyRows = json?.headers?.length ? rows : rows.slice(1);

    const body = headerCells?.length ? (
      <table>
        <thead>
          <tr>
            {headerCells.map((h, i) => (
              <th key={i}><InlineMd>{h}</InlineMd></th>
            ))}
          </tr>
        </thead>
        <tbody>
          {bodyRows.map((row, ri) => (
            <tr key={ri}>
              {row.map((cell, ci) => (
                <td key={ci}><InlineMd>{cell}</InlineMd></td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    ) : block.image_url ? (
      // No table_json for one of two reasons: MinerU genuinely couldn't find
      // structure, or the backend deliberately withheld it because the
      // reconciled rows disagreed in width — a scrambled-but-valid table
      // that would otherwise show plausible, wrong numbers (see
      // chunker.py's _table_rows_are_consistent). Either way, MinerU crops
      // every table into an image regardless of whether the structural
      // parse succeeds, and that crop is strictly more trustworthy than a
      // guess at structure — same fallback-to-the-page-crop pattern
      // MathBlock already uses for an equation KaTeX can't parse.
      //
      // Unlike MathBlock, there is nothing to detect client-side here: a
      // scrambled table still parses as valid HTML (no exception, no
      // .katex-error-style signal to catch), so the decision is made
      // server-side and reaches this component as "no table_json."
      <img
        className="article-table-fallback-img"
        src={block.image_url}
        alt={block.plain_text || 'Table, shown as an image'}
      />
    ) : (
      // MinerU recovered no structure AND no crop is available. Same
      // scroller either way: the CSS targets the <table>.
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
    // With a page crop available, IT is the listing — a literal code/schema
    // block's exact indentation is part of what it shows, and the OCR text
    // lost that before it ever reached the chunk (see chunker.py's
    // crop_code_blocks). Showing both side by side just prints the same
    // thing twice, the second time worse. The text stays reachable, folded
    // away, because an image cannot be copied or selected.
    if (block.image_url) {
      return (
        <section {...common}>
          {ribbon}
          <img
            className="article-code-crop-img"
            src={block.image_url}
            alt={block.plain_text || 'Code listing, shown as it appears on the page'}
          />
          <details className="article-code-text">
            <summary>Extracted text</summary>
            <div className="article-code"><Md>{fenced}</Md></div>
          </details>
        </section>
      );
    }

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
          <InlineMd>{block.plain_text || block.content_markdown}</InlineMd>
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
