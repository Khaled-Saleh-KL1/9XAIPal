import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ArticleBlock } from './ArticleBlock';
import type { DocBlock } from '../api';

const renderBlock = (text: string, baseDirection: 'ltr' | 'rtl' | 'auto') => {
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
