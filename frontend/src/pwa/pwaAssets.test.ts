import { describe, expect, it } from 'vitest';
import manifestRaw from '../../public/manifest.webmanifest?raw';
import offline from '../../public/offline.html?raw';
import qr from '../../public/qr-9xaipal.svg?raw';
import indexHtml from '../../index.html?raw';

const icons = import.meta.glob('../../public/icons/*.png', { query: '?url', eager: true });


describe('manifest.webmanifest', () => {
  const manifest = JSON.parse(manifestRaw);
  it('has the required fields', () => {
    expect(manifest).toMatchObject({
      name: '9XAIPal',
      short_name: '9XAIPal',
      start_url: '/?source=pwa',
      scope: '/',
      display: 'standalone',
    });
    expect(manifest.description).toBeTruthy();
    expect(manifest.background_color).toMatch(/^#[0-9a-f]{6}$/i);
    expect(manifest.theme_color).toMatch(/^#[0-9a-f]{6}$/i);
  });
  it('declares 192, 512 and 512 maskable icons that exist', () => {
    const find = (sizes: string, purpose: string) => manifest.icons.find((i: any) => i.sizes === sizes && i.purpose === purpose);
    for (const icon of [find('192x192', 'any'), find('512x512', 'any'), find('512x512', 'maskable')]) {
      expect(icon).toBeTruthy();
      expect(icon.type).toBe('image/png');
      expect(Object.keys(icons)).toContain(`../../public${icon.src}`);
    }
  });
  it('is linked from index.html along with the iOS meta tags', () => {
    const html = indexHtml;
    expect(html).toContain('rel="manifest" href="/manifest.webmanifest"');
    for (const name of ['theme-color', 'apple-mobile-web-app-capable', 'mobile-web-app-capable', 'apple-mobile-web-app-status-bar-style', 'apple-mobile-web-app-title']) {
      expect(html).toContain(`name="${name}"`);
    }
  });
});

describe('static files', () => {
  it('ships offline page and QR without em dashes', () => {
    expect(qr).toContain('<svg');
    expect(offline).toContain("You're offline.");
    expect(offline).not.toContain('—');
  });
});
