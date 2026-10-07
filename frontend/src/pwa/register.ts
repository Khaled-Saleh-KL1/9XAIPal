/**
 * Service worker registration plus the "new version" reload.
 *
 * A new worker takes over immediately (skipWaiting + clients.claim). To pick up
 * the new JS we reload ONCE, but never under the user's hands: the reload is
 * deferred until the page goes hidden and then becomes visible again.
 */

export function setupUpdateReload(
  container: Pick<ServiceWorkerContainer, 'addEventListener' | 'controller'>,
  doc: Pick<Document, 'addEventListener' | 'visibilityState'>,
  reload: () => void,
): void {
  // First install: clients.claim() fires controllerchange, but nothing is stale.
  const hadController = container.controller != null;
  let pending = false;
  let reloaded = false;
  let wasHidden = doc.visibilityState === 'hidden';

  container.addEventListener('controllerchange', () => {
    if (hadController) pending = true;
  });
  doc.addEventListener('visibilitychange', () => {
    if (doc.visibilityState === 'hidden') {
      wasHidden = true;
    } else if (wasHidden) {
      wasHidden = false;
      if (pending && !reloaded) {
        reloaded = true;
        reload();
      }
    }
  });
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
