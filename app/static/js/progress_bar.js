/* OpenHangar top-of-page loading indicator for HTMX-boosted navigation.
 *
 * Every HTMX request in this app is a boosted link click or form submit (see
 * AGENTS.md — no template uses hx-get/hx-post directly), so a single pair of
 * document-level listeners covers every "click a link, wait for the page"
 * moment site-wide with no per-page wiring.
 *
 * htmx:afterRequest fires for both success and failure (offline included —
 * it's dispatched right alongside htmx:sendError/htmx:timeout), so it alone
 * is enough to always resolve the bar instead of leaving it stuck.
 */
(function () {
  'use strict';

  var SHOW_DELAY_MS = 150; /* avoid a flash on fast, sub-150ms requests */
  var CREEP_TO = '85%';
  var HOLD_AT_100_MS = 200;
  var FADE_MS = 300;

  var showTimer = null;
  var resetTimer = null;
  var active = false;

  function bar() { return document.getElementById('oh-progress-bar'); }

  function start() {
    if (active) return;
    active = true;
    clearTimeout(resetTimer);
    showTimer = setTimeout(function () {
      var el = bar();
      if (!el) return;
      el.style.transition = 'none';
      el.style.width = '0%';
      el.style.opacity = '1';
      /* Force layout so the width:0 starting point commits before the
       * transition to CREEP_TO is applied — otherwise the browser may
       * coalesce both style changes and skip the animation entirely. */
      void el.offsetWidth;
      el.style.transition = 'width 4s cubic-bezier(.1,.7,.3,1)';
      el.style.width = CREEP_TO;
    }, SHOW_DELAY_MS);
  }

  function finish() {
    if (!active) return;
    active = false;
    clearTimeout(showTimer);
    var el = bar();
    if (!el || el.style.opacity !== '1') return; /* never shown — fast request, nothing to clean up */
    el.style.transition = 'width .25s ease';
    el.style.width = '100%';
    resetTimer = setTimeout(function () {
      el.style.transition = 'opacity ' + FADE_MS + 'ms ease';
      el.style.opacity = '0';
      resetTimer = setTimeout(function () {
        el.style.transition = 'none';
        el.style.width = '0%';
      }, FADE_MS);
    }, HOLD_AT_100_MS);
  }

  document.addEventListener('htmx:beforeRequest', start);
  document.addEventListener('htmx:afterRequest', finish);
})();
