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

export function blockDirection(baseDirection: TextDirection, text: string): TextDirection {
  if (baseDirection !== 'rtl') return baseDirection;
  return /\p{Script=Arabic}/u.test(text) ? 'rtl' : 'auto';
}
