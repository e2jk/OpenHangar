/**
 * Central event delegation — eliminates inline onclick/onsubmit/onchange
 * handlers so templates are compatible with a nonce-based Content-Security-Policy.
 *
 * Supported data attributes:
 *   data-href="URL"                   any element behaves like a link
 *   data-stop-prop                    click does not propagate (action cells in clickable rows)
 *   data-confirm="msg"               form submit requires confirm() — msg is HTML-decoded by the browser
 *   data-auto-submit                  input/select change auto-submits its form
 *   data-onchange="fnName"           change calls window[fnName](event)
 *   data-action="apply-preset"       permissions preset button
 *     data-scope="all|<aircraft_id>"
 *     data-preset="<role_key>"
 *   data-action="copy-reset-url"     copy-to-clipboard button for reset URL
 *   data-scroll-anchor="elementId"   form submit remembers a section to scroll
 *                                    back to after the resulting page (re)loads —
 *                                    for actions on long pages that would
 *                                    otherwise land back at the top
 *
 * HTMX compatibility: _ohInit() is called on both DOMContentLoaded and
 * htmx:afterSettle so that elements injected by HTMX body-swaps are wired up.
 * Event delegation on `document` is used wherever possible to avoid the need
 * for re-wiring. Per-element listeners (scroll, resize) are guarded with a
 * data attribute to prevent double-attachment.
 */

/* ── Per-element init guard ─────────────────────────────────────────────── */
var _ohInitialized = typeof WeakSet !== 'undefined' ? new WeakSet() : null;

function _ohMarkInit(el) {
  if (_ohInitialized) _ohInitialized.add(el);
}
function _ohIsInit(el) {
  return _ohInitialized ? _ohInitialized.has(el) : el.dataset.ohInit === '1';
}

