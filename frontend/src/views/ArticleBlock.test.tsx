import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ArticleBlock } from './ArticleBlock';
import type { DocBlock, ReferenceEntry } from '../api';
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

  it('links each supported parenthetical and narrative author-year form to its entry', () => {
    const entries: ReferenceEntry[] = [
      authorYearEntry(1, 'Abdelali', 2024, 'Ahmed Abdelali et al. 2024. LAraBench title.'),
      authorYearEntry(2, 'Gehring', 2017, 'Jonas Gehring et al. Convolutional learning. In ICML, 2017.'),
      authorYearEntry(3, 'Kiela', 2021, 'Kiela et al. 2021. A multimodal paper title.'),
      authorYearEntry(4, 'Smith', 2020, 'Smith and Jones. 2020. A two-author paper.'),
      authorYearEntry(5, 'Smith', 2020, 'Smith. 2020a. A suffix-year paper.'),
    ];
    const refs = makeAuthorYearIndex(entries);

    const rendered = renderBlock(
      '(Abdelali et al., 2024) (Gehring et al., 2017; Kiela et al., 2021) '
        + '(Smith and Jones, 2020) (Smith, 2020a). '
        + 'Abdelali et al. (2024) and Smith and Jones (2020).',
      'ltr',
      refs,
    );

    expect([...rendered.container.querySelectorAll('.cite-chip')].map((button) => button.textContent)).toEqual([
      '[1]', '[2]', '[3]', '[4]', '[5]', '[1]', '[4]',
    ]);
    expect(rendered.container.textContent).toBe('([1]) ([2]; [3]) ([4]) ([5]). [1] and [4].');
  });

  it('matches accented surnames without accents', () => {
    const refs = makeAuthorYearIndex([
      authorYearEntry(6, 'Fédérico', 2023, 'Fédérico. 2023. Une étude importante.'),
    ]);

    const rendered = renderBlock('(fEderico, 2023).', 'ltr', refs);

    expect(rendered.container.querySelector('.cite-chip')).toHaveTextContent('[6]');
  });

  it('leaves ambiguous, unknown, and suffix-mismatched citations as text', () => {
    const refs = makeAuthorYearIndex([
      authorYearEntry(7, 'Ng', 2022, 'Ng et al. 2022. First paper.'),
      authorYearEntry(8, 'Ng', 2022, 'Ng et al. 2022. Second paper.'),
      authorYearEntry(9, 'Smith', 2020, 'Smith. 2020a. A suffix-year paper.'),
    ]);

    const rendered = renderBlock('(Ng et al., 2022), (Unknown, 2099), and (Smith, 2020b).', 'ltr', refs);

    expect(rendered.container.querySelector('.cite-chip')).toBeNull();
    expect(rendered.container.textContent).toContain('(Ng et al., 2022), (Unknown, 2099), and (Smith, 2020b).');
  });

  it('does not match a later coauthor when the citation starts with an unknown author', () => {
    const refs = makeAuthorYearIndex([
      authorYearEntry(10, 'Brown', 2020, 'Brown and Jones. 2020. A paper title.'),
    ]);

    const rendered = renderBlock('(Unknown, Brown and Jones, 2020).', 'ltr', refs);

    expect(rendered.container.querySelector('.cite-chip')).toBeNull();
    expect(rendered.container.textContent).toContain('(Unknown, Brown and Jones, 2020).');
  });
});

function authorYearEntry(number: number, firstAuthor: string, year: number, rawText: string): ReferenceEntry {
  return {
    number,
    first_author: firstAuthor,
    year,
    raw_text: rawText,
    resolve_status: 'pending',
    already_in_library: false,
  };
}

function makeAuthorYearIndex(entries: ReferenceEntry[]): ReferenceIndex {
  return {
    citationStyle: 'author_year',
    numbers: new Set(entries.map((entry) => entry.number)),
    byNumber: new Map(entries.map((entry) => [entry.number, entry])),
  };
}
