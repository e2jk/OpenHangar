/* OpenHangar offline-submit guard, plus a proactive up-front warning.
 *
 * Nothing can be saved without a connection (there is no offline editing).
 * A user already sitting on a page when connectivity drops can still fill a
 * form in and submit — the POST would then fail with a raw network error
 * instead of a clear message. One capturing `submit` listener at the
 * document level blocks any form submitted while offline and says why.
 * No per-page allow/deny list to maintain: every current and future form
 * gets the same protection automatically.
 *
 * The submit-time block only tells you the dead end once you've already
 * filled the form in. `updateProactiveBanners()` shows the same message up
 * front — on load, and live on the `offline`/`online` events. Fields are
 * never disabled (a guarded form is still worth drafting into, and unlike
 * disabling there's no risk of the UI getting stuck if `online` fails to
 * fire — see pwa.js's offline badge for the same reasoning). The
 * `htmx:afterSettle` listener re-scans after every hx-boost swap (new page,
 * new forms) and doubles as a connectivity-restored fallback: a successful
 * swap is independent proof the network is up. */
(function () {
  'use strict';

  function messageText() {
    return (window._pwa_i18n && window._pwa_i18n.offlineGuardMsg) ||
      "You're offline — this can't be saved right now. Reconnect and try again.";
  }

  function showGuardAlert(form) {
    if (!form || !form.parentNode) return;
    if (form.parentNode.querySelector('[data-oh-guard-alert]')) return;

    var alertEl = document.createElement('div');
    alertEl.className = 'alert alert-warning alert-dismissible fade show';
    alertEl.setAttribute('role', 'alert');
    alertEl.setAttribute('data-oh-guard-alert', '1');

    var text = document.createElement('span');
    text.textContent = messageText();
    alertEl.appendChild(text);

    var closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'btn-close';
    closeBtn.setAttribute('data-bs-dismiss', 'alert');
    closeBtn.setAttribute('aria-label', 'Close');
    alertEl.appendChild(closeBtn);

    form.parentNode.insertBefore(alertEl, form);
  }

  function removeGuardAlert(form) {
    var existing = form && form.parentNode && form.parentNode.querySelector('[data-oh-guard-alert]');
    if (existing) existing.remove();
  }

  /* Skip button-only forms (delete/dismiss/logout-style actions) — there is
   * nothing to warn about "investing time" into before a single click, and
   * a list page can have dozens of these, which would otherwise turn into a
   * wall of identical banners. Derived from the form's own markup, not a
   * maintained list, so it still applies automatically to future forms. */
  function hasFillableField(form) {
    return !!form.querySelector(
      'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="reset"]), ' +
      'textarea, select'
    );
  }

  function updateProactiveBanners() {
    var offline = !navigator.onLine;
    var forms = document.querySelectorAll('form');
    for (var i = 0; i < forms.length; i++) {
      var form = forms[i];
      if (!hasFillableField(form)) continue;
      if (offline) {
        showGuardAlert(form);
      } else {
        removeGuardAlert(form);
      }
    }
  }

  document.addEventListener('DOMContentLoaded', updateProactiveBanners);
  document.addEventListener('htmx:afterSettle', updateProactiveBanners);
  window.addEventListener('offline', updateProactiveBanners);
  window.addEventListener('online', updateProactiveBanners);

  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (navigator.onLine) return;
    e.preventDefault();
    e.stopPropagation();
    showGuardAlert(form);
  }, true);

  /* An already-online submit whose connection drops mid-flight (htmx
   * requests only — native form posts don't emit this) gets the same
   * message instead of htmx's own generic error handling. */
  document.addEventListener('htmx:sendError', function (e) {
    var form = e.target && e.target.closest ? e.target.closest('form') : null;
    if (form) {
      showGuardAlert(form);
      return;
    }

    /* body has hx-boost="true" (base.html), so every un-opted-out link is a
     * boosted GET dispatched at the body, not at the <a> itself — that's why
     * `form` is null here for a failed nav click. htmx has no fallback of
     * its own for a boosted request it couldn't send: the click just does
     * nothing, which is what made offline navigation look completely dead.
     * Re-issue it as a real browser navigation instead: that request has
     * navigate mode, which the service worker's fetch handler (sw.js) does
     * know how to answer from its precache, or with offline.html. */
    var detail = e.detail || {};
    var verb = String((detail.requestConfig && detail.requestConfig.verb) || '').toLowerCase();
    var path = detail.pathInfo && (detail.pathInfo.finalRequestPath || detail.pathInfo.requestPath);
    if (verb === 'get' && path) {
      window.location.href = path;
    }
  });
})();
