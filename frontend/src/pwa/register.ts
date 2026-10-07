import { isBusy, subscribeIdle } from './busy';

/**
 * Service worker registration plus the "new version" reload.
 *
 * A new worker takes over immediately (skipWaiting + clients.claim). To pick up
 * the new JS we reload once per update, but never under the user's hands: the
 * reload waits until nothing is busy (upload or answer stream, see busy.ts) and
 * the page has gone hidden and become visible again.
 */

export function setupUpdateReload(
  container: Pick<ServiceWorkerContainer, 'addEventListener' | 'controller'>,
  doc: Pick<Document, 'addEventListener' | 'visibilityState'>,
  reload: () => void,
  busy: { isBusy: () => boolean; subscribeIdle: (cb: () => void) => unknown } = { isBusy, subscribeIdle },
): void {
  // First install: the first controllerchange is clients.claim(), nothing is stale.
  // Any later one is a real new deploy, even in a long-lived tab.
  let skipFirst = container.controller == null;
  let pending = false;
  let reloaded = false;
  let wasHidden = doc.visibilityState === 'hidden';
  let returned = false; // the user left and came back since the update landed

  const tryReload = () => {
    if (pending && returned && !reloaded && !busy.isBusy()) {
      reloaded = true;
      reload();
    }
  };

  container.addEventListener('controllerchange', () => {
    if (skipFirst) {
      skipFirst = false;
      return;
    }
    pending = true;
    returned = false;
  });
  doc.addEventListener('visibilitychange', () => {
    if (doc.visibilityState === 'hidden') {
      wasHidden = true;
    } else if (wasHidden) {
      wasHidden = false;
      returned = true;
      tryReload();
    }
  });
  busy.subscribeIdle(tryReload);
}

export function registerServiceWorker(): void {
  if (!import.meta.env.PROD || typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return;
  try {
    setupUpdateReload(navigator.serviceWorker, document, () => window.location.reload());
    const start = () => {
      navigator.serviceWorker
        .register('/sw.js', { scope: '/' })
        .then((registration) => {
          // Look for a new deploy whenever the app comes back to the foreground.
          document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') registration.update().catch(() => {});
          });
        })
        .catch(() => {
          /* The app works fine without a service worker. */
        });
    };
    if (document.readyState === 'complete') start();
    else window.addEventListener('load', start, { once: true });
  } catch {
    /* never break the page */
  }
}
