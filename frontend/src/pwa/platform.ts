/** Pure install-environment detection. No DOM access: callers pass what they read. */

export interface PlatformInput {
  userAgent: string;
  /** navigator.platform */
  platform?: string;
  maxTouchPoints?: number;
  /** navigator.standalone (iOS Safari only) */
  navigatorStandalone?: boolean;
  /** matchMedia('(display-mode: standalone)').matches */
  displayModeStandalone?: boolean;
}

export type InstallPlatform = 'ios' | 'android' | 'desktop';

export function isStandalone(input: PlatformInput): boolean {
  return input.navigatorStandalone === true || input.displayModeStandalone === true;
}

export function isIOS(input: PlatformInput): boolean {
  // iPhone, iPad and iPod, including iOS Chrome (CriOS) and Firefox (FxiOS).
  if (/iPhone|iPad|iPod/i.test(input.userAgent)) return true;
  // iPadOS 13+ reports a Mac user agent; a touch screen gives it away.
  return /Macintosh|MacIntel/i.test(`${input.userAgent} ${input.platform ?? ''}`) && (input.maxTouchPoints ?? 0) > 1;
}

export function isAndroid(input: PlatformInput): boolean {
  return /Android/i.test(input.userAgent);
}

export function detectPlatform(input: PlatformInput): InstallPlatform {
  if (isIOS(input)) return 'ios';
  if (isAndroid(input)) return 'android';
  return 'desktop';
}

export function readPlatformInput(): PlatformInput {
  const nav = navigator as Navigator & { standalone?: boolean };
  let displayModeStandalone = false;
  try {
    displayModeStandalone = window.matchMedia('(display-mode: standalone)').matches;
  } catch {
    /* matchMedia unavailable */
  }
  return {
    userAgent: nav.userAgent,
    platform: nav.platform,
    maxTouchPoints: nav.maxTouchPoints,
    navigatorStandalone: nav.standalone,
    displayModeStandalone,
  };
}
