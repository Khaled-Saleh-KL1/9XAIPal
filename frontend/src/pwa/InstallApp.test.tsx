import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { INSTALL } from '../landing/content';
import { InstallApp } from './InstallApp';
import { resetInstallCapture } from './installPrompt';

const ANDROID = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/126.0.0.0 Mobile Safari/537.36';
const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Version/17.5 Mobile/15E148 Safari/604.1';
const DESKTOP = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36';

function setNav(ua: string, extra: Record<string, unknown> = {}) {
  Object.defineProperty(navigator, 'userAgent', { value: ua, configurable: true });
  Object.defineProperty(navigator, 'maxTouchPoints', { value: 0, configurable: true });
  Object.defineProperty(navigator, 'standalone', { value: undefined, configurable: true });
  for (const [k, v] of Object.entries(extra)) Object.defineProperty(navigator, k, { value: v, configurable: true });
}

function fireInstallPrompt(outcome: 'accepted' | 'dismissed' = 'accepted') {
  const event = Object.assign(new Event('beforeinstallprompt', { cancelable: true }), {
    prompt: vi.fn().mockResolvedValue(undefined),
    userChoice: Promise.resolve({ outcome }),
  });
  act(() => { window.dispatchEvent(event); });
  return event;
}

describe('InstallApp', () => {
  beforeEach(() => resetInstallCapture());
  afterEach(() => setNav(DESKTOP));

  it('Android: captures the prompt, calls prompt() on click and hides after appinstalled', async () => {
    setNav(ANDROID);
    render(<InstallApp />);
    const event = fireInstallPrompt();
    expect(event.defaultPrevented).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: /get the app/i }));
    expect(event.prompt).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).toBeNull();
    act(() => { window.dispatchEvent(new Event('appinstalled')); });
    expect(screen.queryByRole('button', { name: /get the app/i })).toBeNull();
  });

  it('iOS: opens the two step Add to Home Screen guide', async () => {
    setNav(IPHONE, { maxTouchPoints: 5 });
    render(<InstallApp />);
    await userEvent.click(screen.getByRole('button', { name: /get the app/i }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveTextContent(/Share icon/);
    expect(dialog).toHaveTextContent(/Add to Home Screen/);
    expect(screen.getByRole('img', { name: 'Share icon' })).toBeInTheDocument();
  });

  it('desktop: shows the QR code and closes on Escape', async () => {
    setNav(DESKTOP);
    render(<InstallApp />);
    await userEvent.click(screen.getByRole('button', { name: /get the app/i }));
    const qr = await screen.findByAltText(/QR code/i);
    expect(qr).toHaveAttribute('src', '/qr-9xaipal.svg');
    expect(screen.getByRole('dialog')).toHaveTextContent('https://9xaipal.kl1.site/');
    await userEvent.keyboard('{Escape}');
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('standalone: renders nothing', () => {
    setNav(IPHONE, { standalone: true });
    const { container } = render(<InstallApp />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the required copy', () => {
    setNav(DESKTOP);
    render(<InstallApp />);
    expect(screen.getByText('Install on Android or iPhone. Updates arrive automatically.')).toBeInTheDocument();
  });

  it('Android without a captured prompt shows the Android guide, not the QR code', async () => {
    setNav(ANDROID, { maxTouchPoints: 5 });
    render(<InstallApp />);
    await userEvent.click(screen.getByRole('button', { name: /get the app/i }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('Open your browser menu');
    expect(dialog).toHaveTextContent(/Install app/);
    expect(dialog).toHaveTextContent(/Add to Home screen/);
    expect(screen.queryByAltText(/QR code/i)).toBeNull();
  });

  it('Android after the prompt was dismissed falls back to the Android guide', async () => {
    setNav(ANDROID, { maxTouchPoints: 5 });
    render(<InstallApp />);
    const event = fireInstallPrompt('dismissed');
    await userEvent.click(screen.getByRole('button', { name: /get the app/i }));
    expect(event.prompt).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole('button', { name: /get the app/i }));
    expect(await screen.findByRole('dialog')).toHaveTextContent('Open your browser menu');
  });
});

describe('install copy', () => {
  it('has no em dashes', () => {
    for (const value of Object.values(INSTALL)) expect(value).not.toContain('\u2014');
  });
});
