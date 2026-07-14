/* OpenHangar offline-changes page — the single sync-status surface: lists
 * every pending aircraft-logbook edit (outbox) and legacy queued new-flight
 * entry (queue), entirely from IndexedDB, with per-field conflict
 * resolution. Works offline like the workbench. */
(function () {
  'use strict';

  function el(tag, opts) {
    var e = document.createElement(tag);
    opts = opts || {};
    if (opts.className) e.className = opts.className;
    if (opts.text !== undefined) e.textContent = opts.text;
    return e;
  }

  function init() {
    var root = document.getElementById('oh-changes-root');
    if (!root || root.dataset.ohInited) return;
    root.dataset.ohInited = '1';

    var i18nEl = document.getElementById('oh-ch-i18n');
    var i18n = i18nEl ? JSON.parse(i18nEl.textContent) : {};
    var listEl = document.getElementById('oh-changes-list');
    var authBanner = document.getElementById('oh-ch-auth-banner');
    var progressEl = document.getElementById('oh-ch-progress');
    var summaryEl = document.getElementById('oh-ch-summary');
    var syncNowBtn = document.getElementById('oh-ch-sync-now');

    var snapshotCache = {};

    function getRegistration(aircraftId) {
      if (Object.prototype.hasOwnProperty.call(snapshotCache, aircraftId)) {
        return Promise.resolve(snapshotCache[aircraftId]);
      }
      return window.OhOffline.getSnapshot(aircraftId).then(function (snap) {
        var reg = snap && snap.aircraft ? snap.aircraft.registration : null;
        snapshotCache[aircraftId] = reg;
        return reg;
      });
    }

    function statusBadgeClass(status) {
      switch (status) {
        case 'conflict':
        case 'duplicate':
        case 'error':
          return 'bg-danger';
        default:
          return 'bg-warning text-dark';
      }
    }

    function statusLabel(status) {
      var labels = {
        pending: i18n.statusPending,
        conflict: i18n.statusConflict,
        duplicate: i18n.statusDuplicate,
        error: i18n.statusError
      };
      return labels[status] || labels.pending;
    }

    function buildDiffTable(record) {
      var table = el('table', { className: 'table table-sm oh-fs-082 mb-0' });
      var tbody = el('tbody');
      var changedKeys = Object.keys(record.fields).filter(function (k) {
        return record.fields[k] !== record.base[k];
      });
      if (!changedKeys.length) {
        var emptyRow = el('tr');
        var emptyCell = el('td', { text: i18n.noChanges || '' });
        emptyRow.appendChild(emptyCell);
        tbody.appendChild(emptyRow);
      }
      changedKeys.forEach(function (k) {
        var row = el('tr');
        row.appendChild(el('td', { className: 'text-muted', text: k }));
        row.appendChild(el('td', { className: 'text-muted', text: record.base[k] || '—' }));
        row.appendChild(el('td', { className: 'text-muted', text: '→' }));
        row.appendChild(el('td', { className: 'fw-semibold', text: record.fields[k] || '—' }));
        var revertCell = el('td');
        if (record.status !== 'conflict') {
          var revertBtn = el('button', { className: 'btn btn-link btn-sm p-0 oh-fs-08', text: i18n.revert || '' });
          revertBtn.type = 'button';
          revertBtn.addEventListener('click', function () {
            record.fields[k] = record.base[k];
            window.OhOffline.updateOutboxRecord(record).then(render);
          });
          revertCell.appendChild(revertBtn);
        }
        row.appendChild(revertCell);
        tbody.appendChild(row);
      });
      table.appendChild(tbody);
      return table;
    }

    function buildConflictArea(record) {
      var wrap = el('div');
      wrap.appendChild(el('p', { className: 'oh-fs-082 text-muted', text: i18n.conflictIntro || '' }));

      var choices = {};
      (record.conflicts || []).forEach(function (c) {
        choices[c.field] = 'local';
        var row = el('div', { className: 'row g-2 align-items-center mb-2 pb-2 border-bottom' });
        row.appendChild(el('div', { className: 'col-md-3 fw-semibold oh-fs-082', text: c.field }));

        var baseCol = el('div', { className: 'col-md-2 text-muted oh-fs-08' });
        baseCol.appendChild(el('div', { text: c.base || '—' }));
        row.appendChild(baseCol);

        var localCol = el('div', { className: 'col-md-3' });
        var localLabel = el('label', { className: 'd-flex align-items-center gap-1 oh-fs-082' });
        var localRadio = el('input');
        localRadio.type = 'radio';
        localRadio.name = 'oh-ch-conflict-' + record.id + '-' + c.field;
        localRadio.checked = true;
        localRadio.addEventListener('change', function () { choices[c.field] = 'local'; });
        localLabel.appendChild(localRadio);
        localLabel.appendChild(el('span', { text: (i18n.myOfflineValue || '') + ': ' + (c.local || '—') }));
        localCol.appendChild(localLabel);
        row.appendChild(localCol);

        var serverCol = el('div', { className: 'col-md-3' });
        var serverLabel = el('label', { className: 'd-flex align-items-center gap-1 oh-fs-082' });
        var serverRadio = el('input');
        serverRadio.type = 'radio';
        serverRadio.name = 'oh-ch-conflict-' + record.id + '-' + c.field;
        serverRadio.addEventListener('change', function () { choices[c.field] = 'server'; });
        serverLabel.appendChild(serverRadio);
        serverLabel.appendChild(el('span', { text: (i18n.currentOnlineValue || '') + ': ' + (c.server || '—') }));
        serverCol.appendChild(serverLabel);
        row.appendChild(serverCol);

        wrap.appendChild(row);
      });

      var applyBtn = el('button', { className: 'btn btn-ac-primary btn-sm mt-1', text: i18n.applyResolution || '' });
      applyBtn.type = 'button';
      applyBtn.addEventListener('click', function () {
        var newFields = {};
        for (var k in record.entry) { newFields[k] = record.entry[k]; }
        (record.conflicts || []).forEach(function (c) {
          if (choices[c.field] === 'local') newFields[c.field] = c.local;
        });
        record.base = record.entry;
        record.fields = newFields;
        delete record.status;
        delete record.conflicts;
        delete record.entry;
        window.OhOffline.updateOutboxRecord(record).then(function () {
          return window.OhOffline.flush();
        }).then(render);
      });
      wrap.appendChild(applyBtn);
      return wrap;
    }

    function buildDuplicateArea(record) {
      var wrap = el('div');
      wrap.appendChild(el('p', { className: 'text-danger oh-fs-082', text: i18n.duplicateMsg || '' }));
      var saveBtn = el('button', { className: 'btn btn-ac-danger btn-sm', text: i18n.saveAnyway || '' });
      saveBtn.type = 'button';
      saveBtn.addEventListener('click', function () {
        record.force_duplicate = true;
        record.status = 'pending';
        window.OhOffline.updateOutboxRecord(record).then(function () {
          return window.OhOffline.flush();
        }).then(render);
      });
      wrap.appendChild(saveBtn);
      return wrap;
    }

    function renderOutboxCard(record, registration) {
      var card = el('div', { className: 'ac-form-card p-3 mb-3' });

      var header = el('div', { className: 'd-flex justify-content-between align-items-start mb-2' });
      var titleWrap = el('div');
      var title = (registration || ((i18n.aircraftFallback || '') + ' #' + record.aircraft_id)) +
        ' — ' + (record.fields.date || '') + ' ' + (record.fields.departure_icao || '') +
        '→' + (record.fields.arrival_icao || '');
      titleWrap.appendChild(el('strong', { text: title }));
      titleWrap.appendChild(el('span', {
        className: 'badge ms-2 ' + statusBadgeClass(record.status || 'pending'),
        text: statusLabel(record.status || 'pending')
      }));
      header.appendChild(titleWrap);

      var discardBtn = el('button', { className: 'btn btn-ac-ghost btn-xs', text: i18n.discard || '' });
      discardBtn.type = 'button';
      discardBtn.addEventListener('click', function () {
        window.OhOffline.deleteOutbox(record.id).then(render);
      });
      header.appendChild(discardBtn);
      card.appendChild(header);

      if (record.status === 'conflict') {
        card.appendChild(buildConflictArea(record));
      } else if (record.status === 'duplicate') {
        card.appendChild(buildDuplicateArea(record));
      } else {
        if (record.status === 'error' && record.errors && record.errors.length) {
          card.appendChild(el('div', { className: 'text-danger oh-fs-08 mb-2', text: record.errors.join(' ') }));
        }
        card.appendChild(buildDiffTable(record));
      }

      return card;
    }

    function renderQueueCard(row) {
      var card = el('div', { className: 'ac-form-card p-3 mb-3' });
      var header = el('div', { className: 'd-flex justify-content-between align-items-start' });
      var fields = row.fields || {};
      var title = (i18n.newFlightEntry || '') + ' — ' + (fields.date || '') + ' ' +
        (fields.departure_icao || '') + '→' + (fields.arrival_icao || '');
      var titleWrap = el('div');
      titleWrap.appendChild(el('strong', { text: title }));
      if (row.status === 'error') {
        titleWrap.appendChild(el('span', { className: 'badge bg-danger ms-2', text: i18n.statusError || '' }));
      }
      header.appendChild(titleWrap);

      var discardBtn = el('button', { className: 'btn btn-ac-ghost btn-xs', text: i18n.discard || '' });
      discardBtn.type = 'button';
      discardBtn.addEventListener('click', function () {
        window.OhOffline.deleteQueueEntry(row.id).then(render);
      });
      header.appendChild(discardBtn);
      card.appendChild(header);

      if (row.status === 'error') {
        var msg = (i18n.queueEntryFailed || '').replace('{status}', row.httpStatus || '?');
        card.appendChild(el('div', { className: 'text-danger oh-fs-08 mt-1', text: msg }));
      }
      return card;
    }

    function render() {
      return Promise.all([
        window.OhOffline.getOutbox(),
        window.OhOffline.getQueue()
      ]).then(function (results) {
        var outbox = results[0];
        var queue = results[1];
        listEl.innerHTML = '';
        if (!outbox.length && !queue.length) {
          listEl.appendChild(el('p', { className: 'text-muted text-center py-4', text: i18n.noChangesPending || '' }));
          return;
        }
        return Promise.all(outbox.map(function (r) { return getRegistration(r.aircraft_id); }))
          .then(function (registrations) {
            outbox.forEach(function (r, i) {
              listEl.appendChild(renderOutboxCard(r, registrations[i]));
            });
            queue.forEach(function (row) {
              listEl.appendChild(renderQueueCard(row));
            });
          });
      });
    }

    document.addEventListener('oh-offline-sync-progress', function (e) {
      if (!progressEl) return;
      progressEl.classList.remove('d-none');
      progressEl.textContent = (i18n.syncingProgress || '')
        .replace('{current}', e.detail.current)
        .replace('{total}', e.detail.total);
    });

    document.addEventListener('oh-offline-sync', function (e) {
      if (progressEl) progressEl.classList.add('d-none');
      var detail = e.detail || {};
      if (detail.authRequired) {
        if (authBanner) authBanner.classList.remove('d-none');
        return;
      }
      if (authBanner) authBanner.classList.add('d-none');
      if (summaryEl && (detail.synced || detail.conflicts || detail.errors)) {
        summaryEl.classList.remove('d-none');
        summaryEl.textContent = (i18n.syncSummary || '')
          .replace('{synced}', detail.synced || 0)
          .replace('{conflicts}', detail.conflicts || 0)
          .replace('{errors}', detail.errors || 0);
      }
      render();
    });

    if (syncNowBtn) {
      syncNowBtn.addEventListener('click', function () {
        window.OhOffline.flush().then(render);
      });
    }

    render();
  }

  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
