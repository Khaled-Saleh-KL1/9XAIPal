/* 9XAIPal service worker. Hand written on purpose; see docs/05-features/14-pwa.md.
 *
 * Model: HTML is always network-first (a deploy shows up on the next launch),
 * /assets/* are content-hashed so they are cache-first, everything else
 * (API, uploads, docs, cross-origin, non-GET) is never touched.
 * BUILD_ID is replaced at build time (vite.config.ts), so every deploy changes
 * the bytes of this file and the browser installs the new worker.
 */
const BUILD_ID = '__BUILD_ID__';
const ASSET_CACHE = '9xaipal-assets-' + BUILD_ID;
const SHELL_CACHE = '9xaipal-shell-' + BUILD_ID;
const OFFLINE_URL = '/offline.html';
const NEVER_INTERCEPT = ['/api', '/static', '/docs', '/redoc', '/openapi.json'];

/** 'navigate' | 'asset' | 'pass' */
function route(request) {
  if (request.method !== 'GET') return 'pass';
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return 'pass';
  const path = url.pathname;
  if (NEVER_INTERCEPT.some((p) => path === p || path.startsWith(p + '/'))) return 'pass';
  if (request.mode === 'navigate') return 'navigate';
  if (path.startsWith('/assets/')) return 'asset';
  return 'pass';
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.add(new Request(OFFLINE_URL, { cache: 'reload' }))).then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith('9xaipal-') && k !== ASSET_CACHE && k !== SHELL_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const kind = route(event.request);
  if (kind === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => caches.match(OFFLINE_URL).then((r) => r || Response.error())),
    );
  } else if (kind === 'asset') {
    event.respondWith(
      caches.open(ASSET_CACHE).then((cache) =>
        cache.match(event.request).then(
          (hit) =>
            hit ||
            fetch(event.request).then((response) => {
              // nginx answers a missing /assets file with index.html and 200: never cache that.
              const type = (response.headers && response.headers.get('content-type')) || '';
              if (response.ok && response.status === 200 && !/text\/html/i.test(type)) {
                try {
                  Promise.resolve(cache.put(event.request, response.clone())).catch(() => {});
                } catch (e) {
                  /* caching is best effort */
                }
              }
              return response;
            }),
        ),
      ),
    );
  }
  // 'pass': no respondWith, the browser handles the request normally.
});
