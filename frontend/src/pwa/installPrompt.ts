import { useEffect, useSyncExternalStore } from 'react';

/** Chromium's install prompt event (not in lib.dom). */
export interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform?: string }>;
}

let deferred: BeforeInstallPromptEvent | null = null;
let installed = false;
let listening = false;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

/** Idempotent. Call early (main.tsx) so an event fired before the landing page mounts is not lost. */
export function initInstallCapture(): void {
  if (listening || typeof window === 'undefined') return;
  listening = true;
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    deferred = event as BeforeInstallPromptEvent;
    emit();
  });
  window.addEventListener('appinstalled', () => {
    deferred = null;
    installed = true;
    emit();
  });
}

/** Test helper. */
export function resetInstallCapture(): void {
  deferred = null;
  installed = false;
}

const subscribe = (cb: () => void) => {
  listeners.add(cb);
  return () => listeners.delete(cb);
};

export function useInstallPrompt() {
  useEffect(() => {
    initInstallCapture();
  }, []);
  const event = useSyncExternalStore(subscribe, () => deferred, () => null);
  const wasInstalled = useSyncExternalStore(subscribe, () => installed, () => false);

  const promptInstall = async (): Promise<'accepted' | 'dismissed' | 'unavailable'> => {
    const current = deferred;
    if (!current) return 'unavailable';
    await current.prompt();
    const { outcome } = await current.userChoice;
    // The event can only be used once.
    deferred = null;
    if (outcome === 'accepted') installed = true;
    emit();
    return outcome;
  };

  return { canPrompt: event !== null, installed: wasInstalled, promptInstall };
}
