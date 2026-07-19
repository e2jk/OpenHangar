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

/* Main nav routes cached with stale-while-revalidate — instant navigation,
 * refreshed in the background on every visit. A successful htmx write
 * (see pwa.js OH_INVALIDATE_NAV_CACHE) drops all of these immediately, so
 * a page never shows content that's stale because of your own edit.
 * / needs extra handling for its auth-state edges (see the fetch listener
 * below) before falling through to this same generic caching path.
 * /flights/new, /pilot/gps-import, /pilot/import and /config/ are excluded —
 * one-shot upload/wizard flows or pages needing live status.
 * WTF_CSRF_TIME_LIMIT is unset (see init.py) specifically so a CSRF token
 * embedded in a page served from this cache stays valid. */
var SWR_ROUTES = [
  '/',
  '/aircraft/',
  '/pilot/logbook',
  '/maintenance',
  '/pilot/tracks',
  '/pilot/profile',
  '/pilot/minimums',
  '/reservations/fleet/',
  '/config/users/'
];

/* Per-aircraft tabs (documents/expenses/costs/snags/airworthiness/W&B/tracks/
 * reservations) — same read-mostly-hub reasoning as SWR_ROUTES above, just
 * keyed by regex since the aircraft ID varies. Prefetched from within
 * aircraft/detail.html for the aircraft currently being viewed.
 * Also covers offline logbook editing (Phase 38): aircraft logbook list,
 * workbench, and the offline-changes page get stale-while-revalidate so a
 * single online visit is enough to work fully offline afterwards. */
var SWR_PATTERNS = [
  /^\/aircraft\/\d+$/,
  /^\/aircraft\/\d+\/flights$/,
  /^\/aircraft\/\d+\/logbook\/offline$/,
  /^\/aircraft\/\d+\/wb\/$/,
  /^\/aircraft\/\d+\/tracks$/,
  /^\/aircraft\/\d+\/documents$/,
  /^\/aircraft\/\d+\/expenses$/,
  /^\/aircraft\/\d+\/costs$/,
  /^\/aircraft\/\d+\/snags$/,
  /^\/aircraft\/\d+\/airworthiness\/$/,
  /^\/aircraft\/\d+\/reservations\/$/,
  /^\/offline\/changes$/,
  /^\/pilot\/logbook\/offline$/
];

function _isSWRRoute(url) {
  if (SWR_ROUTES.indexOf(url.pathname) !== -1) return true;
  return SWR_PATTERNS.some(function (re) { return re.test(url.pathname); });
}

/* TEMP DEBUG — remove once the SWR cache-hit investigation is done.
 * Tags every SWR-route response with an X-SW-Debug header so the actual
 * cache-hit/miss decision is visible directly in the Network tab's response
 * headers, instead of having to infer it from transfer size or timing. */
function _tagDebug(response, tag) {
  var headers = new Headers(response.headers);
  headers.set('X-SW-Debug', tag);
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: headers
  });
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

  /* / renders different content depending on auth state (landing vs.
   * dashboard) on the same URL, so a plain cache lookup could show the
   * wrong one. auth/routes.py appends ?_swr_fresh=1 to every post-login
   * redirect to / — that exact request always bypasses the cache and
   * re-populates it (under the canonical / key, marker stripped) so the
   * very first dashboard view after logging in is never the stale cached
   * landing page. The logout link (pwa.js) separately deletes the / entry
   * outright before navigating away, so a subsequent logged-out visit on
   * the same browser never shows a leftover dashboard either. Once past
   * these two edges, plain / requests behave like any other SWR route. */
  if (url.pathname === '/' && url.searchParams.has('_swr_fresh')) {
    e.respondWith(
      fetch(req).then(function (response) {
        if (response.ok) {
          var canonical = new Request(url.origin + '/');
          caches.open(CACHE).then(function (cache) {
            cache.put(canonical, response.clone());
          });
        }
        return response;
      }).catch(function () {
        return caches.match('/static/pwa/offline.html');
      })
    );
    return;
  }

  /* Stale-while-revalidate for the main read-only nav pages — deliberately
   * NOT gated on req.mode === 'navigate'. hx-boost intercepts nav-bar clicks
   * and issues its own fetch()/XHR (never mode:'navigate' — that mode is
   * reserved for real browser-driven navigations per the Fetch spec), and
   * <link rel="prefetch"> requests aren't 'navigate' either. Route
   * allowlisting (_isSWRRoute) is what keeps this safe, not the request mode. */
  if (_isSWRRoute(url)) {
    e.respondWith(
      caches.open(CACHE).then(function (cache) {
        return cache.match(req).then(function (cached) {
          /* Always kick off a background revalidation */
          var fetchPromise = fetch(req).then(function (response) {
            if (response.ok) {
              cache.put(req, response.clone());
            }
            return _tagDebug(response, 'miss-network'); // TEMP DEBUG
          }).catch(function () {
            return caches.match('/static/pwa/offline.html');
          });
          /* Return cached immediately if available; otherwise wait for fetch */
          return cached ? _tagDebug(cached, 'hit-cache') : fetchPromise; // TEMP DEBUG
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

  /* A write (POST/PUT/DELETE) may have changed what a cached nav page would
   * show — e.g. deleting a logbook entry while /pilot/logbook is cached.
   * Rather than track which route each endpoint affects (a mapping that
   * silently rots as routes change), just drop every currently-cached SWR
   * entry so the next visit to any of them is guaranteed fresh. Triggered
   * from pwa.js on every successful non-GET htmx request. */
  if (e.data && e.data.type === 'OH_INVALIDATE_NAV_CACHE') {
    e.waitUntil(
      caches.open(CACHE).then(function (cache) {
        return cache.keys().then(function (reqs) {
          return Promise.all(
            reqs
              .filter(function (r) { return _isSWRRoute(new URL(r.url)); })
              .map(function (r) { return cache.delete(r); })
          );
        });
      })
    );
  }
});
