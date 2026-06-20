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
 */
document.addEventListener('DOMContentLoaded', function () {

  /* ── Clickable rows / divs ─────────────────────────────────────────── */
  document.querySelectorAll('[data-href]').forEach(function (el) {
    el.style.cursor = el.style.cursor || 'pointer';
    el.addEventListener('click', function () {
      window.location.href = el.dataset.href;
    });
  });

  /* ── Stop propagation (action cells inside clickable rows) ─────────── */
  document.querySelectorAll('[data-stop-prop]').forEach(function (el) {
    el.addEventListener('click', function (e) {
      e.stopPropagation();
    });
  });

  /* ── Delete / action confirmations ─────────────────────────────────── */
  document.querySelectorAll('form[data-confirm]').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      // dataset automatically HTML-decodes the attribute value
      if (!confirm(form.dataset.confirm)) {
        e.preventDefault();
      }
    });
  });

  /* ── Auto-submit on change (role selector, filter dropdowns) ────────── */
  document.querySelectorAll('[data-auto-submit]').forEach(function (el) {
    el.addEventListener('change', function () {
      // requestSubmit() fires the submit event (unlike submit()), so the
      // CSRF token injection listener in base.html runs before the POST.
      el.form.requestSubmit();
    });
  });

  /* ── Generic onchange dispatch (calls window[fnName]) ──────────────── */
  document.querySelectorAll('[data-onchange]').forEach(function (el) {
    el.addEventListener('change', function (e) {
      var fn = window[el.dataset.onchange];
      if (typeof fn === 'function') fn.call(el, e);
    });
  });

  /* ── Permissions preset buttons ─────────────────────────────────────── */
  document.querySelectorAll('[data-action="apply-preset"]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      if (window.applyPreset && window.roleDefaults) {
        window.applyPreset(btn.dataset.scope, window.roleDefaults[btn.dataset.preset]);
      }
    });
  });

  /* ── Copy reset URL to clipboard ────────────────────────────────────── */
  document.querySelectorAll('[data-action="copy-reset-url"]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      if (typeof window._copyResetUrl === 'function') window._copyResetUrl();
    });
  });

  /* ── Smart navbar: hide on scroll-down, reveal on scroll-up ─────────── */
  (function () {
    var navbar = document.querySelector('nav.navbar');
    if (!navbar) return;
    var lastY = window.scrollY;
    var hidden = false;
    var ticking = false;
    var navH = navbar.offsetHeight;
    window.addEventListener('resize', function () { navH = navbar.offsetHeight; }, { passive: true });

    function update() {
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
      if (!ticking) { requestAnimationFrame(update); ticking = true; }
    }, { passive: true });
  }());

});
