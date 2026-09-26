import { describe, expect, it } from 'vitest';
import { blockDirection, documentDirection } from './documentDirection';

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
  it('lets a Latin-only block inside an RTL document choose its own flow', () => {
    expect(blockDirection('rtl', 'English heading only')).toBe('auto');
  });

  it('keeps Arabic text right-to-left even when it contains numbers', () => {
    expect(blockDirection('rtl', 'العنوان 2026')).toBe('rtl');
  });

  it('keeps every block left-to-right in an English document', () => {
    expect(blockDirection('ltr', 'العنوان 2026')).toBe('ltr');
  });
});
