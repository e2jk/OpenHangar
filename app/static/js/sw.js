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

/* Offline logbook editing (Phase 38): aircraft logbook list, workbench,
 * and the offline-changes page also get stale-while-revalidate so a single
 * online visit is enough to work fully offline afterwards. */
var SWR_PATTERNS = [
  /^\/aircraft\/\d+\/flights$/,
  /^\/aircraft\/\d+\/logbook\/offline$/,
  /^\/offline\/changes$/,
  /^\/pilot\/logbook\/offline$/
];

function _isSWRRoute(url) {
  if (SWR_ROUTES.indexOf(url.pathname) !== -1) return true;
  return SWR_PATTERNS.some(function (re) { return re.test(url.pathname); });
}

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE)
      .then(function (cache) {
        /* cache: 'reload' bypasses the HTTP cache — static assets are served
         * with a long immutable lifetime, so a plain fetch after an upgrade
         * would precache the previous version's files. */
        return cache.addAll(PRECACHE.map(function (u) {
          return new Request(u, { cache: 'reload' });
        }));
      })
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

  /* Cache-first for static assets.  ignoreSearch lets versioned page URLs
   * (?v=…) hit the unversioned precache entries — safe because every entry
   * in a given CACHE belongs to a single app version. */
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(req, { ignoreSearch: true }).then(function (cached) {
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

/* Offline logbook editing (Phase 38): a visit to the aircraft logbook list
 * while online precaches the workbench + offline-changes page URLs, so
 * having browsed the logbook once online is sufficient to work offline —
 * no page has to be manually opened first. */
self.addEventListener('message', function (e) {
  if (e.data && e.data.type === 'OH_PRECACHE' && Array.isArray(e.data.urls)) {
    e.waitUntil(
      caches.open(CACHE).then(function (cache) {
        return Promise.all(e.data.urls.map(function (u) {
          return fetch(u).then(function (resp) {
            if (resp.ok) return cache.put(u, resp.clone());
          }).catch(function () {});
        }));
      })
    );
  }
});
