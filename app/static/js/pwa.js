/* OpenHangar PWA — service worker registration, offline indicator, install prompt */
(function () {
  'use strict';

  /* ── Service worker registration (skipped in debug/dev mode) ── */
  var _debug = window._oh_config && window._oh_config.debug;
  if ('serviceWorker' in navigator && !_debug) {
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .catch(function (err) {
        console.warn('[OH-PWA] SW registration failed:', err);
      });
  }

  /* Any successful write (htmx-boosted form POST/PUT/DELETE) may have
   * changed what a cached nav page would show next time — drop the SW's
   * cached nav pages so the next visit is guaranteed fresh rather than
   * showing what was on screen before this change. */
  document.addEventListener('htmx:afterRequest', function (e) {
    var cfg = e.detail && e.detail.requestConfig;
    var verb = cfg && cfg.verb;
    if (!verb || verb.toLowerCase() === 'get') return;
    if (!e.detail.successful) return;
    if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
      navigator.serviceWorker.controller.postMessage(Object.assign({ type: 'OH_INVALIDATE_NAV_CACHE' }, window.OhOfflineIdentity.current() || {}));
    }
  });

  /* auth/routes.py appends ?_swr_fresh=1 to the post-login/post-setup
   * redirect to / so the SW knows to bypass its cache for that one request
   * (see sw.js). The marker has done its job once the page shows it — scrub
   * it from the visible URL without adding a history entry or reloading.
   * Some of these redirects are followed by hx-boost as a body swap rather
   * than a real navigation (e.g. the setup wizard's forms aren't
   * hx-boost="false" the way login's are) — htmx updates the address bar
   * itself in that case, via its own history.replaceState AFTER
   * htmx:afterSettle fires, which would clobber a scrub done that early.
   * htmx:pushedIntoHistory (also used in ui.js) fires once htmx's own
   * history update has already happened, so this runs last and sticks. */
  function _scrubSwrFreshMarker() {
    if (window.location.pathname === '/' && window.location.search.indexOf('_swr_fresh') !== -1) {
      var _cleanUrl = new URL(window.location.href);
      _cleanUrl.searchParams.delete('_swr_fresh');
      window.history.replaceState(null, '', _cleanUrl.pathname + _cleanUrl.search + _cleanUrl.hash);
    }
  }
  _scrubSwrFreshMarker();
  document.addEventListener('htmx:pushedIntoHistory', _scrubSwrFreshMarker);

  /* ── Offline / online indicator ── */
  var _offlineBadge = document.getElementById('oh-pwa-offline-badge');

  function _setOfflineClass(offline) {
    document.body.classList.toggle('oh-offline', offline);
    /* _offlineBadge visibility is handled by the body.oh-offline CSS rule */
  }

  _setOfflineClass(!navigator.onLine);

  window.addEventListener('online', function () {
    _setOfflineClass(false);
  });
  window.addEventListener('offline', function () {
    _setOfflineClass(true);
  });

  /* The 'online' event is not reliable in every browser (Firefox's devtools
   * network throttling in particular can leave navigator.onLine/online-event
   * out of sync with real connectivity), so the badge can get stuck showing
   * "Offline" forever with no second chance to clear it — pwa.js only runs
   * once, since hx-boost swaps just the body and never re-executes scripts.
   * A successful htmx swap is independent proof the network is up: use it
   * as a fallback signal regardless of what navigator.onLine last reported. */
  document.addEventListener('htmx:afterSettle', function () {
    _setOfflineClass(false);
  });

  /* ── Install prompt ── */
  var _deferredPrompt = null;
  var _installBar = document.getElementById('oh-pwa-install-bar');
  var _installBtn = document.getElementById('oh-pwa-install-btn');
  var _installDismiss = document.getElementById('oh-pwa-install-dismiss');
  var _INSTALL_DISMISSED_KEY = 'oh-pwa-install-dismissed';

  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    if (localStorage.getItem(_INSTALL_DISMISSED_KEY)) return;
    _deferredPrompt = e;
    if (_installBar) _installBar.classList.add('visible');
  });

  if (_installBtn) {
    _installBtn.addEventListener('click', function () {
      if (!_deferredPrompt) return;
      _deferredPrompt.prompt();
      _deferredPrompt.userChoice.then(function () {
        _deferredPrompt = null;
        if (_installBar) _installBar.classList.remove('visible');
      });
    });
  }

  if (_installDismiss) {
    _installDismiss.addEventListener('click', function () {
      localStorage.setItem(_INSTALL_DISMISSED_KEY, '1');
      if (_installBar) _installBar.classList.remove('visible');
    });
  }

  /* ── Inline flash banner ── */
  function _showBanner(msg, iconClass, alertClass, id) {
    var container = document.querySelector('.page-content .container.mt-3') ||
                    (function () {
                      var c = document.createElement('div');
                      c.className = 'container mt-3';
                      var main = document.querySelector('.page-content');
                      if (main) main.prepend(c);
                      return c;
                    })();
    var div = document.createElement('div');
    div.className = 'alert ' + (alertClass || 'alert-info') + ' alert-dismissible fade show';
    div.setAttribute('role', 'alert');
    if (id) div.id = id;
    div.innerHTML = '<i class="bi ' + (iconClass || 'bi-cloud-check') + ' me-1"></i>' + msg +
      '<button type="button" class="btn-close" data-bs-dismiss="alert"></button>';
    container.prepend(div);
  }

  /* ── EE-09/EE-10 banner poll + periodic nav-cache refresh ──
   * base.html bakes the aviation-day/anniversary banners (and fires the
   * anniversary confetti) into server-rendered HTML, but that only ever
   * runs on a genuine full-page render. Once hx-boost, sw.js's stale-while-
   * revalidate SWR cache, or simply a browser tab left open across midnight
   * are in the picture, a user can go a whole day without a fresh render
   * and miss a once-a-year message entirely — which is what happened with
   * the PPL-anniversary banner. /api/banners is a tiny endpoint outside
   * sw.js's SWR/precache lists (the /api/ prefix is exempted, see
   * TestSWRRouteCoverage), so this poll always gets a live answer
   * regardless of how stale the page around it is. It also piggybacks a
   * periodic OH_REFRESH_NAV_CACHE nudge to the SW on the same wake-up, to
   * bound staleness for everything else the SW caches — not just banners. */
  var _BANNER_POLL_MS = 2 * 60 * 60 * 1000; /* 2h while the tab stays open */
  var _NAV_CACHE_REFRESH_MS = 24 * 60 * 60 * 1000;
  var _NAV_CACHE_REFRESH_KEY = 'oh-nav-cache-last-refresh';

  function _todayStamp() {
    return new Date().toISOString().slice(0, 10);
  }

  function _fireAnniversaryConfetti() {
    var colors = ['#ffd700', '#ff6b6b', '#4a6fa5', '#48bb78'];
    function fire() {
      confetti({ particleCount: 8, angle: 60, spread: 70, origin: { x: 0 }, colors: colors });
      confetti({ particleCount: 8, angle: 120, spread: 70, origin: { x: 1 }, colors: colors });
    }
    setTimeout(fire, 300);
    setTimeout(fire, 900);
    setTimeout(fire, 1500);
  }

  function _maybeRefreshNavCache() {
    if (!('serviceWorker' in navigator) || !navigator.serviceWorker.controller) return;
    var last = parseInt(localStorage.getItem(_NAV_CACHE_REFRESH_KEY) || '0', 10);
    if (Date.now() - last < _NAV_CACHE_REFRESH_MS) return;
    localStorage.setItem(_NAV_CACHE_REFRESH_KEY, String(Date.now()));
    navigator.serviceWorker.controller.postMessage(Object.assign({ type: 'OH_REFRESH_NAV_CACHE' }, window.OhOfflineIdentity.current() || {}));
  }

  function _pollBanners() {
    // #oh-logout-link only renders when logged_in is true (base.html) — the
    // same signal EE-06 already relies on elsewhere in this file. Checked
    // fresh on every call (not just once at page load) since a page can go
    // from logged-out to logged-in without a full reload (e.g. the login
    // form's hx-boosted submit). Skipping the fetch entirely when logged
    // out avoids a 401 the browser logs to the console regardless of how
    // the response is handled in JS — the setup wizard's e2e tests assert
    // zero console errors during that flow.
    if (!document.getElementById('oh-logout-link')) return;
    fetch('/api/banners', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;

        if (data.aviation_day_banner) {
          var aKey = 'oh-banner-shown-aviation-' + _todayStamp();
          if (!sessionStorage.getItem(aKey)) {
            sessionStorage.setItem(aKey, '1');
            /* Skip if base.html's own footer banner already rendered it
             * (id added there for exactly this check) — avoids a duplicate
             * on the very first load, where this poll and the SSR banner
             * both fire for the same page. */
            if (!document.getElementById('aviation-day-banner')) {
              _showBanner(data.aviation_day_banner, 'bi-airplane-engines', 'alert-warning', 'aviation-day-banner');
            }
          }
        }

        if (data.pilot_anniversary) {
          var pKey = 'oh-banner-shown-anniversary-' + _todayStamp();
          if (!sessionStorage.getItem(pKey)) {
            sessionStorage.setItem(pKey, '1');
            if (!document.getElementById('anniversary-banner')) {
              _showBanner(data.pilot_anniversary.message, 'bi-stars', 'alert-warning', 'anniversary-banner');
            }
          }
        }

        /* pilot_anniversary_confetti is a genuine once-a-day server flag
         * (see _compute_banners), so no client-side dedup needed here. */
        if (data.pilot_anniversary_confetti && window.confetti) {
          _fireAnniversaryConfetti();
        }
      })
      .catch(function () {});

    _maybeRefreshNavCache();
  }

  _pollBanners();
  setInterval(_pollBanners, _BANNER_POLL_MS);
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') _pollBanners();
  });

})();
