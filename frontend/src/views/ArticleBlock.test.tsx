import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ArticleBlock } from './ArticleBlock';
import type { DocBlock } from '../api';
import type { ReferenceIndex } from '../lib/references';

const renderBlock = (
  text: string,
  baseDirection: 'ltr' | 'rtl' | 'auto',
  citationRefs?: ReferenceIndex,
) => {
  const block: DocBlock = {
    id: 'block-1',
    sequence_order: 1,
    structural_type: 'paragraph',
    content_markdown: text,
    plain_text: text,
    heading_path: null,
    page_start: null,
    page_end: null,
    image_url: null,
  };
  return render(
    <ArticleBlock
      block={block}
      baseDirection={baseDirection}
      blockTinted={false}
      active={false}
      bookmarkTitle={null}
      onAsk={vi.fn()}
      onClearBookmark={vi.fn()}
      registerRef={vi.fn()}
      paperId={citationRefs ? 'paper' : undefined}
      citationRefs={citationRefs}
    />,
  );
};

describe('ArticleBlock direction', () => {
  it('marks Arabic blocks RTL and Latin-only blocks in an RTL document LTR', () => {
    const arabic = renderBlock('العنوان 2026', 'rtl');
    expect(arabic.container.querySelector('[data-seq="1"]')).toHaveAttribute('dir', 'rtl');
    arabic.unmount();

    const latin = renderBlock('English heading only', 'rtl');
    expect(latin.container.querySelector('[data-seq="1"]')).toHaveAttribute('dir', 'ltr');
  });

  it('flows an Arabic block RTL even in an English document', () => {
    const rendered = renderBlock('العنوان 2026', 'ltr');
    expect(rendered.container.querySelector('[data-seq="1"]')).toHaveAttribute('dir', 'rtl');
  });
});

describe('ArticleBlock citation style', () => {
  it('does not turn author-year papers’ bracketed text into numeric chips', () => {
    const refs = {
      citationStyle: 'author_year',
      numbers: new Set([2]),
      byNumber: new Map([[2, {
        number: 2,
        raw_text: 'Smith. 2020. A paper title.',
        resolve_status: 'pending',
        already_in_library: false,
      }]]),
    } as unknown as ReferenceIndex;

    const rendered = renderBlock('A bracketed list index [2] stays text.', 'ltr', refs);

    expect(rendered.container.querySelector('.cite-chip')).toBeNull();
    expect(rendered.container.textContent).toContain('[2]');
  });
});
