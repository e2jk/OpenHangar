/* OpenHangar read-only offline logbooks — renders the aircraft or pilot
 * logbook snapshot that offline_db.js keeps in IndexedDB (per account), so
 * the whole logbook can be read without a connection. Nothing here edits or
 * queues anything: changes need a connection. */
(function () {
  'use strict';

  function formatSavedAt(timestamp) {
    try {
      return new Date(timestamp).toLocaleString(document.documentElement.lang || undefined);
    } catch (e) {
      return new Date(timestamp).toISOString();
    }
  }

  function init() {
    var root = document.getElementById('oh-offline-logbook');
    if (!root || root.dataset.ohInited) return;
    root.dataset.ohInited = '1';

    var kind = root.dataset.ohOfflineLogbook;
    var aircraftId = parseInt(root.dataset.ohAircraftId, 10);
    var tbody = root.querySelector('[data-oh-offline-rows]');
    var rowTpl = document.getElementById('oh-offline-row');
    var banner = root.querySelector('[data-oh-offline-banner]');
    var savedAt = root.querySelector('[data-oh-offline-saved-at]');
    var i18nEl = document.getElementById('oh-offline-logbook-i18n');
    var i18n = i18nEl ? JSON.parse(i18nEl.textContent) : {};
    var columns = tbody.closest('table').querySelectorAll('thead th').length;

    function updateBanner() {
      if (banner) banner.classList.toggle('d-none', navigator.onLine);
    }

    function message(text) {
      tbody.replaceChildren();
      var row = document.createElement('tr');
      var cell = document.createElement('td');
      cell.colSpan = columns;
      cell.className = 'text-center text-muted py-4';
      cell.textContent = text;
      row.appendChild(cell);
      tbody.appendChild(row);
    }

    function render(snapshot) {
      var entries = ((snapshot && snapshot.entries) || []).slice();
      if (savedAt) {
        savedAt.textContent = snapshot && snapshot.fetched_at
          ? (i18n.savedAt || '').replace('{when}', formatSavedAt(snapshot.fetched_at))
          : '';
      }
      if (!entries.length) {
        message(i18n.noEntries || '');
        return;
      }
      /* Newest first, like the online logbooks' default order. */
      entries.sort(function (a, b) {
        var da = a.fields.date || '';
        var db = b.fields.date || '';
        if (da !== db) return da < db ? 1 : -1;
        return b.id - a.id;
      });
      tbody.replaceChildren();
      entries.forEach(function (entry) {
        var row = rowTpl.content.cloneNode(true);
        row.querySelectorAll('[data-col]').forEach(function (cell) {
          cell.textContent = entry.fields[cell.dataset.col] || '';
        });
        tbody.appendChild(row);
      });
    }

    function load() {
      var read = kind === 'aircraft'
        ? window.OhOffline.getSnapshot(aircraftId)
        : window.OhOffline.getPilotSnapshot();
      return read.then(render).catch(function () { message(i18n.noEntries || ''); });
    }

    document.addEventListener('oh-snapshot-updated', function (e) {
      if (kind === 'aircraft' && e.detail && e.detail.aircraftId === aircraftId) load();
    });
    document.addEventListener('oh-pilot-snapshot-updated', function () {
      if (kind === 'pilot') load();
    });
    window.addEventListener('online', updateBanner);
    window.addEventListener('offline', updateBanner);
    updateBanner();
    load();
  }

  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
