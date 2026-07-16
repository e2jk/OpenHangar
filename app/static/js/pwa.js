/* OpenHangar PWA — service worker registration, install prompt, offline queue */
(function () {
  'use strict';

  /* ── Translated strings injected by base.html ── */
  var t = window._pwa_i18n || {};

  /* ── Service worker registration (skipped in debug/dev mode) ── */
  var _debug = window._oh_config && window._oh_config.debug;
  if ('serviceWorker' in navigator && !_debug) {
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .catch(function (err) {
        console.warn('[OH-PWA] SW registration failed:', err);
      });

    /* Listen for sync requests forwarded by the SW */
    navigator.serviceWorker.addEventListener('message', function (e) {
      if (e.data && e.data.type === 'OH_SYNC_REQUESTED') {
        _syncQueue();
      }
    });
  }

  /* ── Offline / online indicator ── */
  var _offlineBadge = document.getElementById('oh-pwa-offline-badge');
  var _queueBadge = document.getElementById('oh-pwa-queue-badge');

  function _setOfflineClass(offline) {
    document.body.classList.toggle('oh-offline', offline);
    /* _offlineBadge visibility is handled by the body.oh-offline CSS rule */
  }

  _setOfflineClass(!navigator.onLine);

  window.addEventListener('online', function () {
    _setOfflineClass(false);
    _syncQueue();
  });
  window.addEventListener('offline', function () {
    _setOfflineClass(true);
  });

  /* The 'online' event is not reliable in every browser (Firefox's devtools
   * network throttling in particular can leave navigator.onLine/online-event
   * out of sync with real connectivity), so the badge can get stuck showing
   * "Offline" forever with no second chance to clear it — pwa.js only runs
   * once, since hx-boost swaps just the body and never re-executes scripts.
   * A successful htmx swap is independent proof the network is up: use it
   * as a fallback signal regardless of what navigator.onLine last reported. */
  document.addEventListener('htmx:afterSettle', function () {
    _setOfflineClass(false);
  });

  /* ── IndexedDB helpers ── */
  /* Raw store access lives in offline_db.js (window.OhOffline), loaded
   * before this file, so there is a single owner of the shared
   * openhangar-offline database and its version/upgrade path. */
  function _getAll() { return window.OhOffline.getQueue(); }
  function _addEntry(entry) { return window.OhOffline.addQueueEntry(entry); }
  function _removeEntry(id) { return window.OhOffline.deleteQueueEntry(id); }

  /* ── Queue badge update — combines the legacy queue with the offline
   * logbook outbox (Phase 38) so the navbar shows one total. ── */
  function _updateQueueBadge() {
    if (!_queueBadge) return;
    Promise.all([
      window.OhOffline.getQueue(),
      window.OhOffline.outboxCount()
    ]).then(function (results) {
      var count = results[0].length + results[1];
      document.body.classList.toggle('oh-queue-active', count > 0);
      if (count === 0) return;
      if (count === 1) {
        _queueBadge.textContent = t.queued1 || '1 queued';
      } else {
        _queueBadge.textContent = (t.queuedN || '{n} queued').replace('{n}', count);
      }
    }).catch(function () {});
  }

  /* Refresh badge on load */
  _updateQueueBadge();

  /* ── Flight form offline interception ── */
  var _flightForm = document.getElementById('flight-form');
  if (_flightForm) {
    _flightForm.addEventListener('submit', function (e) {
      if (navigator.onLine) return; /* let normal submission proceed */
      e.preventDefault();

      var fd = new FormData(_flightForm);
      /* _flightForm has no action= attribute, so this resolves to the
       * current page URL — /flights/new or /flights/<id>/edit depending on
       * where the form was rendered. Stored so replay hits the right route
       * instead of always /flights/new (which would resurface an offline
       * *edit* as a duplicate new flight). */
      var entry = { queued_at: Date.now(), fields: {}, action: _flightForm.action };
      fd.forEach(function (value, key) {
        /* Store both text fields and File Blobs */
        if (value instanceof File && value.size > 0) {
          entry.fields[key] = { _file: true, name: value.name, type: value.type, data: value };
        } else if (!(value instanceof File)) {
          entry.fields[key] = value;
        }
      });

      _addEntry(entry).then(function () {
        _updateQueueBadge();
        /* Register background sync for browsers that support it */
        if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
          navigator.serviceWorker.ready.then(function (reg) {
            if ('sync' in reg) { reg.sync.register('oh-flight-sync').catch(function () {}); }
          });
        }
        _showBanner(t.flightQueued || 'Flight saved offline — will sync when online.');
      }).catch(function (err) {
        console.error('[OH-PWA] Failed to queue flight:', err);
      });
    });
  }

  /* ── Sync queued flights ── */
  function _syncQueue() {
    if (!navigator.onLine) return;
    _getAll().then(function (rows) {
      /* Permanently-failed entries (38f) are not retried automatically —
       * they need the user to fix the underlying issue (via the workbench
       * or a fresh submission) and discard the stale entry on
       * /offline/changes. */
      var retryable = rows.filter(function (row) { return row.status !== 'error'; });
      retryable.forEach(function (row) { _syncEntry(row); });
    }).catch(function () {});
  }

  function _syncEntry(row) {
    var fields = row.fields || {};
    var date = fields['date'] || '';
    var aircraftId = fields['aircraft_id'] || '';
    var dep = fields['departure_icao'] || '';
    var arr = fields['arrival_icao'] || '';

    /* Conflict check before submitting */
    var checkUrl = '/api/check-flight-duplicate?date=' + encodeURIComponent(date) +
      '&aircraft_id=' + encodeURIComponent(aircraftId) +
      '&departure_icao=' + encodeURIComponent(dep) +
      '&arrival_icao=' + encodeURIComponent(arr);

    /* An edit replays against /flights/<id>/edit (see the submit handler
     * above, which stores the form's own action) — exclude that flight from
     * its own duplicate check, or every offline edit of an unchanged
     * date/aircraft/route (e.g. just adding a comment) gets misflagged as a
     * duplicate of itself and dead-ends in the conflict dialog instead of
     * being applied. */
    var editMatch = /\/flights\/(\d+)\/edit(?:[/?]|$)/.exec(row.action || '');
    if (editMatch) checkUrl += '&exclude_flight_id=' + editMatch[1];

    fetch(checkUrl)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.duplicate) {
          _showConflict(row, data);
        } else {
          _submitEntry(row);
        }
      })
      .catch(function () {
        /* If check fails (e.g. offline again), leave in queue */
      });
  }

  function _submitEntry(row) {
    var fd = new FormData();
    var fields = row.fields || {};
    var pending = [];

    Object.keys(fields).forEach(function (key) {
      var val = fields[key];
      if (val && val._file) {
        /* Reconstruct File from stored Blob */
        pending.push(
          Promise.resolve(new File([val.data], val.name, { type: val.type }))
            .then(function (f) { fd.append(key, f); })
        );
      } else {
        fd.append(key, val);
      }
    });

    Promise.all(pending).then(function () {
      /* Fetch a fresh CSRF token before replaying — the one captured at
       * queue time is in the form data too, but after a long offline
       * period it has outlived the 1 h token lifetime and would 400
       * forever, silently. */
      return fetch('/api/offline/csrf');
    }).then(function (r) {
      if (!r.ok) throw new Error('csrf-fetch-failed');
      return r.json();
    }).then(function (data) {
      fd.set('csrf_token', data.csrf_token);
      var targetUrl = row.action || '/flights/new';
      return fetch(targetUrl, { method: 'POST', body: fd });
    }).then(function (resp) {
      if (resp.ok || resp.redirected || resp.status === 302) {
        _removeEntry(row.id).then(function () {
          _updateQueueBadge();
          _showBanner(t.flightSynced || 'Offline flight synced.');
        });
      } else if (resp.status < 500) {
        /* Permanent failure (e.g. validation error) — 5xx is left silently
         * queued for the next automatic retry, but this won't self-resolve;
         * surface it on /offline/changes instead of failing forever with no
         * feedback. */
        row.status = 'error';
        row.httpStatus = resp.status;
        window.OhOffline.updateQueueEntry(row).then(_updateQueueBadge);
      }
    }).catch(function () {
      /* offline again, or the CSRF fetch itself failed — leave queued */
    });
  }

  /* ── Conflict dialog ── */
  function _showConflict(row, data) {
    var msg = (t.conflictMsg || 'A duplicate flight was found. Discard the offline copy?');
    var discardLabel = t.conflictDiscard || 'Discard offline copy';
    var keepLabel = t.conflictKeep || 'Keep for later';

    /* Build a simple Bootstrap modal */
    var modalId = 'oh-pwa-conflict-modal';
    var existing = document.getElementById(modalId);
    if (existing) existing.remove();

    var modal = document.createElement('div');
    modal.id = modalId;
    modal.className = 'modal fade';
    modal.tabIndex = -1;
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('role', 'dialog');
    modal.innerHTML =
      '<div class="modal-dialog modal-dialog-centered">' +
        '<div class="modal-content">' +
          '<div class="modal-header">' +
            '<h5 class="modal-title"><i class="bi bi-exclamation-triangle-fill text-warning me-2"></i>' +
            (t.conflictTitle || 'Duplicate flight') + '</h5>' +
          '</div>' +
          '<div class="modal-body"><p class="mb-0">' + msg + '</p></div>' +
          '<div class="modal-footer">' +
            '<button type="button" class="btn btn-outline-secondary btn-sm" id="oh-conflict-keep">' + keepLabel + '</button>' +
            '<button type="button" class="btn btn-danger btn-sm" id="oh-conflict-discard">' + discardLabel + '</button>' +
          '</div>' +
        '</div>' +
      '</div>';

    document.body.appendChild(modal);

    var bsModal = new bootstrap.Modal(modal);
    bsModal.show();

    document.getElementById('oh-conflict-discard').addEventListener('click', function () {
      bsModal.hide();
      _removeEntry(row.id).then(function () {
        _updateQueueBadge();
      });
    });

    document.getElementById('oh-conflict-keep').addEventListener('click', function () {
      bsModal.hide();
    });

    modal.addEventListener('hidden.bs.modal', function () { modal.remove(); });
  }

  /* ── Install prompt ── */
  var _deferredPrompt = null;
  var _installBar = document.getElementById('oh-pwa-install-bar');
  var _installBtn = document.getElementById('oh-pwa-install-btn');
  var _installDismiss = document.getElementById('oh-pwa-install-dismiss');
  var _INSTALL_DISMISSED_KEY = 'oh-pwa-install-dismissed';

  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    if (localStorage.getItem(_INSTALL_DISMISSED_KEY)) return;
    _deferredPrompt = e;
    if (_installBar) _installBar.classList.add('visible');
  });

  if (_installBtn) {
    _installBtn.addEventListener('click', function () {
      if (!_deferredPrompt) return;
      _deferredPrompt.prompt();
      _deferredPrompt.userChoice.then(function () {
        _deferredPrompt = null;
        if (_installBar) _installBar.classList.remove('visible');
      });
    });
  }

  if (_installDismiss) {
    _installDismiss.addEventListener('click', function () {
      localStorage.setItem(_INSTALL_DISMISSED_KEY, '1');
      if (_installBar) _installBar.classList.remove('visible');
    });
  }

  /* ── Inline flash banner ── */
  function _showBanner(msg) {
    var container = document.querySelector('.page-content .container.mt-3') ||
                    (function () {
                      var c = document.createElement('div');
                      c.className = 'container mt-3';
                      var main = document.querySelector('.page-content');
                      if (main) main.prepend(c);
                      return c;
                    })();
    var div = document.createElement('div');
    div.className = 'alert alert-info alert-dismissible fade show';
    div.setAttribute('role', 'alert');
    div.innerHTML = '<i class="bi bi-cloud-check me-1"></i>' + msg +
      '<button type="button" class="btn-close" data-bs-dismiss="alert"></button>';
    container.prepend(div);
  }

  /* Run sync on page load if online and there are queued entries */
  if (navigator.onLine) { _syncQueue(); }

})();
