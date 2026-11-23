/* Re-apply <link rel="prefetch"> hints after every navigation, including
 * hx-boost swaps. htmx's makeFragment() strips <head> out of every boosted
 * response before swapping (only <title> survives) — so a <link
 * rel="prefetch"> declared in a page's own {% block head %} only ever
 * fires once, on the browsing session's very first real page load. Every
 * page visited afterwards via hx-boost never gets its own prefetch set
 * applied at all, regardless of which page it is.
 *
 * Each page instead renders its prefetch URLs into one or more
 * <div hidden class="oh-prefetch-urls" data-urls="[...]"> elements in the
 * (swappable) body — base.html's shared nav list plus each page's own
 * per-page list — and this module reads them back out and inserts real
 * <link rel="prefetch"> elements into document.head itself on every
 * DOMContentLoaded/htmx:afterSettle. A plain element with a data-*
 * attribute, not a <script type="application/json"> block, on purpose:
 * this app's htmx-config sets allowScriptTags:false, under which htmx
 * strips EVERY <script> tag from a boosted response before swapping it
 * in — type="application/json" included, even though it never executes —
 * so a script-tag data bridge would vanish from the DOM on every boosted
 * navigation exactly like the <link> tags it was meant to replace. A
 * data-* attribute on a non-script element isn't touched by that
 * stripping and survives the swap.
 *
 * Deliberately does NOT use the usual per-element "already inited" guard
 * from the standard JS module pattern (see AGENTS.md) — the whole point
 * here is to re-run on every single swap, since each page needs a fresh
 * prefetch set, not a one-time init.
 */
(function () {
  var FIRST_WAIT_MS = 4000;
  var _generation = 0;

  function dataSaverActive() {
    // navigator.connection is Chromium-only; browsers without it just skip
    // this check and prefetch normally (same as before this guard existed).
    var conn = navigator.connection;
    if (!conn) return false;
    return (
      conn.saveData === true ||
      conn.effectiveType === 'slow-2g' ||
      conn.effectiveType === '2g'
    );
  }

  function refreshPrefetchHints() {
    document
      .querySelectorAll('link[rel="prefetch"][data-oh-prefetch]')
      .forEach(function (el) {
        el.remove();
      });

    // Respect the user's data saver setting / a slow connection: idle-
    // priority prefetching of sibling tabs a visitor may never open still
    // spends real bytes on a metered mobile connection.
    if (dataSaverActive()) return;

    function urlsOf(selector) {
      var urls = [];
      document.querySelectorAll(selector).forEach(function (el) {
        try {
          JSON.parse(el.dataset.urls || '[]').forEach(function (u) {
            if (urls.indexOf(u) === -1) urls.push(u);
          });
        } catch (e) {
          /* malformed block — skip it rather than break prefetching for the rest */
        }
      });
      return urls;
    }
    function insert(urls) {
      return urls.map(function (u) {
        var link = document.createElement('link');
        link.rel = 'prefetch';
        link.href = u;
        link.dataset.ohPrefetch = '1';
        var done = new Promise(function (resolve) {
          link.addEventListener('load', resolve);
          link.addEventListener('error', resolve);
        });
        document.head.appendChild(link);
        return done;
      });
    }

    /* The likeliest next taps (the bottom bar's pages, marked
     * data-oh-prefetch-first in base.html) go first; the rest only once
     * those are in (or after FIRST_WAIT_MS), so they don't compete. */
    var first = urlsOf('.oh-prefetch-urls[data-oh-prefetch-first]');
    var rest = urlsOf('.oh-prefetch-urls:not([data-oh-prefetch-first])').filter(function (u) {
      return first.indexOf(u) === -1;
    });
    var generation = ++_generation;
    var waited = new Promise(function (resolve) { setTimeout(resolve, FIRST_WAIT_MS); });
    Promise.race([Promise.all(insert(first)), waited]).then(function () {
      /* A newer page has started its own prefetching meanwhile. */
      if (generation === _generation) insert(rest);
    });
  }

  document.addEventListener('DOMContentLoaded', refreshPrefetchHints);
  document.addEventListener('htmx:afterSettle', refreshPrefetchHints);
})();
