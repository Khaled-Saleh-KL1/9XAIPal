import { renderHook, waitFor } from '@testing-library/react';
import { renderToStaticMarkup } from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PluggableList } from 'unified';
import type { ReferenceEntry } from '../api';
import { MARKDOWN_REHYPE, MARKDOWN_REMARK } from './markdown';
import { remarkAuthorYearCitationRefs, remarkCitationRefs, useReferences } from './references';

const numericRemark = [
  ...MARKDOWN_REMARK,
  [remarkCitationRefs, new Set([1, 2, 3])],
] as PluggableList;

function renderWithNumericReferences(markdown: string): string {
  return renderToStaticMarkup(
    <ReactMarkdown remarkPlugins={numericRemark} rehypePlugins={MARKDOWN_REHYPE}>
      {markdown}
    </ReactMarkdown>,
  );
}

function renderWithAuthorYearReferences(markdown: string, entries: Map<number, ReferenceEntry>): string {
  const remarkPlugins = [
    ...MARKDOWN_REMARK,
    [remarkAuthorYearCitationRefs, entries],
  ] as PluggableList;
  return renderToStaticMarkup(
    <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={MARKDOWN_REHYPE}>
      {markdown}
    </ReactMarkdown>,
  );
}

const referenceEntry: ReferenceEntry = {
  number: 3,
  raw_text: 'Smith. 2020. A paper title.',
  resolve_status: 'pending',
  first_author: null,
  year: null,
  resolved_title: null,
  resolved_authors: null,
  resolved_year: null,
  resolved_pdf_url: null,
  search_query: null,
  s2_url: null,
  arxiv_url: null,
  already_in_library: false,
  existing_document_id: null,
};

afterEach(() => vi.unstubAllGlobals());

describe('bibliography citations through the shared markdown pipeline', () => {
  it('keeps numeric [3] and [1, 2] citation markers', () => {
    const html = renderWithNumericReferences('See [3] and [1, 2].');

    expect(html).toContain('<span class="citation-ref" data-numbers="3">[3]</span>');
    expect(html).toContain('<span class="citation-ref" data-numbers="1,2">[1, 2]</span>');
  });

  it('keeps author-year marker attributes through rehype-sanitize', () => {
    const entry: ReferenceEntry = {
      ...referenceEntry,
      number: 1,
      first_author: 'Abdelali',
      year: 2024,
      raw_text: 'Ahmed Abdelali et al. 2024. LAraBench title.',
    };

    const html = renderWithAuthorYearReferences('(Abdelali et al., 2024)', new Map([[1, entry]]));

    expect(html).toContain('<span class="citation-ref" data-numbers="1">Abdelali et al., 2024</span>');
  });
});

describe('useReferences citation style', () => {
  it('exposes author_year from the bibliography response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        references: [{ ...referenceEntry, first_author: 'Smith', year: 2020 }],
        document_id: 'paper',
        citation_style: 'author_year',
      }),
    }));

    const { result } = renderHook(() => useReferences('paper', true));

    await waitFor(() => {
      expect((result.current as typeof result.current & { citationStyle?: string }).citationStyle).toBe('author_year');
    });
    expect(result.current.numbers).toEqual(new Set([3]));
  });

  it('defaults a missing style to numeric', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ references: [referenceEntry], document_id: 'paper' }),
    }));

    const { result } = renderHook(() => useReferences('paper', true));

    await waitFor(() => {
      expect((result.current as typeof result.current & { citationStyle?: string }).citationStyle).toBe('numeric');
    });
  });
});
