/* OpenHangar service worker — cache-first for static assets; saved copies of the
 * main pages shown instantly for the life of the login (see "Instant screens" below) */
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

/* Main nav routes saved per login and shown instantly, refreshed in the
 * background on every visit (see "Instant screens" below). A successful save
 * marks them outdated, so a page never shows content that's stale because
 * of your own edit.
 * / needs extra handling for its auth-state edges (?_swr_fresh right after
 * signing in, see below) before falling through to this same caching path.
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
/* Also saved: blank forms to enter new data (the new flight, pilot logbook
 * entry, expense, refuel and snag forms; never a form editing existing
 * data, whose pre-filled values must be current). A date defaulting to
 * today is set by the browser (data-oh-default-today, date_default_today.js),
 * so a copy saved on another day still shows today. The GPS import hand-over
 * to the flight form carries NEVER_SAVED_PARAMS: always from the server.
 * And the Explore and passports hubs. Keep comments out of these arrays:
 * tests/test_pwa.py reads quotes and slashes in them as entries. */
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
  '/config/tenants',
  '/flights/new',
  '/pilot/logbook/new',
  '/passports/',
  '/places/',
  '/explore/where-next'
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
  /^\/pilot\/logbook\/offline$/,
  /^\/aircraft\/[A-Z0-9][A-Z0-9-]*\/(?:expenses\/add|refuels\/new|snags\/new)$/
];

/* Routes deliberately NOT cached, with why — exact-path matches.
 * Together with NOT_CACHED_PATTERNS below, this is the full audit trail:
 * every navigable GET page in the app is either in SWR_ROUTES/SWR_PATTERNS
 * above, or has an entry (exact or pattern) here explaining the call. */
var NOT_CACHED_ROUTES = {
  '/landegut': 'demo sign-in assigns a fresh slot or resumes one already assigned',
  '/login': 'auth form — a cached-but-nonfunctional login page while offline is worse than the existing offline fallback',
  '/setup': 'first-run auth wizard — same reasoning as /login',
  '/profile': 'account security settings (password/TOTP) — always show current state, never a cached one',
  '/documents/reconcile': 'admin utility, infrequent access',
  '/expenses/fee-checks': 'live expense worklist; linked and reviewed fees must disappear immediately',
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
  [/^\/places\/./, 'one place or collection, its members and invitations — too many distinct instances; the places hub itself is saved'],
  [/^\/passports\/./, 'one passport, its members and evidence — too many distinct instances; the passports hub itself is saved'],
  [/^\/explore\/(?!where-next$)./, 'per-passport and per-place explore views — same reasoning as passports'],
  [/^(?!\/flights\/new$|\/pilot\/logbook\/new$|\/aircraft\/[A-Z0-9][A-Z0-9-]*\/(?:expenses\/add|refuels\/new|snags\/new)$).*\/(new|add|create|edit|upload|service|charge|checkin|checkout|resolve|permissions|publish|settings|config|status)$/,
    'edit or action form — its pre-filled values must be current; other create forms not checked for saved-copy safety yet (the blank forms saved are listed in SWR_ROUTES and SWR_PATTERNS)'],
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
  [/^\/share\/[^/]+\/documents\/\d+$/, 'document of a full share link — same reasoning, must stop being servable once the token is revoked'],
  [/^\/squawk\/\d+$/, 'easter egg']
];

/* Query parameters marking a one-time hand-over (the GPS import's values
 * waiting in the server session for the new-flight form): never saved. */
var NEVER_SAVED_PARAMS = ['gps_review_return', 'gps_seg'];

