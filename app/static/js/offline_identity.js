/* Private browser state belongs to an account and one authenticated login.
 * These namespaces are not credentials: every replay is checked by the server. */
(function () {
  'use strict';
  var KEY = 'oh-offline-active-identity';
  var _current = null;
  var _locked = false;

  function readStored() {
    try { return JSON.parse(localStorage.getItem(KEY) || 'null'); }
    catch (e) { return null; }
  }
  function same(a, b) {
    return !!a && !!b && a.owner === b.owner && a.session === b.session && a.stamp === b.stamp;
  }
  function fromBody() {
    return { owner: document.body.dataset.ohOfflineOwner || '',
      session: document.body.dataset.ohOfflineSession || '' };
  }
  function notifyWorker(type) {
    if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
      navigator.serviceWorker.controller.postMessage({ type: type,
        owner: _current ? _current.owner : '', session: _current ? _current.session : '' });
    }
  }
  function lock() {
    if (_locked) return;
    _locked = true;
    document.dispatchEvent(new CustomEvent('oh-offline-identity-lost'));
    /* Remove already-rendered private data before an old tab can be interacted
     * with. Keep IndexedDB intact: pending records remain with their owner. */
    var label = document.body.dataset.ohOfflineChanged || '';
    document.body.replaceChildren();
    var message = document.createElement('p');
    message.className = 'container py-4';
    message.textContent = label;
    document.body.appendChild(message);
    var signIn = document.createElement('a');
    signIn.href = '/login';
    signIn.textContent = document.body.dataset.ohOfflineSignIn || '';
    message.appendChild(document.createTextNode(' '));
    message.appendChild(signIn);
  }
  function current() {
    if (_locked || !_current || !_current.owner || !same(_current, readStored())) return null;
    return Object.assign({}, _current);
  }
  function scope() {
    var value = current();
    return value ? value.owner + ':' + value.session + ':' + value.stamp : '';
  }
  function headers() {
    var value = current();
    if (!value) throw new Error('offline-account-changed');
    return { 'X-OH-Offline-Owner': value.owner, 'X-OH-Offline-Session': value.session };
  }
  function assertScope(expected) {
    if (!expected || expected !== scope()) throw new Error('offline-account-changed');
  }
  function quarantine() {
    if (same(_current, readStored())) {
      try { localStorage.removeItem(KEY); } catch (e) { /* fail closed in this tab */ }
    }
    notifyWorker('OH_CLEAR_PRIVATE');
    lock();
  }
  function scopedFetch(input, options) {
    var expected = scope();
    assertScope(expected);
    options = Object.assign({}, options || {});
    var requestHeaders = new Headers(options.headers || {});
    var identityHeaders = headers();
    Object.keys(identityHeaders).forEach(function (key) { requestHeaders.set(key, identityHeaders[key]); });
    options.headers = requestHeaders;
    return window.fetch(input, options).then(function (response) {
      assertScope(expected);
      var value = current();
      var deniedSnapshot = response.status === 404 &&
        /^\/api\/offline\/(aircraft\/[^/]+\/logbook|pilot\/logbook)$/.test(String(input));
      if (response.status === 401 || response.status === 403 || deniedSnapshot ||
          response.headers.get('X-OH-Offline-Owner') !== value.owner ||
          response.headers.get('X-OH-Offline-Session') !== value.session) {
        quarantine();
        throw new Error('offline-account-changed');
      }
      return response;
    });
  }
  function init() {
    var next = fromBody();
    var stored = readStored();
    if (_current && next.owner === _current.owner && next.session === _current.session &&
        !same(_current, stored)) { lock(); return; }
    if (!stored || stored.owner !== next.owner || stored.session !== next.session) {
      next.stamp = Date.now().toString(36) + Math.random().toString(36).slice(2);
      try { localStorage.setItem(KEY, JSON.stringify(next)); }
      catch (e) { _current = null; _locked = true; return; }
    } else { next = stored; }
    _current = next;
    _locked = false;
    notifyWorker('OH_VERIFY_IDENTITY');
  }
  window.addEventListener('storage', function (event) {
    if (event.key === KEY && _current && _current.owner && !same(_current, readStored())) lock();
  });
  window.addEventListener('pageshow', function (event) {
    if (event.persisted && !same(_current, readStored())) lock();
  });
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', function (event) {
      var data = event.data || {};
      if (data.type !== 'OH_IDENTITY_CHANGED' || !_current || !_current.owner ||
          !navigator.serviceWorker.controller) return;
      /* Broadcasts queued for an old document can arrive after a new login.
       * Query the worker's current state instead of trusting that old snapshot. */
      var expected = scope();
      if (!expected) return;
      var channel = new MessageChannel();
      channel.port1.onmessage = function (reply) {
        channel.port1.close();
        if (expected !== scope()) return;
        var latest = reply.data || {};
        if (latest.owner === _current.owner && latest.session === _current.session) return;
        /* Same account, new login (e.g. this browser just changed its own
         * password): nothing of another account is at stake and the old login's
         * private cache is already gone, so load the page afresh under the new
         * login instead of locking it. Another account always locks at once. */
        if (latest.owner && latest.owner === _current.owner) { location.reload(); return; }
        quarantine();
      };
      navigator.serviceWorker.controller.postMessage({ type: 'OH_GET_IDENTITY' }, [channel.port2]);
    });
    navigator.serviceWorker.addEventListener('controllerchange', function () { notifyWorker('OH_VERIFY_IDENTITY'); });
  }
  document.addEventListener('htmx:configRequest', function (event) {
    if (!_current || !_current.owner) return;
    if (!current()) { event.preventDefault(); lock(); return; }
    Object.assign(event.detail.headers, headers());
  });
  document.addEventListener('htmx:afterSettle', init);
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('click', function (event) {
    if (event.target.closest && event.target.closest('#oh-logout-link')) quarantine();
  });
  window.OhOfflineIdentity = { current: current, scope: scope, assertScope: assertScope,
    headers: headers, fetch: scopedFetch, quarantine: quarantine };
  /* Scripts run at the end of body; the offline data layer may start now. */
  init();
})();
