import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import type { PluggableList } from 'unified';
import { MARKDOWN_REHYPE, MARKDOWN_REMARK } from './markdown';
import { rehypeTextDirection } from './rehypeTextDirection';

function render(md: string, rehype: PluggableList = MARKDOWN_REHYPE): string {
  return renderToStaticMarkup(
    <ReactMarkdown remarkPlugins={MARKDOWN_REMARK} rehypePlugins={rehype}>
      {md}
    </ReactMarkdown>,
  );
}

describe('rehypeTextDirection', () => {
  it('is the last plugin in the shared pipeline', () => {
    expect(MARKDOWN_REHYPE[MARKDOWN_REHYPE.length - 1]).toBe(rehypeTextDirection);
  });

  it('marks an Arabic paragraph right-to-left', () => {
    expect(render('هذه فقرة عربية كاملة')).toContain('<p dir="rtl">');
  });

  it('marks an English paragraph left-to-right', () => {
    expect(render('This is an English paragraph.')).toContain('<p dir="ltr">');
  });

  it('marks an Arabic list and its items right-to-left', () => {
    const html = render('- العنصر الأول\n- العنصر الثاني');
    expect(html).toContain('<ul dir="rtl">');
    expect(html).toContain('<li dir="rtl">');
  });

  it('does not let inline code vote or receive a direction', () => {
    const html = render('استخدم الأمر `const x = 1` في الملف');
    expect(html).toContain('<p dir="rtl">');
    expect(html).toContain('<code>const x = 1</code>');
  });

  it('leaves a formula-only paragraph without a direction', () => {
    const html = render('$x^2$');
    expect(html).toMatch(/<p>/);
    expect(html).not.toMatch(/<p[^>]* dir=/);
  });

  it('leaves English rendering unchanged apart from dir="ltr"', () => {
    const sample = [
      '# A heading about models',
      '',
      'A paragraph with a [link](https://example.com) and `inline code` and math $E = mc^2$.',
      '',
      '- first bullet',
      '- second bullet',
      '',
      '> A quoted sentence in English.',
      '',
      '| Name | Value |',
      '| ---- | ----- |',
      '| alpha | one |',
      '| beta | two |',
      '',
      '```python',
      'print("hello")',
      '```',
    ].join('\n');
    const without = MARKDOWN_REHYPE.filter((p) => p !== rehypeTextDirection);
    const before = render(sample, without);
    const after = render(sample).replace(/ dir="ltr"/g, '');
    expect(after).toBe(before);
    expect(render(sample)).not.toContain('dir="rtl"');
  });
});
