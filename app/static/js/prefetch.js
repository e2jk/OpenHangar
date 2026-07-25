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
  function refreshPrefetchHints() {
    document
      .querySelectorAll('link[rel="prefetch"][data-oh-prefetch]')
      .forEach(function (el) {
        el.remove();
      });

    var urls = [];
    document.querySelectorAll('.oh-prefetch-urls').forEach(function (el) {
      try {
        JSON.parse(el.dataset.urls || '[]').forEach(function (u) {
          urls.push(u);
        });
      } catch (e) {
        /* malformed block — skip it rather than break prefetching for the rest */
      }
    });

    urls.forEach(function (u) {
      var link = document.createElement('link');
      link.rel = 'prefetch';
      link.href = u;
      link.dataset.ohPrefetch = '1';
      document.head.appendChild(link);
    });
  }

  document.addEventListener('DOMContentLoaded', refreshPrefetchHints);
  document.addEventListener('htmx:afterSettle', refreshPrefetchHints);
})();