/* ── Re-callable page init ──────────────────────────────────────────────── */
function _ohInit() {

  /* ── Clickable rows / divs ─────────────────────────────────────────── */
  /* Uses event delegation on document — no re-wiring needed after swap */

  /* ── Stop propagation (action cells inside clickable rows) ─────────── */
  document.querySelectorAll('[data-stop-prop]').forEach(function (el) {
    if (_ohIsInit(el)) return;
    _ohMarkInit(el);
    el.addEventListener('click', function (e) {
      e.stopPropagation();
    });
  });

  /* ── Delete / action confirmations ─────────────────────────────────── */
  document.querySelectorAll('form[data-confirm]').forEach(function (form) {
    if (_ohIsInit(form)) return;
    _ohMarkInit(form);
    /* Capture phase: fires before HTMX's bubble-phase submit handler so that
       a cancelled dialog prevents the XHR from ever being dispatched. On
       accept we do nothing — the event continues and HTMX handles it. */
    form.addEventListener('submit', function (e) {
      if (!confirm(form.dataset.confirm)) {
        e.preventDefault();
        e.stopImmediatePropagation();
      }
    }, true);
  });

  /* ── Auto-submit on change (role selector, filter dropdowns) ────────── */
  document.querySelectorAll('[data-auto-submit]').forEach(function (el) {
    if (_ohIsInit(el)) return;
    _ohMarkInit(el);
    el.addEventListener('change', function () {
      el.form.requestSubmit();
    });
  });

  /* ── Generic onchange dispatch (calls window[fnName]) ──────────────── */
  document.querySelectorAll('[data-onchange]').forEach(function (el) {
    if (_ohIsInit(el)) return;
    _ohMarkInit(el);
    el.addEventListener('change', function (e) {
      var fn = window[el.dataset.onchange];
      if (typeof fn === 'function') fn.call(el, e);
    });
  });

  /* ── Permissions preset buttons ─────────────────────────────────────── */
  document.querySelectorAll('[data-action="apply-preset"]').forEach(function (btn) {
    if (_ohIsInit(btn)) return;
    _ohMarkInit(btn);
    btn.addEventListener('click', function () {
      if (window.applyPreset && window.roleDefaults) {
        window.applyPreset(btn.dataset.scope, window.roleDefaults[btn.dataset.preset]);
      }
    });
  });

  /* ── Copy reset URL to clipboard ────────────────────────────────────── */
  document.querySelectorAll('[data-action="copy-reset-url"]').forEach(function (btn) {
    if (_ohIsInit(btn)) return;
    _ohMarkInit(btn);
    btn.addEventListener('click', function () {
      if (typeof window._copyResetUrl === 'function') window._copyResetUrl();
    });
  });

  /* ── Flash-message toasts (Bootstrap Toast component) ────────────────── */
  document.querySelectorAll('.toast-container .toast').forEach(function (el) {
    if (_ohIsInit(el)) return;
    _ohMarkInit(el);
    if (window.bootstrap && window.bootstrap.Toast) {
      new bootstrap.Toast(el).show();
    }
  });

  /* ── Remember a section to scroll back to (see restore step below) ─── */
  document.querySelectorAll('form[data-scroll-anchor]').forEach(function (form) {
    if (_ohIsInit(form)) return;
    _ohMarkInit(form);
    form.addEventListener('submit', function () {
      sessionStorage.setItem('ohScrollAnchor', form.dataset.scrollAnchor);
    });
  });

  /* ── Restore scroll position after a data-scroll-anchor submit ──────── */
  /* Runs unconditionally on every init (not per-element guarded): the
     sessionStorage key itself is the one-shot guard — it's removed as soon
     as it's read, so this only fires once per swap where it was set. */
  var ohScrollAnchor = sessionStorage.getItem('ohScrollAnchor');
  if (ohScrollAnchor) {
    sessionStorage.removeItem('ohScrollAnchor');
    var ohAnchorEl = document.getElementById(ohScrollAnchor);
    if (ohAnchorEl) ohAnchorEl.scrollIntoView();
  }

  /* ── QR code (TOTP setup on profile page) ──────────────────────── */
  var qrEl = document.getElementById('qr-container');
  if (qrEl && qrEl.dataset.totpUri && !_ohIsInit(qrEl) && typeof QRCode !== 'undefined') {
    _ohMarkInit(qrEl);
    new QRCode(qrEl, {
      text: qrEl.dataset.totpUri,
      width: 200, height: 200,
      colorDark: '#000000', colorLight: '#ffffff',
      correctLevel: QRCode.CorrectLevel.M
    });
  }

  /* ── Expense form: initial fuel-fields toggle ───────────────────── */
  if (document.getElementById('expense_type')) window.toggleFuelFields();

  /* ── Expense form: initial coverage-fields toggle ────────────────── */
  if (document.getElementById('expense_category')) window.toggleCoverageFields();

  /* ── Maintenance trigger form: initial type toggle ──────────────── */
  if (document.getElementById('type_hours')) window.toggleType();

  /* ── Tenant create: operating model description ─────────────────── */
  if (document.getElementById('om-descs-data')) window._updateOmDesc();

  /* ── Permissions: load role defaults JSON ───────────────────────── */
  var roleDataEl = document.getElementById('role-defaults-data');
  if (roleDataEl && !window.roleDefaults) {
    window.roleDefaults = JSON.parse(roleDataEl.textContent);
  }

  /* ── Airworthiness dashboard filter ─────────────────────────────── */
  var filterCont = document.getElementById('doc-filters');
  if (filterCont && !_ohIsInit(filterCont)) {
    _ohMarkInit(filterCont);
    var filterBtns = filterCont.querySelectorAll('[data-filter]');
    var tableRows  = document.querySelectorAll('#docs-table tbody tr');
    filterBtns.forEach(function (btn) {
      btn.addEventListener('click', function () {
        filterBtns.forEach(function (b) { b.classList.remove('active'); });
        btn.classList.add('active');
        var f = btn.dataset.filter;
        tableRows.forEach(function (tr) {
          tr.style.display = (!f || tr.dataset.status === f) ? '' : 'none';
        });
      });
    });
  }

  /* ── Milestone confetti (flights/list — EE-03) ──────────────────── */
  var confettiEl = document.getElementById('confetti-milestone');
  if (confettiEl && !_ohIsInit(confettiEl) && typeof confetti === 'function') {
    _ohMarkInit(confettiEl);
    console.log('[OpenHangar] EE-03 fleet hours milestone: ' + confettiEl.dataset.milestoneHours + ' flight hours reached');
    var _cfEnd = Date.now() + 3000;
    var _cfColors = ['#4a6fa5', '#f5a623', '#7ed321', '#bd10e0', '#d0021b'];
    (function _cfFrame() {
      confetti({ particleCount: 6, angle: 60, spread: 55, origin: { x: 0 }, colors: _cfColors });
      confetti({ particleCount: 6, angle: 120, spread: 55, origin: { x: 1 }, colors: _cfColors });
      if (Date.now() < _cfEnd) { requestAnimationFrame(_cfFrame); }
    })();
  }

  /* ── One-click upgrade polling (config page only) ──────────────────── */
  var _ohUpgradeBridge = document.getElementById('upgrade-poll-bridge');
  if (_ohUpgradeBridge && !_ohIsInit(_ohUpgradeBridge)) {
    _ohMarkInit(_ohUpgradeBridge);
    var _ohUpgradePollUrl  = _ohUpgradeBridge.dataset.pollUrl;
    var _ohUpgradeMsgFail  = _ohUpgradeBridge.dataset.msgFailed  || 'Upgrade failed';
    var _ohUpgradeMsgWait  = _ohUpgradeBridge.dataset.msgTimeout || 'Upgrade taking longer than expected';
    var _ohUpgradeStartMs  = Date.now();
    var _ohUpgradeActive   = false;
    var _ohUpgradeWasDown  = false;
    var _ohUpgradeFailN    = 0;

    function _ohSetUpgradeBanner(cls, html) {
      var el = document.getElementById('upgrade-progress-banner');
      if (el) { el.className = 'alert ' + cls + ' mb-4'; el.innerHTML = html; }
    }

    function _ohPollUpgrade() {
      if (Date.now() - _ohUpgradeStartMs > 5 * 60 * 1000) {
        _ohSetUpgradeBanner('alert-warning',
          '<i class="bi bi-exclamation-triangle me-2"></i>' + _ohUpgradeMsgWait);
        return;
      }
      fetch(_ohUpgradePollUrl, {credentials: 'same-origin'})
        .then(function(r) { return r.json(); })
        .then(function(data) {
          _ohUpgradeFailN = 0;
          var prevDown = _ohUpgradeWasDown;
          _ohUpgradeWasDown = false;
          if (data.status === 'done' ||
              (data.status === 'idle' && (_ohUpgradeActive || prevDown))) {
            window.location.reload();
            return;
          }
          if (data.status === 'failed') {
            _ohSetUpgradeBanner('alert-danger',
              '<i class="bi bi-exclamation-triangle me-2"></i>' + _ohUpgradeMsgFail);
            return;
          }
          if (data.status !== 'idle') { _ohUpgradeActive = true; }
          setTimeout(_ohPollUpgrade, 5000);
        })
        .catch(function() {
          _ohUpgradeFailN++;
          if (_ohUpgradeFailN >= 2) { _ohUpgradeWasDown = true; _ohUpgradeActive = true; }
          setTimeout(_ohPollUpgrade, 3000);
        });
    }
    _ohPollUpgrade();
  }

  /* ── Smart navbar: hide on scroll-down, reveal on scroll-up ──────────
   * Attached once to window; re-reads the navbar element each time so it
   * works after HTMX swaps a new navbar into the DOM. */
  if (!document.documentElement.dataset.ohNavScroll) {
    document.documentElement.dataset.ohNavScroll = '1';
    var lastY = window.scrollY;
    var hidden = false;
    var ticking = false;

    function _updateNav() {
      var navbar = document.querySelector('nav.navbar');
      if (!navbar) { ticking = false; return; }
      var navH = navbar.offsetHeight;
      var y = window.scrollY;
      if (y > lastY && y > navH && !hidden) {
        navbar.style.top = '-' + navH + 'px';
        hidden = true;
      } else if ((y < lastY || y <= navH) && hidden) {
        navbar.style.top = '';
        hidden = false;
      }
      lastY = y;
      ticking = false;
    }

    window.addEventListener('scroll', function () {
      if (!ticking) { requestAnimationFrame(_updateNav); ticking = true; }
    }, { passive: true });
  }
}

