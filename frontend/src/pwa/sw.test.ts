import { beforeEach, describe, expect, it, vi } from 'vitest';
import source from '../../public/sw.js?raw';


type Handler = (event: any) => void;

function loadWorker(networkFails = false) {
  const handlers: Record<string, Handler> = {};
  const store = new Map<string, Map<string, any>>();
  const cacheFor = (name: string) => {
    if (!store.has(name)) store.set(name, new Map());
    const m = store.get(name)!;
    return {
      match: async (req: any) => m.get(typeof req === 'string' ? new URL(req, 'https://9xaipal.kl1.site').href : req.url),
      put: async (req: any, res: any) => { m.set(req.url, res); },
      add: async (req: any) => { m.set(new URL(req.url ?? req, 'https://9xaipal.kl1.site').href, { ok: true, offline: true }); },
    };
  };
  const caches = {
    open: async (n: string) => cacheFor(n),
    keys: async () => [...store.keys()],
    delete: async (n: string) => store.delete(n),
    match: async (req: any) => {
      for (const n of store.keys()) { const hit = await cacheFor(n).match(req); if (hit) return hit; }
      return undefined;
    },
  };
  const fetchMock = vi.fn(async (req: any) => {
    if (networkFails) throw new TypeError('offline');
    return { ok: true, url: req.url, clone() { return this; } };
  });
  const scope: any = {
    location: { origin: 'https://9xaipal.kl1.site' },
    addEventListener: (type: string, h: Handler) => { handlers[type] = h; },
    skipWaiting: vi.fn(),
    clients: { claim: vi.fn(async () => {}) },
  };
  class FakeRequest { constructor(public url: string, public init: any = {}) {} }
  const fn = new Function('self', 'caches', 'fetch', 'Request', 'Response', 'URL', source);
  fn(scope, caches, fetchMock, FakeRequest, { error: () => 'ERR' }, URL);
  const dispatch = async (req: { url: string; method?: string; mode?: string }) => {
    const request = { method: 'GET', mode: 'cors', ...req };
    let responded: Promise<any> | undefined;
    handlers.fetch({ request, respondWith: (p: Promise<any>) => { responded = p; } });
    return { handled: responded !== undefined, response: responded ? await responded : undefined };
  };
  return { handlers, store, caches, fetchMock, scope, dispatch };
}

const O = 'https://9xaipal.kl1.site';

describe('sw.js', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('has a build id placeholder for the build to stamp', () => {
    expect(source).toContain('__BUILD_ID__');
  });

  it.each([
    [`${O}/api/documents`, 'GET', 'cors'],
    [`${O}/api/auth/me`, 'GET', 'navigate'],
    [`${O}/static/uploads/a.pdf`, 'GET', 'cors'],
    [`${O}/docs`, 'GET', 'navigate'],
    [`${O}/openapi.json`, 'GET', 'cors'],
    [`${O}/assets/index-abc.js`, 'POST', 'cors'],
    ['https://other.example.com/assets/x.js', 'GET', 'cors'],
    ['https://other.example.com/', 'GET', 'navigate'],
    [`${O}/favicon.svg`, 'GET', 'no-cors'],
  ])('passes through %s (%s, %s) without respondWith', async (url, method, mode) => {
    const w = loadWorker();
    const { handled } = await w.dispatch({ url, method, mode });
    expect(handled).toBe(false);
    expect(w.fetchMock).not.toHaveBeenCalled();
  });

  it('navigations are network-first and never read the cache while online', async () => {
    const w = loadWorker();
    await (await w.caches.open('9xaipal-shell-x')).put({ url: `${O}/` }, { stale: true });
    const { handled, response } = await w.dispatch({ url: `${O}/library`, mode: 'navigate' });
    expect(handled).toBe(true);
    expect(w.fetchMock).toHaveBeenCalledTimes(1);
    expect(response.url).toBe(`${O}/library`);
  });

  it('navigations fall back to the precached offline page when the network fails', async () => {
    const w = loadWorker(true);
    await w.handlers.install({ waitUntil: (p: Promise<any>) => p });
    // install precaches inside the build-versioned shell cache
    const names = [...w.store.keys()];
    expect(names.some((n) => n.startsWith('9xaipal-shell-'))).toBe(true);
    const { response } = await w.dispatch({ url: `${O}/`, mode: 'navigate' });
    expect(response).toMatchObject({ offline: true });
  });

  it('/assets/* are cache-first: second request does not hit the network', async () => {
    const w = loadWorker();
    const req = { url: `${O}/assets/index-abc123.js` };
    await w.dispatch(req);
    expect(w.fetchMock).toHaveBeenCalledTimes(1);
    const second = await w.dispatch(req);
    expect(w.fetchMock).toHaveBeenCalledTimes(1);
    expect(second.response.url).toBe(req.url);
  });

  it('install skips waiting; activate claims clients and prunes old caches', async () => {
    const w = loadWorker();
    await w.caches.open('9xaipal-assets-old');
    await w.caches.open('unrelated-cache');
    let installed: Promise<any> | undefined;
    w.handlers.install({ waitUntil: (p: Promise<any>) => { installed = p; } });
    await installed;
    expect(w.scope.skipWaiting).toHaveBeenCalled();
    let activated: Promise<any> | undefined;
    w.handlers.activate({ waitUntil: (p: Promise<any>) => { activated = p; } });
    await activated;
    expect(w.scope.clients.claim).toHaveBeenCalled();
    const names = [...w.store.keys()];
    expect(names).not.toContain('9xaipal-assets-old');
    expect(names).toContain('unrelated-cache');
  });
});
