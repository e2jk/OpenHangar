/* OpenHangar offline standalone pilot-logbook workbench — renders the
 * current user's cached pilot_snapshot + pilot_outbox entirely from
 * IndexedDB, mirroring offline_workbench.js's pattern for the aircraft
 * logbook. No continuity checks here — this logbook has no counters. */
(function () {
  'use strict';

  var FIELDS = [
    'date', 'entry_type', 'aircraft_type', 'aircraft_type_icao', 'aircraft_registration',
    'departure_place', 'departure_time', 'arrival_place', 'arrival_time',
    'pic_name', 'night_time', 'instrument_time', 'landings_day', 'landings_night',
    'single_pilot_se', 'single_pilot_me', 'multi_pilot',
    'function_pic', 'function_copilot', 'function_dual', 'function_instructor',
    'remarks', 'fstd_type', 'fstd_duration'
  ];

  function shallowCopy(obj) {
    var out = {};
    for (var k in obj) {
      if (Object.prototype.hasOwnProperty.call(obj, k)) out[k] = obj[k];
    }
    return out;
  }

  function init() {
    var root = document.getElementById('oh-pilot-workbench-root');
    if (!root || root.dataset.ohInited) return;
    root.dataset.ohInited = '1';

    var tbody = document.getElementById('oh-pwb-tbody');
    var rowTpl = document.getElementById('oh-pwb-row');
    var i18nEl = document.getElementById('oh-pwb-i18n');
    var i18n = i18nEl ? JSON.parse(i18nEl.textContent) : {};
    var banner = document.getElementById('oh-pwb-offline-banner');

    var state = { entries: [] };

    function updateBanner() {
      if (!banner) return;
      var count = state.entries.filter(function (e) { return e.pending; }).length;
      if (!navigator.onLine) {
        banner.classList.remove('d-none');
        banner.querySelector('[data-banner-text]').textContent = count > 0
          ? (i18n.workingOfflinePending || '').replace('{n}', count)
          : (i18n.workingOffline || '');
      } else {
        banner.classList.add('d-none');
      }
    }

    function load() {
      return Promise.all([
        window.OhOffline.getPilotSnapshot(),
        window.OhOffline.getPilotOutbox()
      ]).then(function (results) {
        var snapshot = results[0];
        var outboxByEntry = {};
        results[1].forEach(function (r) { outboxByEntry[r.entry_id] = r; });

        state.entries = ((snapshot && snapshot.entries) || []).map(function (e) {
          var baseFields = {};
          FIELDS.forEach(function (f) { baseFields[f] = e.fields[f] || ''; });
          var mergedFields = shallowCopy(baseFields);
          var ob = outboxByEntry[e.id];
          var effectiveBase = baseFields;
          if (ob) {
            for (var k in ob.fields) { mergedFields[k] = ob.fields[k]; }
            effectiveBase = ob.base;
          }
          return {
            id: e.id,
            fields: mergedFields,
            baseFields: effectiveBase,
            pending: !!ob,
            status: ob ? (ob.status || 'pending') : null,
            outboxId: ob ? ob.id : null
          };
        });
        state.entries.sort(function (a, b) {
          if (a.fields.date !== b.fields.date) {
            return a.fields.date < b.fields.date ? -1 : 1;
          }
          return a.id - b.id;
        });
        render();
        updateBanner();
      });
    }

    function statusBadgeClass(status) {
      switch (status) {
        case 'conflict':
        case 'error':
          return 'bg-danger';
        case 'syncing':
          return 'bg-info text-dark';
        default:
          return 'bg-warning text-dark';
      }
    }

    function statusLabel(status) {
      var labels = {
        pending: i18n.statusPending,
        syncing: i18n.statusSyncing,
        conflict: i18n.statusConflict,
        error: i18n.statusError
      };
      return labels[status] || labels.pending;
    }

    function applyEntryTypeVisibility(row, entryType) {
      var isFstd = entryType === 'fstd';
      row.querySelectorAll('.oh-pwb-flight-only').forEach(function (el) {
        el.classList.toggle('d-none', isFstd);
      });
      row.querySelectorAll('.oh-pwb-fstd-only').forEach(function (el) {
        el.classList.toggle('d-none', !isFstd);
      });
    }

    function render() {
      tbody.innerHTML = '';
      if (!state.entries.length) {
        var emptyRow = document.createElement('tr');
        var emptyCell = document.createElement('td');
        emptyCell.colSpan = 8;
        emptyCell.className = 'text-center text-muted py-4';
        emptyCell.textContent = i18n.noEntries || '';
        emptyRow.appendChild(emptyCell);
        tbody.appendChild(emptyRow);
        return;
      }

      state.entries.forEach(function (entry) {
        var frag = rowTpl.content.cloneNode(true);
        var mainRow = frag.querySelector('[data-row]');
        var detailRow = frag.querySelector('[data-detail-row]');
        mainRow.dataset.entryId = String(entry.id);
        detailRow.dataset.entryId = String(entry.id);

        FIELDS.forEach(function (field) {
          var input = frag.querySelector('[data-field="' + field + '"]');
          if (!input) return;
          input.value = entry.fields[field] || (field === 'entry_type' ? 'flight' : '');
          input.addEventListener('change', function () {
            onFieldChange(entry, field, input, mainRow, detailRow);
          });
        });

        applyEntryTypeVisibility(mainRow, entry.fields.entry_type || 'flight');
        applyEntryTypeVisibility(detailRow, entry.fields.entry_type || 'flight');

        var chip = mainRow.querySelector('[data-status-chip]');
        if (entry.pending && chip) {
          chip.classList.remove('d-none');
          chip.className = 'badge ' + statusBadgeClass(entry.status);
          chip.textContent = statusLabel(entry.status);
          if (entry.status !== 'pending' && entry.status !== 'syncing') {
            var link = document.createElement('a');
            link.href = '/offline/changes';
            link.className = 'ms-1 oh-fs-075';
            link.textContent = i18n.reviewLink || '';
            mainRow.querySelector('td:last-child').appendChild(link);
          }
        }

        var toggleBtn = mainRow.querySelector('[data-toggle-detail]');
        if (toggleBtn) {
          toggleBtn.addEventListener('click', function () {
            detailRow.classList.toggle('d-none');
          });
        }

        tbody.appendChild(frag);
      });
    }

    function validate(field, canonValue) {
      var errors = [];
      if (field === 'date' && !canonValue) {
        errors.push(i18n.requiredField || '');
      }
      return errors;
    }

    function onFieldChange(entry, field, input, mainRow, detailRow) {
      var canon = window.OhOffline.ohCanonPilot(field, input.value);
      input.value = canon;

      var errors = validate(field, canon);
      var errorEl = detailRow.querySelector('[data-errors]');
      if (errors.length) {
        input.classList.add('is-invalid');
        if (errorEl) errorEl.textContent = errors.join(' ');
        return; /* don't queue invalid values */
      }
      input.classList.remove('is-invalid');
      if (errorEl) errorEl.textContent = '';

      entry.fields[field] = canon;
      if (field === 'entry_type') {
        applyEntryTypeVisibility(mainRow, canon);
        applyEntryTypeVisibility(detailRow, canon);
      }

      window.OhOffline.upsertPilotOutboxForEntry(entry.id, {
        fields: shallowCopy(entry.fields),
        base: entry.baseFields
      }).then(function () {
        return window.OhOffline.flush();
      }).then(load);
    }

    document.addEventListener('oh-offline-sync', function () { load(); });
    document.addEventListener('oh-pilot-snapshot-updated', function () { load(); });
    window.addEventListener('online', function () { updateBanner(); load(); });
    window.addEventListener('offline', updateBanner);

    load();
  }

  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
