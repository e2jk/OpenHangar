/* Compact/detailed view toggle for the pilot and aircraft logbook lists.
 *
 * Default is CSS-only (see .oh-logbook-compact/.oh-logbook-detailed in
 * components.css): the compact card list shows below the md breakpoint,
 * the full table above it — so the page is usable even if this script
 * fails to load. This module layers a persistent manual override on top:
 * the switch always reflects the *effective* view (CSS default, or a
 * previously saved choice) and, once touched, its choice is remembered
 * in localStorage and applied explicitly via inline styles (which win
 * over the CSS breakpoint rule regardless of viewport width) until
 * changed again.
 */
(function () {
  var STORAGE_KEY = 'oh-logbook-compact-view';
  var MOBILE_QUERY = '(max-width: 767.98px)';

  function init() {
    document.querySelectorAll('[data-oh-logbook-views]').forEach(function (root) {
      if (root.dataset.ohInited) return;
      root.dataset.ohInited = '1';

      var toggle = root.querySelector('[data-oh-logbook-toggle]');
      var compact = root.querySelector('[data-oh-logbook-compact]');
      var detailed = root.querySelector('[data-oh-logbook-detailed]');
      if (!toggle || !compact || !detailed) return;

      var stored = null;
      try {
        stored = window.localStorage.getItem(STORAGE_KEY);
      } catch (e) {
        /* storage disabled/unavailable — fall back to the CSS default */
      }
      var isCompact = stored !== null
        ? stored === '1'
        : window.matchMedia(MOBILE_QUERY).matches;

      function apply(compactOn) {
        /* Explicit values on both branches, not '' for the "on" case — ''
         * only clears the inline override and falls back to the CSS
         * breakpoint rule, which is the wrong display for the *other*
         * viewport (e.g. '' on .oh-logbook-compact still resolves to
         * display:none on a desktop-width screen, since that's the CSS
         * default there — silently hiding both views instead of showing
         * the one the switch says is active). */
        compact.style.display = compactOn ? 'block' : 'none';
        detailed.style.display = compactOn ? 'none' : 'block';
        toggle.checked = compactOn;
      }
      apply(isCompact);

      toggle.addEventListener('change', function () {
        try {
          window.localStorage.setItem(STORAGE_KEY, toggle.checked ? '1' : '0');
        } catch (e) {
          /* storage disabled/unavailable — toggle still works for this view */
        }
        apply(toggle.checked);
      });
    });
  }

  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
