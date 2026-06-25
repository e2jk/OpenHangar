/* OpenHangar service worker — cache-first for static assets, stale-while-revalidate for nav pages */
var CACHE = '__SW_CACHE_VERSION__';

/* Static assets to pre-cache on SW install */
var PRECACHE = [
  '/static/css/auth.css',
  '/static/css/welcome.css',
  '/static/css/base.css',
  '/static/css/components.css',
  '/static/css/pwa.css',
  '/static/css/dashboard.css',
  '/static/vendor/bootstrap/css/bootstrap.min.css',
  '/static/vendor/bootstrap-icons/font/bootstrap-icons.min.css',
  '/static/vendor/bootstrap-icons/font/fonts/bootstrap-icons.woff2',
  '/static/vendor/bootstrap-icons/font/fonts/bootstrap-icons.woff',
  '/static/vendor/bootstrap/js/bootstrap.bundle.min.js',
  '/static/vendor/leaflet/leaflet.css',
  '/static/vendor/leaflet/leaflet.js',
  '/static/vendor/htmx/htmx.min.js',
  '/static/js/ui.js',
  '/static/js/pwa.js',
  '/static/js/airport_autocomplete.js',
  '/static/js/aircraft_type_autocomplete.js',
  '/static/favicon.svg',
  '/static/icons/icon.svg',
  '/static/pwa/offline.html',
];

/* Bottom-nav routes cached with stale-while-revalidate.
 * /flights/new is excluded — its CSRF token must always be fresh.
 * / is excluded — it returns different content depending on auth state
 * (landing page vs. dashboard), so stale-while-revalidate would serve the
 * wrong page after login or logout. */
var SWR_ROUTES = ['/aircraft/', '/pilot/logbook'];

function _isSWRRoute(url) {
  return SWR_ROUTES.indexOf(url.pathname) !== -1;
}

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

  /* Stale-while-revalidate for the 3 main read-only nav pages */
  if (req.mode === 'navigate' && _isSWRRoute(url)) {
    e.respondWith(
      caches.open(CACHE).then(function (cache) {
        return cache.match(req).then(function (cached) {
          /* Always kick off a background revalidation */
          var fetchPromise = fetch(req).then(function (response) {
            if (response.ok) {
              cache.put(req, response.clone());
            }
            return response;
          }).catch(function () {
            return caches.match('/static/pwa/offline.html');
          });
          /* Return cached immediately if available; otherwise wait for fetch */
          return cached || fetchPromise;
        });
      })
    );
    return;
  }

  /* Network-first for all other navigation and API calls; offline fallback for page loads */
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