function _isSWRRoute(url) {
  if (NEVER_SAVED_PARAMS.some(function (name) { return url.searchParams.has(name); })) return false;
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

/* ── Instant screens for the life of the login ──
 *
 * What is saved: the pages listed above, per account AND per login (a
 * separate cache namespace each), never another account's or a signed-out
 * visitor's. Each saved copy records when its request started.
 *
 * When a saved copy is shown at once (cold app start and in-app clicks
 * alike): while this login is valid, the server allows it
 * (OPENHANGAR_INSTANT_PAGES, sent as X-OH-Instant-Pages) and the copy is
 * not outdated. The server is still asked in the background every time: its
 * answer refreshes the copy, removes it on 403/404, and an ended or changed
 * login retires the whole namespace (the tab then locks, see
 * offline_identity.js). So other people's changes show on the next visit.
 *
 * When a copy is outdated: any successful save (a same-origin non-GET seen
 * here — htmx forms, plain forms, uploads, other tabs) records a "last
 * change" time; every copy saved before it is outdated and comes from the
 * server once on its next visit, then is instant again. Outdated copies are
 * kept, so they are still there to read offline. Right after a save, the
 * main pages (MAIN_PAGES, and each aircraft's page) are refreshed in the
 * background so they are current when opened; a page
 * opened while its refresh is still running waits for it — never a copy
 * older than the user's own last save. When the app opens or comes back to
 * the foreground (pwa.js), main pages older than MAIN_REFRESH_AGE_MS are
 * refreshed the same way.
 *
 * Pages the app asks the browser to prefetch (prefetch.js) only fill in
 * missing, outdated or older-than-MAIN_REFRESH_AGE_MS copies: a fresh copy
 * answers them without asking the server, so the ~10 prefetches of every
 * page view don't each cost a server request.
 *
 * Never saved: a page showing a one-time message (a flash toast, marked
 * data-oh-flash in base.html) — it would come back on every instant view.
 *
 * When saved pages are deleted: sign-out; the login reaching its end date
 * (X-OH-Login-Expires-In, kept with the identity so it is known without a
 * connection); the server reporting another or no login; another account
 * signing in. With instant screens off, pages come from the server first
 * and saved copies are only used without a connection.
 *
 * Why: like an online shop account, the saved pages belong to one login and
 * last as long as it does; on a computer other people use, signing out is
 * the user's job (see the user guide). */
var MAIN_PAGES = ['/', '/flights', '/flights/new', '/pilot/logbook', '/maintenance', '/aircraft/', '/passports/'];
/* Each aircraft's own page counts as a main page too. */
var AIRCRAFT_PAGE = /^\/aircraft\/[A-Z0-9][A-Z0-9-]*$/;
function _isMainPage(pathname) {
  return MAIN_PAGES.indexOf(pathname) !== -1 || AIRCRAFT_PAGE.test(pathname);
}
var MAIN_REFRESH_AGE_MS = 15 * 60 * 1000;
/* POSTs that only read (nothing to mark outdated). */
var READ_ONLY_POSTS = [/^\/flights\/photo-hints$/];
var SAVED_AT = 'X-OH-Saved-At';
var FLASH_MARKER = 'data-oh-flash';
var IDENTITY_KEY = '/__oh_offline_identity__';
var LOGIN_KEY = '/__oh_login_state__';

/* Identity metadata survives worker suspension, but never contains a bearer
 * credential. Private pages are separate from the public static-asset cache. */
var _identity = { owner: '', session: '' };
var _login = { expiresAt: 0, instant: false, changedAt: 0 };
var _revision = 0;
var _metadataCache = CACHE + '-identity';
function _noop() {}
function _readMeta(cache, key) {
  return cache.match(key).then(function (response) { return response ? response.json() : null; });
}
var _identityReady = caches.open(_metadataCache).then(function (cache) {
  return Promise.all([_readMeta(cache, IDENTITY_KEY), _readMeta(cache, LOGIN_KEY)]);
}).then(function (values) {
  if (values[0]) _identity = values[0];
  var login = values[1];
  if (login && _identity.owner && login.owner === _identity.owner && login.session === _identity.session) {
    _login = { expiresAt: login.expiresAt || 0, instant: !!login.instant, changedAt: login.changedAt || 0 };
  }
}).catch(function () { _identity = { owner: '', session: '' }; });
var _identityCommit = Promise.resolve();
function _sameIdentity(a, b) { return a.owner === b.owner && a.session === b.session; }
function _privateName(identity) { return CACHE + '-private-' + identity.owner + '-' + identity.session; }
function _fallback() { return caches.match('/static/pwa/offline.html'); }
function _saveLogin() {
  var state = Object.assign({ owner: _identity.owner, session: _identity.session }, _login);
  return caches.open(_metadataCache).then(function (cache) {
    return cache.put(LOGIN_KEY, new Response(JSON.stringify(state)));
  });
}
function _changeIdentity(next) {
  if (_sameIdentity(next, _identity)) return _identityCommit;
  _identity = next;
  /* A new login starts with nothing known about its lifetime or changes. */
  _login = { expiresAt: 0, instant: false, changedAt: 0 };
  _revision += 1;
  var version = _revision;
  /* Serialize metadata writes so a slow retired A write cannot overwrite B's
   * persisted identity after B has already signed in. */
  _identityCommit = _identityCommit.catch(function () {}).then(function () {
    if (version !== _revision) return;
    return Promise.all([
      caches.open(_metadataCache).then(function (cache) {
        return Promise.all([
          cache.put(IDENTITY_KEY, new Response(JSON.stringify(next))),
          cache.delete(LOGIN_KEY)
        ]);
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
/* Learn this login's remaining lifetime and whether instant screens are
 * allowed from a server answer. Persisted only when it actually changes. */
function _rememberLogin(response) {
  var left = Number(response.headers.get('X-OH-Login-Expires-In'));
  if (!_identity.owner || response.headers.get('X-OH-Login-Expires-In') === null || !isFinite(left)) {
    return Promise.resolve();
  }
  if (response.headers.get('X-OH-Offline-Owner') !== _identity.owner ||
      response.headers.get('X-OH-Offline-Session') !== _identity.session) return Promise.resolve();
  var expiresAt = Date.now() + left * 1000;
  var instant = response.headers.get('X-OH-Instant-Pages') === '1';
  if (instant === _login.instant && Math.abs(expiresAt - _login.expiresAt) < 60000) return Promise.resolve();
  _login.expiresAt = expiresAt;
  _login.instant = instant;
  return _saveLogin();
}
function _expired() {
  return !!_identity.owner && !!_login.expiresAt && Date.now() >= _login.expiresAt;
}
function _instant() {
  return !!_identity.owner && _login.instant && _login.expiresAt > Date.now();
}
function _retireExpired() {
  return _expired() ? _changeIdentity({ owner: '', session: '' }) : Promise.resolve();
}
function _markChanged(startedAt) {
  if (!_identity.owner || startedAt <= _login.changedAt) return Promise.resolve();
  _login.changedAt = startedAt;
  return _saveLogin();
}
function _savedAt(response) { return Number(response.headers.get(SAVED_AT) || 0); }
function _outdated(response) { return _savedAt(response) < _login.changedAt; }
function _stale(response, maxAge) {
  return _outdated(response) || Date.now() - _savedAt(response) > maxAge;
}
function _persistedIdentity() {
  return caches.open(_metadataCache).then(function (cache) {
    return cache.match(IDENTITY_KEY);
  }).then(function (response) {
    return response ? response.json() : { owner: '', session: '' };
  });
}
function _network(request) {
  if (request.credentials === 'omit') return fetch(request);
  var began = _revision;
  var startedIdentity = _identity;
  return fetch(request).then(function (response) {
    /* A server error says nothing about the login: its identity headers can
     * be missing (the request failed before they could be computed), which
     * must not read as a sign-out and lock every tab. */
    if (response.status >= 500 || response.type === 'opaqueredirect') return response;
    /* Sent with a login this browser has just replaced (password change) or
     * ended: the newer login's answers decide, not this late one (it used to
     * end the new login and refuse its first page). */
    if (response.headers.get('X-OH-Login-Replaced')) return response;
    var next = { owner: response.headers.get('X-OH-Offline-Owner') || '',
      session: response.headers.get('X-OH-Offline-Session') || '' };
    if (began !== _revision && !_sameIdentity(next, _identity)) {
      /* An old account's in-flight response must not re-activate or render it
       * after a newer login/logout has already changed the global epoch. A
       * signed-out page (no owner) has nothing private to hide: it is shown,
       * without changing the login state (the sign-in page right after a
       * sign-out used to become the offline page here). */
      if (!next.owner) return response;
      throw new Error('offline-identity-retired');
    }
    /* The server's identity headers are authoritative. A forbidden page may
     * belong to an otherwise valid login (e.g. a nav prefetch), not a logout. */
    return _persistedIdentity().then(function (persisted) {
      /* A retiring worker must not replace a newer worker's login state. */
      if (!_sameIdentity(persisted, startedIdentity) && !_sameIdentity(persisted, next)) {
        if (!next.owner) return response;
        throw new Error('offline-identity-retired');
      }
      return _changeIdentity(next).then(function () {
        return _rememberLogin(response).catch(_noop);
      }).then(function () { return response; });
    });
  });
}
/* Save a copy of a private page for this login, stamped with when its
 * request started (a refresh started before a save stays outdated). The
 * copy is taken at once, so the caller may hand `response` on meanwhile. */
function _cachePrivate(request, response, startedAt) {
  var identity = { owner: response.headers.get('X-OH-Offline-Owner') || '',
    session: response.headers.get('X-OH-Offline-Session') || '' };
  if (!response.ok || !identity.owner || !identity.session || !_sameIdentity(identity, _identity)) {
    return Promise.resolve(response);
  }
  var copy = response.clone();
  var version = _revision;
  var name = _privateName(identity);
  var key = new Request(_privateCacheKey(request));
  var headers = new Headers(copy.headers);
  headers.set(SAVED_AT, String(startedAt));
  return _persistedIdentity().then(function (persisted) {
    if (version !== _revision || !_sameIdentity(identity, persisted)) return;
    return copy.text().then(function (body) {
      /* A one-time message (flash toast) would come back on every instant
       * view: such a page is not saved; its next visit asks the server. */
      if (body.indexOf(FLASH_MARKER) !== -1) return;
      var stamped = new Response(body, { status: copy.status, statusText: copy.statusText, headers: headers });
      return caches.open(name).then(function (cache) {
        if (version !== _revision) return;
        return cache.put(key, stamped);
      });
    });
  }).then(function () { return _persistedIdentity(); }).then(function (persisted) {
    /* Check both this worker's epoch and the state shared with replacement workers. */
    if (version !== _revision || !_sameIdentity(identity, persisted)) return caches.delete(name);
  }).then(function () { return response; });
}
function _privateCacheKey(request) {
  var url = new URL(request.url);
  url.searchParams.delete('_swr_fresh');
  return url.href;
}
function _cachedForCurrentLogin(request, identity, revision) {
  /* Only this login's namespace, and only while it is still the current one:
   * never another account's pages, nor a retired login's. */
  if (!identity.owner || revision !== _revision || !_sameIdentity(identity, _identity)) {
    return Promise.resolve(undefined);
  }
  return caches.open(_privateName(identity)).then(function (cache) {
    return cache.match(_privateCacheKey(request));
  });
}
/* Requests to the server for a saved page still running, by cache key, so a
 * page opened mid-refresh waits for that answer instead of an older copy. */
var _inflight = {};
function _fetchAndSave(request) {
  var key = _privateCacheKey(request);
  var startedAt = Date.now();
  var network = _network(request).then(function (response) {
    if (response.status === 403 || response.status === 404) {
      return caches.open(_privateName(_identity)).then(function (cache) {
        return cache.delete(key);
      }).then(function () { return response; });
    }
    return _cachePrivate(request, response, startedAt);
  });
  var settled = network.then(_noop, _noop);
  _inflight[key] = settled;
  settled.then(function () { if (_inflight[key] === settled) delete _inflight[key]; });
  return network;
}
function _privateNavigation(request, event) {
  var identity = _identity;
  var revision = _revision;
  function fromNetwork() {
    /* Without a connection, this login's saved copy (even an outdated one)
     * beats the generic offline page. */
    return _fetchAndSave(request).catch(function () {
      if (!_identity.owner) return _fallback();
      return _cachedForCurrentLogin(request, _identity, _revision).then(function (cached) {
        return cached || _fallback();
      });
    });
  }
  /* Right after signing in (?_swr_fresh), or with instant screens off or
   * unknown yet: the server first. */
  if (new URL(request.url).searchParams.has('_swr_fresh') || !_instant()) return fromNetwork();
  return _cachedForCurrentLogin(request, identity, revision).then(function (cached) {
    var pending = _inflight[_privateCacheKey(request)];
    /* A <link rel="prefetch"> (prefetch.js: no-cors, no destination) only
     * fills in missing, outdated or old copies — a fresh one needs no check. */
    if (cached && request.mode === 'no-cors' && request.destination === '' &&
        !_stale(cached, MAIN_REFRESH_AGE_MS)) {
      return cached;
    }
    if (cached && !_outdated(cached)) {
      /* Shown at once; the server is still asked (unless a request for it
       * is already running), and the worker kept alive until its answer has
       * refreshed or removed the copy. */
      var check = pending || _fetchAndSave(request).then(_noop, _noop);
      if (event) event.waitUntil(check);
      return cached;
    }
    /* Missing or outdated while a refresh is running (e.g. right after a
     * save): wait for that answer rather than show an older copy. */
    if (!pending) return fromNetwork();
    return pending.then(function () {
      return _cachedForCurrentLogin(request, identity, revision);
    }).then(function (fresh) {
      return fresh && !_outdated(fresh) ? fresh : fromNetwork();
    });
  });
}
/* Refresh this login's saved copies of the pages `wanted(pathname)` accepts
 * (all saved pages if null) from the server, except `exceptHref`, and with
 * `maxAge` only those saved longer ago than that (or outdated). */
function _refreshSaved(wanted, exceptHref, maxAge) {
  if (!_identity.owner) return Promise.resolve();
  var identity = _identity;
  return caches.open(_privateName(identity)).then(function (cache) {
    return cache.keys().then(function (keys) {
      return Promise.all(keys.filter(function (key) {
        return key.url !== exceptHref && (!wanted || wanted(new URL(key.url).pathname));
      }).map(function (key) {
        var stale = maxAge ? cache.match(key).then(function (cached) {
          return !cached || _stale(cached, maxAge);
        }) : Promise.resolve(true);
        return stale.then(function (needed) {
          if (!needed || !_sameIdentity(identity, _identity)) return;
          return _fetchAndSave(new Request(key.url, {
            headers: _assertionHeaders(identity), credentials: 'same-origin'
          })).catch(_noop);
        });
      }));
    });
  });
}
/* A successful save: everything saved before it is outdated; the page the
 * save landed on (already fresh) is kept; the main pages are refreshed in
 * the background. */
function _afterSave(response, startedAt) {
  var landing = response.redirected && response.url ? new URL(response.url) : null;
  var copy = landing && landing.origin === self.location.origin && _isSWRRoute(landing)
    ? response.clone() : null;
  return _markChanged(startedAt).then(function () {
    return Promise.all([
      copy ? _cachePrivate(new Request(landing.href), copy, startedAt) : null,
      _refreshSaved(_isMainPage, copy ? _privateCacheKey(new Request(landing.href)) : null, 0)
    ]);
  }).catch(_noop);
}
function _save(request, event) {
  var startedAt = Date.now();
  return _network(request).then(function (response) {
    if (response.ok && _identity.owner) event.waitUntil(_afterSave(response, startedAt));
    return response;
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
  event.respondWith(_identityReady.then(_retireExpired).then(function () {
    if (url.pathname === '/logout') {
      return _changeIdentity({ owner: '', session: '' }).then(function () {
        return _network(request).catch(_fallback);
      });
    }
    if (request.method === 'GET' && _isSWRRoute(url)) return _privateNavigation(request, event);
    var reply = request.method !== 'GET' && request.method !== 'HEAD' &&
      !READ_ONLY_POSTS.some(function (re) { return re.test(url.pathname); })
      ? _save(request, event) : _network(request);
    return reply.catch(function (error) {
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
        return _fetchAndSave(new Request(url, { headers: _assertionHeaders(data), credentials: 'same-origin' }))
          .catch(function () {});
      }));
    }
    /* The app was opened or came back to the foreground (pwa.js). */
    if (data.type === 'OH_REFRESH_MAIN') return _refreshSaved(_isMainPage, null, MAIN_REFRESH_AGE_MS);
    if (data.type === 'OH_REFRESH_NAV_CACHE') return _refreshSaved(null, null, 0);
  }));
});
