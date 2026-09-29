import { describe, expect, it } from 'vitest';
import { blockDirection, documentDirection, textDirection } from './documentDirection';

describe('documentDirection', () => {
  it('keeps English documents left-to-right', () => {
    expect(documentDirection('english')).toBe('ltr');
  });

  it('sets Arabic and mixed documents right-to-left', () => {
    expect(documentDirection('arabic')).toBe('rtl');
    expect(documentDirection('mixed')).toBe('rtl');
  });

  it('keeps legacy and unknown documents left-to-right', () => {
    expect(documentDirection(null)).toBe('ltr');
    expect(documentDirection('unknown')).toBe('ltr');
  });
});

describe('blockDirection', () => {
  it('sets a Latin-only block inside an RTL document left-to-right', () => {
    expect(blockDirection('rtl', 'English heading only')).toBe('ltr');
  });

  it('keeps Arabic text right-to-left even when it contains numbers', () => {
    expect(blockDirection('rtl', 'العنوان 2026')).toBe('rtl');
  });

  it('sets an Arabic block right-to-left even inside an English document', () => {
    expect(blockDirection('ltr', 'العنوان 2026')).toBe('rtl');
  });
});

describe('textDirection', () => {
  it('puts pure English left-to-right', () => {
    expect(textDirection('This is an English sentence.')).toBe('ltr');
  });

  it('puts pure Arabic right-to-left', () => {
    expect(textDirection('هذا نص عربي كامل')).toBe('rtl');
  });

  it('puts Arabic with an English term right-to-left', () => {
    expect(textDirection('استخدمنا نموذج Transformer في هذا البحث')).toBe('rtl');
  });

  it('puts mostly-Arabic text that starts in English right-to-left', () => {
    expect(textDirection('Transformer هو نموذج يعتمد على آلية الانتباه الذاتي')).toBe('rtl');
  });

  it('keeps mostly-English text with one Arabic word left-to-right', () => {
    expect(textDirection('The word كتاب means book in Arabic')).toBe('ltr');
  });

  it('returns null when there are no letters', () => {
    expect(textDirection('2026')).toBeNull();
    expect(textDirection('')).toBeNull();
  });

  it('ignores URLs when counting letters', () => {
    expect(textDirection('راجع https://example.com/a-very-long-english-url للمزيد')).toBe('rtl');
  });

  it('returns null for a URL-only string', () => {
    expect(textDirection('https://example.com/abc')).toBeNull();
  });
});
