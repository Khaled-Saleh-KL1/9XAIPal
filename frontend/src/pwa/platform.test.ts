import { describe, expect, it } from 'vitest';
import { detectPlatform, isStandalone } from './platform';

const UA = {
  androidChrome: 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36',
  iphoneSafari: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
  iosChrome: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/126.0.0.0 Mobile/15E148 Safari/604.1',
  ipadAsMac: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15',
  desktopChrome: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
};

describe('platform detection', () => {
  it('Android Chrome', () => expect(detectPlatform({ userAgent: UA.androidChrome, maxTouchPoints: 5 })).toBe('android'));
  it('iPhone Safari', () => expect(detectPlatform({ userAgent: UA.iphoneSafari, maxTouchPoints: 5 })).toBe('ios'));
  it('iOS Chrome', () => expect(detectPlatform({ userAgent: UA.iosChrome, maxTouchPoints: 5 })).toBe('ios'));
  it('iPad reporting a Mac user agent', () => expect(detectPlatform({ userAgent: UA.ipadAsMac, platform: 'MacIntel', maxTouchPoints: 5 })).toBe('ios'));
  it('a real Mac without touch is desktop', () => expect(detectPlatform({ userAgent: UA.ipadAsMac, platform: 'MacIntel', maxTouchPoints: 0 })).toBe('desktop'));
  it('desktop Chrome', () => expect(detectPlatform({ userAgent: UA.desktopChrome, maxTouchPoints: 0 })).toBe('desktop'));
  it('standalone via display-mode', () => expect(isStandalone({ userAgent: UA.androidChrome, displayModeStandalone: true })).toBe(true));
  it('standalone via navigator.standalone', () => expect(isStandalone({ userAgent: UA.iphoneSafari, navigatorStandalone: true })).toBe(true));
  it('browser tab is not standalone', () => expect(isStandalone({ userAgent: UA.desktopChrome, displayModeStandalone: false })).toBe(false));
});