/* ── Page-specific window helpers (called via data-onchange or _ohInit) ─── */

window.toggleFuelFields = function () {
  var sel = document.getElementById('expense_type');
  var flds = document.getElementById('fuel-fields');
  if (sel && flds) flds.style.display = sel.value === 'fuel' ? '' : 'none';
};

window.toggleCoverageFields = function () {
  var sel = document.getElementById('expense_category');
  var flds = document.getElementById('coverage-fields');
  if (sel && flds) flds.style.display = sel.value === 'fixed' ? '' : 'none';
};

window.toggleType = function () {
  var isHours = !!(document.getElementById('type_hours') && document.getElementById('type_hours').checked);
  var cal = document.getElementById('calendar-fields');
  var hrs = document.getElementById('hours-fields');
  if (cal) cal.style.display = isHours ? 'none' : '';
  if (hrs) hrs.style.display = isHours ? '' : 'none';
};

window._copyResetUrl = function () {
  var el = document.getElementById('reset-url');
  if (!el) return;
  el.select();
  document.execCommand('copy');
};

window._updateOmDesc = function () {
  var dataEl = document.getElementById('om-descs-data');
  var descEl = document.getElementById('om-desc');
  var selEl  = document.getElementById('operating_model');
  if (!dataEl || !descEl || !selEl) return;
  var descs = JSON.parse(dataEl.textContent);
  descEl.textContent = descs[selEl.value] || '';
};

