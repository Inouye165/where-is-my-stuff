const CACHE_NAME = 'wims-shell-v30';
const NEVER_CACHE_PATHS = new Set(['/docs', '/redoc', '/openapi.json', '/docs/oauth2-redirect']);
const APP_SHELL_URLS = [
  '/',
  '/index.html',
  '/manifest.webmanifest',
  '/css/app.css',
  '/js/app.js',
  '/js/api.js',
  '/js/carousel.js',
  '/js/dom.js',
  '/js/grid-designer.js',
  '/js/icons.js',
  '/js/mobile-views.js',
  '/js/modals.js',
  '/js/phone-capture.js',
  '/js/photo-compress.js',
  '/js/photo-editor.js',
  '/js/router.js',
  '/js/state.js',
  '/js/unsaved-guard.js',
  '/js/views.js',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/icon-512-maskable.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL_URLS)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/uploads/')) return;

  event.respondWith(
    fetch(req)
      .then((res) => {
        // Only keep clean static paths; tokenized URLs (capture.html?token=...) and API docs
        // would otherwise pile up as one cache entry per request.
        const cacheable = !url.search && !NEVER_CACHE_PATHS.has(url.pathname);
        if (cacheable && res && res.status === 200 && res.type === 'basic') {
          const copy = res.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(req, copy)).catch(() => {});
        }
        return res;
      })
      .catch(() =>
        caches.match(req).then((cached) => {
          if (cached) return cached;
          if (req.mode === 'navigate') return caches.match('/index.html');
          return new Response('', { status: 504, statusText: 'Offline' });
        })
      )
  );
});

