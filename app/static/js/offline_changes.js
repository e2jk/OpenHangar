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
        case 'pilot_missing':
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
        error: i18n.statusError,
        pilot_missing: i18n.statusPilotMissing
      };
      return labels[status] || labels.pending;
    }

    /* `updateFn` persists the record after a revert — updateOutboxRecord for
     * aircraft-logbook (and its inline pilot sub-diff) rows,
     * updatePilotOutboxRecord for standalone pilot-logbook rows. */
    function buildDiffTable(record, fields, base, updateFn) {
      var table = el('table', { className: 'table table-sm oh-fs-082 mb-0' });
      var tbody = el('tbody');
      var changedKeys = Object.keys(fields).filter(function (k) {
        return fields[k] !== base[k];
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
        row.appendChild(el('td', { className: 'text-muted', text: base[k] || '—' }));
        row.appendChild(el('td', { className: 'text-muted', text: '→' }));
        row.appendChild(el('td', { className: 'fw-semibold', text: fields[k] || '—' }));
        var revertCell = el('td');
        if (record.status !== 'conflict') {
          var revertBtn = el('button', { className: 'btn btn-link btn-sm p-0 oh-fs-08', text: i18n.revert || '' });
          revertBtn.type = 'button';
          revertBtn.addEventListener('click', function () {
            fields[k] = base[k];
            updateFn(record).then(render);
          });
          revertCell.appendChild(revertBtn);
        }
        row.appendChild(revertCell);
        tbody.appendChild(row);
      });
      table.appendChild(tbody);
      return table;
    }

    function buildPilotDiffTable(record) {
      var wrap = el('div', { className: 'mt-2' });
      wrap.appendChild(el('div', { className: 'fw-semibold oh-fs-082 mb-1', text: i18n.myLogbookLabel || '' }));
      wrap.appendChild(buildDiffTable(
        record, record.pilot.fields, record.pilot.base, window.OhOffline.updateOutboxRecord
      ));
      return wrap;
    }

    function appendConflictRow(wrap, groupKey, c, choices) {
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
      localRadio.name = 'oh-ch-conflict-' + groupKey + '-' + c.field;
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
      serverRadio.name = 'oh-ch-conflict-' + groupKey + '-' + c.field;
      serverRadio.addEventListener('change', function () { choices[c.field] = 'server'; });
      serverLabel.appendChild(serverRadio);
      serverLabel.appendChild(el('span', { text: (i18n.currentOnlineValue || '') + ': ' + (c.server || '—') }));
      serverCol.appendChild(serverLabel);
      row.appendChild(serverCol);

      wrap.appendChild(row);
    }

    /* Aircraft-logbook conflict area — may carry conflicts from the flight
     * fields, the pilot fields (38j), or both; each resolved independently
     * before "Apply resolution" resubmits the merged request. */
    function buildConflictArea(record) {
      var wrap = el('div');
      wrap.appendChild(el('p', { className: 'oh-fs-082 text-muted', text: i18n.conflictIntro || '' }));

      var choices = {};
      (record.conflicts || []).forEach(function (c) {
        appendConflictRow(wrap, record.id + '-flight', c, choices);
      });

      var pilotChoices = {};
      if (record.pilot_conflicts && record.pilot_conflicts.length) {
        wrap.appendChild(el('div', { className: 'fw-semibold oh-fs-082 mt-2 mb-1', text: i18n.myLogbookLabel || '' }));
        record.pilot_conflicts.forEach(function (c) {
          appendConflictRow(wrap, record.id + '-pilot', c, pilotChoices);
        });
      }

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

        if (record.pilot_entry) {
          var newPilotFields = {};
          for (var pk in record.pilot_entry.fields) { newPilotFields[pk] = record.pilot_entry.fields[pk]; }
          (record.pilot_conflicts || []).forEach(function (c) {
            if (pilotChoices[c.field] === 'local') newPilotFields[c.field] = c.local;
          });
          record.pilot = { fields: newPilotFields, base: record.pilot_entry.fields };
        }

        delete record.status;
        delete record.conflicts;
        delete record.pilot_conflicts;
        delete record.entry;
        delete record.pilot_entry;
        window.OhOffline.updateOutboxRecord(record).then(function () {
          return window.OhOffline.flush();
        }).then(render);
      });
      wrap.appendChild(applyBtn);
      return wrap;
    }

    function buildPilotOutboxConflictArea(record) {
      var wrap = el('div');
      wrap.appendChild(el('p', { className: 'oh-fs-082 text-muted', text: i18n.conflictIntro || '' }));

      var choices = {};
      (record.conflicts || []).forEach(function (c) {
        appendConflictRow(wrap, 'pe-' + record.id, c, choices);
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
        window.OhOffline.updatePilotOutboxRecord(record).then(function () {
          return window.OhOffline.flush();
        }).then(render);
      });
      wrap.appendChild(applyBtn);
      return wrap;
    }

    /* The linked entry was removed server-side while this device was
     * offline (409 pilot_missing) — the flight-field edit above is still
     * pending; this lets the user drop the stale pilot edit and resubmit
     * flight-only rather than staying stuck. */
    function buildPilotMissingArea(record) {
      var wrap = el('div', { className: 'mb-2' });
      wrap.appendChild(el('p', { className: 'text-warning oh-fs-082 mb-1', text: i18n.pilotMissingMsg || '' }));
      var btn = el('button', { className: 'btn btn-ac-ghost btn-sm', text: i18n.keepFlightChanges || '' });
      btn.type = 'button';
      btn.addEventListener('click', function () {
        delete record.pilot;
        record.status = 'pending';
        window.OhOffline.updateOutboxRecord(record).then(function () {
          return window.OhOffline.flush();
        }).then(render);
      });
      wrap.appendChild(btn);
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
      } else if (record.status === 'pilot_missing') {
        card.appendChild(buildPilotMissingArea(record));
        card.appendChild(buildDiffTable(record, record.fields, record.base, window.OhOffline.updateOutboxRecord));
      } else {
        if (record.status === 'error' && record.errors && record.errors.length) {
          card.appendChild(el('div', { className: 'text-danger oh-fs-08 mb-2', text: record.errors.join(' ') }));
        }
        card.appendChild(buildDiffTable(record, record.fields, record.base, window.OhOffline.updateOutboxRecord));
        if (record.pilot) {
          card.appendChild(buildPilotDiffTable(record));
        }
      }

      return card;
    }

    function renderPilotOutboxCard(record) {
      var card = el('div', { className: 'ac-form-card p-3 mb-3' });

      var header = el('div', { className: 'd-flex justify-content-between align-items-start mb-2' });
      var titleWrap = el('div');
      var isFstd = record.fields.entry_type === 'fstd';
      var subtitle = isFstd
        ? (record.fields.fstd_type || 'FSTD')
        : (record.fields.aircraft_type || record.fields.aircraft_registration || '');
      var title = (record.fields.date || '') + ' — ' + subtitle;
      titleWrap.appendChild(el('strong', { text: title }));
      titleWrap.appendChild(el('span', {
        className: 'badge ms-2 ' + statusBadgeClass(record.status || 'pending'),
        text: statusLabel(record.status || 'pending')
      }));
      header.appendChild(titleWrap);

      var discardBtn = el('button', { className: 'btn btn-ac-ghost btn-xs', text: i18n.discard || '' });
      discardBtn.type = 'button';
      discardBtn.addEventListener('click', function () {
        window.OhOffline.deletePilotOutbox(record.id).then(render);
      });
      header.appendChild(discardBtn);
      card.appendChild(header);

      if (record.status === 'conflict') {
        card.appendChild(buildPilotOutboxConflictArea(record));
      } else {
        if (record.status === 'error' && record.errors && record.errors.length) {
          card.appendChild(el('div', { className: 'text-danger oh-fs-08 mb-2', text: record.errors.join(' ') }));
        }
        card.appendChild(buildDiffTable(record, record.fields, record.base, window.OhOffline.updatePilotOutboxRecord));
      }

      return card;
    }

    function renderQueueCard(row) {
      var card = el('div', { className: 'ac-form-card p-3 mb-3' });
      var header = el('div', { className: 'd-flex justify-content-between align-items-start' });
      var fields = row.fields || {};
      /* An edit replays against /flights/<id>/edit (see pwa.js's flight-form
       * submit handler, which stores the form's own action) — everything
       * else in this legacy queue is a genuinely new flight. */
      var isEdit = /\/flights\/\d+\/edit(?:[/?]|$)/.test(row.action || '');
      var title = (isEdit ? (i18n.editedFlightEntry || '') : (i18n.newFlightEntry || '')) +
        ' — ' + (fields.date || '') + ' ' +
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
        window.OhOffline.getQueue(),
        window.OhOffline.getPilotOutbox()
      ]).then(function (results) {
        var outbox = results[0];
        var queue = results[1];
        var pilotOutbox = results[2];
        listEl.innerHTML = '';
        if (!outbox.length && !queue.length && !pilotOutbox.length) {
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
            pilotOutbox.forEach(function (r) {
              listEl.appendChild(renderPilotOutboxCard(r));
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