window.applyPreset = function (scope, mask) {
  [1, 2, 4, 8, 16, 32, 64, 128].forEach(function (b) {
    var cb = document.getElementById('bit_' + scope + '_' + b);
    if (cb) cb.checked = !!(mask & b);
  });
};

/* ── Document-level event delegation (runs once, survives HTMX swaps) ─── */
document.addEventListener('click', function (e) {
  /* data-href: make any element behave like a link */
  var el = e.target.closest('[data-href]');
  if (el) {
    /* Don't navigate when clicking a nested interactive element */
    if (e.target.closest('a, button, form, input, select, textarea')) return;
    window.location.href = el.dataset.href;
    return;
  }
});

/* ── Bootstrap component re-init after HTMX body swap ──────────────────── */
document.addEventListener('htmx:afterSettle', function () {
  /* Re-run per-element wiring for newly swapped content */
  _ohInit();

  /* Re-initialise Bootstrap tooltips in the new content */
  if (window.bootstrap && bootstrap.Tooltip) {
    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (el) {
      bootstrap.Tooltip.getOrCreateInstance(el);
    });
  }
});

/* ── Bottom-nav active state after HTMX navigation ─────────────────────── */
document.addEventListener('htmx:pushedIntoHistory', function (e) {
  var path = e.detail && e.detail.path ? e.detail.path : window.location.pathname;
  document.querySelectorAll('.oh-bottom-nav a').forEach(function (a) {
    var href = a.getAttribute('href') || '';
    /* Exact match for root; prefix match for sub-sections */
    var isActive = href === '/'
      ? path === '/'
      : path.startsWith(href);
    a.classList.toggle('active', isActive);
  });
});

