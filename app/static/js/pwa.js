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
        _queueBadge.textContent = (t.queuedN || '%(n)s queued').replace('%(n)s', count);
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
      var entry = { queued_at: Date.now(), fields: {} };
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
      if (!rows.length) return;
      rows.forEach(function (row) { _syncEntry(row); });
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
      return fetch('/flights/new', { method: 'POST', body: fd });
    }).then(function (resp) {
      if (resp.ok || resp.redirected || resp.status === 302) {
        _removeEntry(row.id).then(function () {
          _updateQueueBadge();
          _showBanner(t.flightSynced || 'Offline flight synced.');
        });
      }
    }).catch(function () {});
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
