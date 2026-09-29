export type TextDirection = 'ltr' | 'rtl' | 'auto';

export function documentDirection(language: string | null | undefined): TextDirection {
  switch (language?.trim().toLowerCase()) {
    case 'english':
    case 'en':
    case 'ltr':
      return 'ltr';
    case 'arabic':
    case 'mixed':
    case 'rtl':
      return 'rtl';
    default:
      return 'ltr';
  }
}

/**
 * Obsidian-style per-paragraph direction. Returns null when the text has no
 * letters at all (digits, punctuation, a bare formula), so the caller can
 * inherit the surrounding direction instead of forcing one.
 *
 * RTL when the first letter is Arabic-script, or Arabic letters make up at
 * least 30% of all letters; a lone Arabic word inside English prose stays LTR.
 * URLs are stripped first: a long English link inside an Arabic sentence must
 * not tip the balance.
 */
export function textDirection(text: string): 'rtl' | 'ltr' | null {
  const stripped = text.replace(/https?:\/\/\S+|www\.\S+/g, ' ');
  let letters = 0;
  let arabic = 0;
  let firstIsArabic: boolean | null = null;
  for (const ch of stripped) {
    if (!/\p{L}/u.test(ch)) continue;
    letters += 1;
    const isArabic = /\p{Script=Arabic}/u.test(ch);
    if (isArabic) arabic += 1;
    if (firstIsArabic === null) firstIsArabic = isArabic;
  }
  if (letters === 0) return null;
  if (arabic === 0) return 'ltr';
  return firstIsArabic || arabic / letters >= 0.3 ? 'rtl' : 'ltr';
}

export function blockDirection(baseDirection: TextDirection, text: string): TextDirection {
  return textDirection(text) ?? (baseDirection === 'rtl' ? 'rtl' : 'ltr');
}
