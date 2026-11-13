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
  '/static/js/offline_identity.js',
  '/static/js/offline_db.js',
  '/static/js/offline_logbook.js',
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
  '/pilot/stats',
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
 * Also covers the read-only offline logbooks: the aircraft logbook list and
 * its offline page (with the pilot one in SWR_PATTERNS too) are cached so a
 * single online visit is enough to read them offline afterwards. */
/* The aircraft slot matches a numeric id OR a registration (e.g. OO-GRN) —
 * mirrors AircraftRefConverter (app/utils.py), which the app's own links
 * use to generate registration-based URLs by default. Registrations are
 * always stored/rendered upper-case (see aircraft/routes.py's .upper() on
 * save), so [A-Z0-9][A-Z0-9-]* also safely excludes lower-case route verbs
 * like /new or /gps-import that live in the same path position elsewhere. */
var SWR_PATTERNS = [
  /^\/aircraft\/[A-Z0-9][A-Z0-9-]*$/,
  /^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/flights$/,
  /^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/logbook\/offline$/,
  /^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/wb\/$/,
  /^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/tracks$/,
  /^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/documents$/,
  /^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/expenses$/,
  /^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/costs$/,
  /^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/snags$/,
  /^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/refuels$/,
  /^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/reports\/utilization$/,
  /^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/maintenance$/,
  /^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/airworthiness\/$/,
  /^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/airworthiness\/adsb\/$/,
  /^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/airworthiness\/equipment\/$/,
  /^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/reservations\/$/,
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
  '/config/data-checks': 'admin worklist recomputed on each visit; a cached copy would still list differences just corrected or dismissed',
  '/flights/removed-gps-files': 'short-lived trash list; a cached copy would offer to restore files already restored or purged',
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
  [/\/maintenance\/import(\/.*)?$/, 'one-shot AMP-import wizard flow (upload and review) and its history page'],
  [/^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/maintenance\/amp\/export(\/pdf)?$/, 'printable AMP document snapshot and its PDF download, no repeat-visit value — same reasoning as the personal-minimums print page'],
  [/^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/airworthiness\/adsb\/print$/, 'printable AD/SB compliance-board snapshot, no repeat-visit value — same reasoning as the personal-minimums print page'],
  [/^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/amp\/revisions\/\d+\/pdf$/, 'binary PDF download of one saved AMP revision — same reasoning as the AMP export PDF'],
  [/^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/flights\/import(\/.*)?$/, 'one-shot airframe-logbook import wizard'],
  [/^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/flights\/\d+$/, 'individual flight detail — too many distinct instances, low repeat-visit value'],
  [/^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/reservations\/\d+$/, 'individual reservation detail — same reasoning'],
  [/^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/components\/\d+\/logbook$/, 'nested per-component leaf view — low repeat-visit value'],
  [/^\/pilot\/logbook\/\d+\/view$/, 'individual logbook-entry detail — same reasoning'],
  [/^\/flights\/\d+\/my-part$/, 'personal crew-slot + correction form of one shared flight — needs fresh state on every open, same reasoning as /edit'],
  [/^\/pilot\/minimums\/revision\/\d+$/, 'individual revision detail — same reasoning'],
  [/^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/owners$/, 'co-owner management form — needs fresh owner/share state on every open, same reasoning as /edit'],
  [/^\/config\/renters\/\d+\/account$/, 'shows live account balance — staleness here is about money, not a badge'],
  [/^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/owners\/billing$/, 'shows live co-owner capital balances — staleness here is about money, not a badge'],
  [/^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/owners\/\d+\/account$/, 'shows live account balance — staleness here is about money, not a badge'],
  [/^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/my-share$/, 'shows live account balance — staleness here is about money, not a badge'],
  [/^\/config\/users\/invite\/[^/]+$/, 'one-shot invite-acceptance page, token-scoped'],
  [/^\/reset-password\/[^/]+$/, 'auth form, same reasoning as /login'],
  [/^\/notifications\/snooze\/[^/]+$/, 'one-shot token-scoped confirm action, must show live snoozed/not-yet-snoozed state, not a stale cached copy'],
  [/^\/share\/[^/]+$/, 'public share link — a revoked token must actually stop working, not keep serving a stale cached copy to whoever had it cached'],
  [/^\/share\/[^/]+\/[^/]+$/, 'same public share link, with the registration as a cosmetic URL prefix — same reasoning'],
  [/^\/showcase\/[^/]+$/, 'public showcase link — same "revoked token must stop working" reasoning as /share'],
  [/^\/showcase\/[^/]+\/[^/]+$/, 'same public showcase link, with the registration as a cosmetic URL prefix — same reasoning'],
  [/^\/showcase\/[^/]+\/photos\/\d+\/img$/, 'showcase photo — same reasoning, must stop being servable once the token is revoked'],
  [/^\/squawk\/\d+$/, 'easter egg']
];

