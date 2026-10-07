# Installable app (PWA)

> **Status:** current. Frontend only; nginx and the backend are untouched.

9XAIPal can be installed on Android and iPhone from the **Get the app** button on the landing page.
There is no store and no update button: every web deploy reaches installed apps on their next launch.

## Pieces

| What | Where |
| --- | --- |
| Manifest (name, scope, colours, icons) | `frontend/public/manifest.webmanifest` |
| Icons (192, 512, 512 maskable), made from the favicon "9" mark | `frontend/public/icons/`, regenerate with `python3 frontend/scripts/generate-pwa-icons.py` |
| Service worker | `frontend/public/sw.js` |
| Build id stamping | `swBuildId` plugin in `frontend/vite.config.ts` (replaces `__BUILD_ID__` in `dist/sw.js`) |
| Offline page | `frontend/public/offline.html` |
| Registration and update reload | `frontend/src/pwa/register.ts`, called from `main.tsx` (production only) |
| Platform detection (pure) | `frontend/src/pwa/platform.ts` |
| Install prompt capture | `frontend/src/pwa/installPrompt.ts` |
| Button and modals | `frontend/src/pwa/InstallApp.tsx`, rendered in `views/landing/Hero.tsx`; copy in `landing/content.ts` (`INSTALL`) |
| Static QR code of `https://9xaipal.kl1.site/` | `frontend/public/qr-9xaipal.svg` (pre-generated with the `qrcode` npm package, not a dependency) |

Button behaviour: Android/Chromium calls the captured `beforeinstallprompt` (without one, e.g. Samsung Internet, Firefox or after a dismissal, an Android guide shows the browser menu steps); iPhone/iPad (Safari, Chrome,
Firefox, and iPadOS reporting a Mac) opens the Share, Add to Home Screen guide; everything else opens a QR
modal. The button is hidden when running installed (`display-mode: standalone` or `navigator.standalone`).

On iPhone the installed app has its own storage and sign-in, separate from Safari, so users sign in once inside the installed app.

## Update model

- Navigations (HTML) are **network-first**. While online the app never serves a stale `index.html`, so a new
  deploy (new hashed `/assets/*` names) shows up on the next launch. Offline, `/offline.html` is shown.
- `/assets/*` are content-hashed, so they are **cache-first** in a cache named with the build id.
- `sw.js` gets a new build id on every `vite build`, so its bytes change per deploy and browsers install it.
  It calls `skipWaiting()` and `clients.claim()`; old caches (`9xaipal-*` with another build id) are deleted on activate.
- When a new worker takes over a page that was already controlled, the page reloads once, but only after the
  user leaves and returns (page hidden, then visible) and nothing is busy, never under their hands. Uploads and answer/note/study streams register in `src/pwa/busy.ts` (wired in `api.ts`); the reload waits until they finish. `registration.update()` runs
  whenever the app returns to the foreground.

## Never cached or intercepted

`/api/*`, `/static/*`, `/docs`, `/redoc`, `/openapi.json`, every non-GET request, and every cross-origin
request pass straight through to the network. Only GET navigations and `/assets/*` are handled.

## Forcing a refresh if ever needed

Closing and reopening the app is normally enough. Otherwise: browser DevTools, Application, Service Workers,
Unregister, then Clear storage; on a phone, remove the app and reinstall, or clear the site data in browser
settings. To retire the worker for everyone, deploy a `sw.js` that unregisters itself
(`self.registration.unregister()` in `activate`).

## Tests

`frontend/src/pwa/*.test.ts(x)`: platform detection, button paths, manifest and static files, `sw.js` routing
(evaluated in a fake worker scope), and the update-reload guard.
