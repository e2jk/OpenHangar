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
    form.addEventListener('submit', function (e) {
      if (!confirm(form.dataset.confirm)) {
        e.preventDefault();
      }
    });
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
