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
    /* Capture phase: fires before HTMX's bubble-phase submit handler.
       Always cancel the original submit, then on accept re-fire via
       requestSubmit() so HTMX receives a clean event with no interference. */
    form.addEventListener('submit', function (e) {
      if (form.dataset.ohConfirmDone) { delete form.dataset.ohConfirmDone; return; }
      e.preventDefault();
      e.stopImmediatePropagation();
      if (confirm(form.dataset.confirm)) {
        form.dataset.ohConfirmDone = '1';
        form.requestSubmit();
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

/* ── Initial page load ──────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', _ohInit);