function _isSWRRoute(url) {
  if (SWR_ROUTES.indexOf(url.pathname) !== -1) return true;
  return SWR_PATTERNS.some(function (re) { return re.test(url.pathname); });
}

/* Same-origin guard for URLs that arrive via postMessage — the SW must never
 * fetch/cache a cross-origin URL on a page's say-so. */
function _isSameOrigin(u) {
  try {
    return new URL(u, self.location.origin).origin === self.location.origin;
  } catch (e) {
    return false;
  }
}

/* Identity metadata survives worker suspension, but never contains a bearer
 * credential. Private pages are separate from the public static-asset cache. */
var _identity = { owner: '', session: '' };
var _revision = 0;
var _metadataCache = CACHE + '-identity';
var _identityReady = caches.open(_metadataCache).then(function (cache) {
  return cache.match('/__oh_offline_identity__');
}).then(function (response) {
  return response ? response.json() : null;
}).then(function (identity) {
  if (identity) _identity = identity;
}).catch(function () { _identity = { owner: '', session: '' }; });
var _identityCommit = Promise.resolve();
function _sameIdentity(a, b) { return a.owner === b.owner && a.session === b.session; }
function _privateName(identity) { return CACHE + '-private-' + identity.owner + '-' + identity.session; }
function _fallback() { return caches.match('/static/pwa/offline.html'); }
function _changeIdentity(next) {
  if (_sameIdentity(next, _identity)) return _identityCommit;
  _identity = next;
  _revision += 1;
  var version = _revision;
  /* Serialize metadata writes so a slow retired A write cannot overwrite B's
   * persisted identity after B has already signed in. */
  _identityCommit = _identityCommit.catch(function () {}).then(function () {
    if (version !== _revision) return;
    return Promise.all([
      caches.open(_metadataCache).then(function (cache) {
        return cache.put('/__oh_offline_identity__', new Response(JSON.stringify(next)));
      }),
      caches.keys().then(function (names) {
        var currentName = _identity.owner ? _privateName(_identity) : '';
        return Promise.all(names.filter(function (name) {
          return name.indexOf(CACHE + '-private-') === 0 && name !== currentName;
        }).map(function (name) { return caches.delete(name); }));
      }),
      self.clients.matchAll({ includeUncontrolled: true }).then(function (clients) {
        if (version !== _revision) return;
        clients.forEach(function (client) {
          client.postMessage({ type: 'OH_IDENTITY_CHANGED', owner: next.owner, session: next.session });
        });
      })
    ]);
  });
  return _identityCommit;
}
function _persistedIdentity() {
  return caches.open(_metadataCache).then(function (cache) {
    return cache.match('/__oh_offline_identity__');
  }).then(function (response) {
    return response ? response.json() : { owner: '', session: '' };
  });
}
function _network(request) {
  if (request.credentials === 'omit') return fetch(request);
  var began = _revision;
  var startedIdentity = _identity;
  return fetch(request).then(function (response) {
    var next = { owner: response.headers.get('X-OH-Offline-Owner') || '',
      session: response.headers.get('X-OH-Offline-Session') || '' };
    if (began !== _revision && !_sameIdentity(next, _identity)) {
      /* An old account's in-flight response must not re-activate or render it
       * after a newer login/logout has already changed the global epoch. */
      throw new Error('offline-identity-retired');
    }
    /* The server's identity headers are authoritative. A forbidden page may
     * belong to an otherwise valid login (e.g. a nav prefetch), not a logout. */
    return _persistedIdentity().then(function (persisted) {
      /* A retiring worker must not replace a newer worker's login state. */
      if (!_sameIdentity(persisted, startedIdentity) && !_sameIdentity(persisted, next)) {
        throw new Error('offline-identity-retired');
      }
      return _changeIdentity(next).then(function () { return response; });
    });
  });
}
function _cachePrivate(request, response) {
  var identity = { owner: response.headers.get('X-OH-Offline-Owner') || '',
    session: response.headers.get('X-OH-Offline-Session') || '' };
  if (!response.ok || !identity.owner || !identity.session || !_sameIdentity(identity, _identity)) {
    return Promise.resolve(response);
  }
  var version = _revision;
  var name = _privateName(identity);
  var url = new URL(request.url);
  url.searchParams.delete('_swr_fresh');
  var key = new Request(url.href);
  return _persistedIdentity().then(function (persisted) {
    if (version !== _revision || !_sameIdentity(identity, persisted)) return;
    return caches.open(name).then(function (cache) {
      if (version !== _revision) return;
      return cache.put(key, response.clone());
    });
  }).then(function () { return _persistedIdentity(); }).then(function (persisted) {
    /* Check both this worker's epoch and the state shared with replacement workers. */
    if (version !== _revision || !_sameIdentity(identity, persisted)) return caches.delete(name);
  }).then(function () { return response; });
}
function _privateNavigation(request) {
  /* Network-first proves the current cookie still owns this namespace before
   * showing sensitive data online. Offline fallback remains automatic. */
  return _network(request).then(function (response) {
    if (response.status === 403 || response.status === 404) {
      var denied = new URL(request.url);
      denied.searchParams.delete('_swr_fresh');
      return caches.open(_privateName(_identity)).then(function (cache) {
        return cache.delete(denied.href);
      }).then(function () { return response; });
    }
    return _cachePrivate(request, response);
  }).catch(function () {
    if (!_identity.owner) return _fallback();
    return caches.open(_privateName(_identity)).then(function (cache) {
      var url = new URL(request.url);
      url.searchParams.delete('_swr_fresh');
      return cache.match(url.href).then(function (cached) { return cached || _fallback(); });
    });
  });
}
function _senderMatches(data) {
  return data.owner === _identity.owner && data.session === _identity.session;
}
function _assertionHeaders(data) {
  return { 'X-OH-Offline-Owner': data.owner || '', 'X-OH-Offline-Session': data.session || '' };
}

