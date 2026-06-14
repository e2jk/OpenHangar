/* OpenHangar service worker — cache-first for static assets, network-first for pages */
var CACHE = '__SW_CACHE_VERSION__';

/* Static assets to pre-cache on SW install */
var PRECACHE = [
  '/static/css/base.css',
  '/static/css/components.css',
  '/static/css/pwa.css',
  '/static/vendor/bootstrap/css/bootstrap.min.css',
  '/static/vendor/bootstrap-icons/font/bootstrap-icons.min.css',
  '/static/vendor/bootstrap-icons/font/fonts/bootstrap-icons.woff2',
  '/static/vendor/bootstrap-icons/font/fonts/bootstrap-icons.woff',
  '/static/vendor/bootstrap/js/bootstrap.bundle.min.js',
  '/static/js/ui.js',
  '/static/js/pwa.js',
  '/static/favicon.svg',
  '/static/icons/icon.svg',
  '/static/pwa/offline.html',
];

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE)
      .then(function (cache) { return cache.addAll(PRECACHE); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(
          keys
            .filter(function (k) { return k !== CACHE; })
            .map(function (k) { return caches.delete(k); })
        );
      })
      .then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (e) {
  var req = e.request;

  /* Ignore non-GET and cross-origin requests */
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  /* Cache-first for static assets */
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(req).then(function (cached) {
        if (cached) return cached;
        return fetch(req).then(function (response) {
          var clone = response.clone();
          caches.open(CACHE).then(function (c) { c.put(req, clone); });
          return response;
        });
      })
    );
    return;
  }

  /* Network-first for navigation and API; offline fallback for page loads */
  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req).catch(function () {
        return caches.match('/static/pwa/offline.html');
      })
    );
    return;
  }
});

/* Background sync: notify open clients to flush the IndexedDB queue */
self.addEventListener('sync', function (e) {
  if (e.tag === 'oh-flight-sync') {
    e.waitUntil(
      self.clients.matchAll({ includeUncontrolled: true }).then(function (clients) {
        clients.forEach(function (client) {
          client.postMessage({ type: 'OH_SYNC_REQUESTED' });
        });
      })
    );
  }
});
