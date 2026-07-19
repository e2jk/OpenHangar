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
 * The /config/* hubs' live-looking values (version-check badge, disk/backup
 * sizes, upgrade-in-progress marker) are all informational or already
 * re-validated server-side on submit, so they're cached the same as every
 * other read-mostly hub below — a stale badge for one extra visit isn't a
 * real risk, and they're admin-only so contention is low regardless.
 * WTF_CSRF_TIME_LIMIT is unset (see init.py) specifically so a CSRF token
 * embedded in a page served from this cache stays valid.
 * Every other navigable GET page in the app is deliberately excluded — see
 * NOT_CACHED_ROUTES/NOT_CACHED_PATTERNS below for the full list and why.
 * tests/test_pwa.py::TestSWRRouteCoverage checks every route in the app
 * against these four lists, so a newly added page that isn't in any of them
 * fails CI instead of silently going uncached-and-unnoticed. */
var SWR_ROUTES = [
  '/',
  '/aircraft/',
  '/pilot/logbook',
  '/maintenance',
  '/pilot/tracks',
  '/pilot/profile',
  '/pilot/minimums',
  '/reservations/fleet/',
  '/flights',
  '/config/',
  '/config/users/',
  '/config/notifications/',
  '/config/renters/',
  '/config/tenants'
];

/* Per-aircraft tabs (documents/expenses/costs/snags/maintenance/airworthiness/
 * W&B/tracks/reservations) — same read-mostly-hub reasoning as SWR_ROUTES
 * above, just keyed by regex since the aircraft ID varies. Prefetched from
 * within aircraft/detail.html for the aircraft currently being viewed.
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
  /^\/aircraft\/\d+\/maintenance$/,
  /^\/aircraft\/\d+\/airworthiness\/$/,
  /^\/aircraft\/\d+\/reservations\/$/,
  /^\/offline\/changes$/,
  /^\/pilot\/logbook\/offline$/
];

/* Routes deliberately NOT cached, with why — exact-path matches.
 * Together with NOT_CACHED_PATTERNS below, this is the full audit trail:
 * every navigable GET page in the app is either in SWR_ROUTES/SWR_PATTERNS
 * above, or has an entry (exact or pattern) here explaining the call. */
var NOT_CACHED_ROUTES = {
  '/login': 'auth form — a cached-but-nonfunctional login page while offline is worse than the existing offline fallback',
  '/setup': 'first-run auth wizard — same reasoning as /login',
  '/profile': 'account security settings (password/TOTP) — always show current state, never a cached one',
  '/documents/reconcile': 'admin utility, infrequent access',
  '/pilot/minimums/history': 'revision history list, infrequent access',
  '/pilot/minimums/print': 'printable snapshot, no repeat-visit value',
  '/my/account': 'shows live account balance — staleness here is about money, not a badge',
  '/hangar/secret': 'easter egg',
  '/not-yet-implemented': 'stub page for unshipped features',
  '/pwa/shared': 'one-shot Share Target landing flow'
};

/* Routes deliberately NOT cached, with why — pattern matches (dynamic
 * segments, or a shared reason covering several routes at once). */
var NOT_CACHED_PATTERNS = [
  [/\/(new|add|create|edit|upload|service|charge|checkin|checkout|resolve|permissions|publish|settings|config|status)$/,
    'one-shot create/edit/action form — needs fresh state on every open, not revisited'],
  [/\/gps-import(\/.*)?$/, 'one-shot GPS-import wizard flow'],
  [/\/logbook\/import(\/.*)?$/, 'one-shot logbook-import wizard flow'],
  [/^\/aircraft\/\d+\/flights\/import$/, 'one-shot airframe-logbook import wizard'],
  [/^\/aircraft\/\d+\/flights\/\d+$/, 'individual flight detail — too many distinct instances, low repeat-visit value'],
  [/^\/aircraft\/\d+\/reservations\/\d+$/, 'individual reservation detail — same reasoning'],
  [/^\/aircraft\/\d+\/components\/\d+\/logbook$/, 'nested per-component leaf view — low repeat-visit value'],
  [/^\/pilot\/logbook\/\d+\/view$/, 'individual logbook-entry detail — same reasoning'],
  [/^\/pilot\/minimums\/revision\/\d+$/, 'individual revision detail — same reasoning'],
  [/^\/config\/renters\/\d+\/account$/, 'shows live account balance — staleness here is about money, not a badge'],
  [/^\/config\/users\/invite\/[^/]+$/, 'one-shot invite-acceptance page, token-scoped'],
  [/^\/reset-password\/[^/]+$/, 'auth form, same reasoning as /login'],
  [/^\/share\/[^/]+$/, 'public share link — a revoked token must actually stop working, not keep serving a stale cached copy to whoever had it cached'],
  [/^\/squawk\/\d+$/, 'easter egg']
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