self.addEventListener('install', function (event) {
  event.waitUntil(caches.open(CACHE).then(function (cache) {
    return cache.addAll(PRECACHE.map(function (url) { return new Request(url, { cache: 'reload' }); }));
  }).then(function () { return self.skipWaiting(); }));
});
self.addEventListener('activate', function (event) {
  event.waitUntil(_identityReady.then(function () {
    return caches.keys().then(function (names) {
      return Promise.all(names.filter(function (name) {
        return name !== CACHE && name !== _metadataCache &&
          name !== (_identity.owner ? _privateName(_identity) : '');
      }).map(function (name) { return caches.delete(name); }));
    });
  }).then(function () {
    /* Remove any unscoped HTML left inside the old static cache as well. */
    return caches.open(CACHE).then(function (cache) {
      return cache.keys().then(function (keys) {
        return Promise.all(keys.filter(function (key) {
          return new URL(key.url).pathname.indexOf('/static/') !== 0;
        }).map(function (key) { return cache.delete(key); }));
      });
    });
  }).then(function () { return self.clients.claim(); }));
});
self.addEventListener('fetch', function (event) {
  var request = event.request;
  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.method === 'GET' && url.pathname.indexOf('/static/') === 0) {
    event.respondWith(caches.open(CACHE).then(function (cache) {
      return cache.match(request, { ignoreSearch: true }).then(function (cached) {
        return cached || fetch(request).then(function (response) {
          if (response.ok) cache.put(request, response.clone());
          return response;
        });
      });
    }));
    return;
  }
  /* Browsers may issue credential-free speculative prefetches for private
   * URLs. They neither establish login state nor receive private fallback. */
  if (request.credentials === 'omit') {
    event.respondWith(fetch(request));
    return;
  }
  event.respondWith(_identityReady.then(function () {
    if (url.pathname === '/logout') {
      return _changeIdentity({ owner: '', session: '' }).then(function () {
        return _network(request).catch(_fallback);
      });
    }
    if (request.method === 'GET' && _isSWRRoute(url)) return _privateNavigation(request);
    return _network(request).catch(function (error) {
      if (request.mode === 'navigate') return _fallback();
      throw error;
    });
  }));
});
self.addEventListener('message', function (event) {
  if (event.origin !== self.location.origin || !event.data) return;
  var data = event.data;
  event.waitUntil(_identityReady.then(function () {
    if (data.type === 'OH_GET_IDENTITY') {
      if (event.ports && event.ports[0]) event.ports[0].postMessage(_identity);
      return;
    }
    if (data.type === 'OH_VERIFY_IDENTITY') {
      return _network(new Request('/api/offline/identity', { headers: _assertionHeaders(data), credentials: 'same-origin' })).catch(function () {});
    }
    if (!_senderMatches(data)) return;
    if (data.type === 'OH_CLEAR_PRIVATE') return _changeIdentity({ owner: '', session: '' });
    if (data.type === 'OH_PRECACHE' && Array.isArray(data.urls)) {
      return Promise.all(data.urls.filter(_isSameOrigin).map(function (url) {
        return _network(new Request(url, { headers: _assertionHeaders(data), credentials: 'same-origin' })).then(function (response) {
          return _cachePrivate(new Request(url), response);
        }).catch(function () {});
      }));
    }
    if (data.type === 'OH_INVALIDATE_NAV_CACHE') return caches.delete(_privateName(_identity));
    if (data.type === 'OH_REFRESH_NAV_CACHE' && _identity.owner) {
      return caches.open(_privateName(_identity)).then(function (cache) {
        return cache.keys().then(function (keys) {
          return Promise.all(keys.map(function (key) {
            return _network(new Request(key, { headers: _assertionHeaders(data), credentials: 'same-origin' }))
              .then(function (response) { return _cachePrivate(key, response); }).catch(function () {});
          }));
        });
      });
    }
  }));
});