/* ── HTMX body-swap pre-flight cleanup ──────────────────────────────────── */
document.addEventListener('htmx:beforeSwap', function () {
  /* HTMX's settle step calls setAttribute() to temporarily copy all attributes
   * from matching old elements (by ID) to new elements. With style-src-attr
   * 'none' CSP, this triggers violations for any element that has an inline
   * style attribute — Bootstrap modals, Leaflet map containers, etc. Stripping
   * inline styles from all ID'd elements just before the swap ensures HTMX
   * has no style attributes to copy, so setAttribute('style',…) is never
   * called during the settle step. */
  document.querySelectorAll('[id][style]').forEach(function (el) {
    el.removeAttribute('style');
  });

  /* Also clear Bootstrap modal artefacts that live outside the swapped
   * content: the backdrop div and the overflow/padding applied to <body>.
   * Without this they get serialised into HTMX history and restored as
   * visual noise (grey overlay, scrolling locked) on browser Back navigation. */
  document.querySelectorAll('.modal-backdrop').forEach(function (el) { el.remove(); });
  document.body.classList.remove('modal-open');
  document.body.removeAttribute('style');
  /* Bootstrap appends a .tooltip div to <body> when a tooltip is shown.
   * If the user navigates while a tooltip is visible it would persist into
   * the next page (orphaned, no anchor, no dismiss handler). Remove them. */
  document.querySelectorAll('.tooltip').forEach(function (el) { el.remove(); });
});

/* ── HTMX history: strip stale init markers before snapshot ─────────────── */
document.addEventListener('htmx:beforeHistorySave', function () {
  /* Before HTMX serialises the current DOM to localStorage, strip any
   * data-oh-inited markers that JS wrote at runtime. If they remain in the
   * snapshot, the restored page will appear already-initialised and all
   * module init() calls on htmx:historyRestore will return early. */
  document.querySelectorAll('[data-oh-inited]').forEach(function (el) {
    el.removeAttribute('data-oh-inited');
  });
  document.querySelectorAll('[data-oh-flight-form-inited]').forEach(function (el) {
    el.removeAttribute('data-oh-flight-form-inited');
  });

  /* Strip ALL inline style= attributes before the snapshot is written.
   *
   * History restore goes through Ve() → Le() directly, bypassing the
   * htmx:beforeSwap handler that normally cleans up inline styles for
   * forward swaps. Two separate violations can result:
   *
   * 1. Settle step (Le → Oe): copies style= FROM the current DOM elements
   *    to matching-ID elements in the stored content.  Stripping styles here
   *    ensures the current DOM is clean when Le() runs inside Wt().
   *
   * 2. Restore step (Le → task → Oe): after the settle delay HTMX reverts
   *    each element to its original stored attributes (s = t.cloneNode()
   *    taken before the settle).  If the stored snapshot itself carried
   *    inline styles (e.g. Leaflet map panes) they get re-applied here.
   *    Stripping styles before the snapshot is written prevents this too.
   *
   * Both violations carry style-src-attr 'none' CSP errors.  Removing the
   * attributes here is safe: JS re-initialises everything from scratch
   * through htmx:historyRestore → _ohInit() → each module's init(). */
  document.querySelectorAll('[style]').forEach(function (el) {
    el.removeAttribute('style');
  });
});

/* ── HTMX history restore: re-run all module inits ──────────────────────── */
document.addEventListener('htmx:historyRestore', function () {
  /* On browser Back/Forward, HTMX inserts the saved body HTML without going
   * through the normal request → swap → settle cycle, so htmx:afterSettle
   * never fires. Dispatch a synthetic one so every module that listens on
   * htmx:afterSettle re-initialises on the restored DOM.
   * Map files have their own htmx:historyRestore listener + WeakSet guard;
   * this covers the remaining modules (forms, autocompletes, etc.). */
  _ohInit();
  if (window.bootstrap && bootstrap.Tooltip) {
    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (el) {
      bootstrap.Tooltip.getOrCreateInstance(el);
    });
  }
  document.body.dispatchEvent(
    new CustomEvent('htmx:afterSettle', { bubbles: true, cancelable: false })
  );
});

/* ── Initial page load ──────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', _ohInit);
