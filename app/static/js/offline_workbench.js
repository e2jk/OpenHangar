/* OpenHangar offline logbook workbench — renders an aircraft's cached
 * snapshot + pending outbox entirely from IndexedDB, so the page works
 * identically online (edits save immediately) and offline (edits queue). */
(function () {
  'use strict';

  var FIELDS = [
    'date', 'departure_icao', 'arrival_icao', 'departure_time', 'arrival_time',
    'flight_time', 'flight_time_counter_start', 'flight_time_counter_end',
    'engine_time_counter_start', 'engine_time_counter_end',
    'fuel_added_qty', 'fuel_remaining_qty', 'oil_added_l',
    'passenger_count', 'landing_count', 'nature_of_flight', 'notes',
    'fuel_added_unit', 'fuel_event', 'crew_name_0', 'crew_role_0',
    'crew_name_1', 'crew_role_1'
  ];

  var CONTINUITY_PAIRS = ['flight_time_counter', 'engine_time_counter'];

  var PILOT_FIELDS = [
    'night_time', 'instrument_time', 'landings_day', 'landings_night',
    'multi_pilot', 'pic_name', 'departure_time', 'arrival_time'
  ];

  function shallowCopy(obj) {
    var out = {};
    for (var k in obj) {
      if (Object.prototype.hasOwnProperty.call(obj, k)) out[k] = obj[k];
    }
    return out;
  }

  function init() {
    var root = document.getElementById('oh-workbench-root');
    if (!root || root.dataset.ohInited) return;
    root.dataset.ohInited = '1';

    var aircraftId = parseInt(root.getAttribute('data-oh-aircraft-id'), 10);
    var tbody = document.getElementById('oh-wb-tbody');
    var rowTpl = document.getElementById('oh-wb-row');
    var i18nEl = document.getElementById('oh-wb-i18n');
    var i18n = i18nEl ? JSON.parse(i18nEl.textContent) : {};
    var banner = document.getElementById('oh-wb-offline-banner');

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
        window.OhOffline.getSnapshot(aircraftId),
        window.OhOffline.getOutbox()
      ]).then(function (results) {
        var snapshot = results[0];
        var outboxByFlight = {};
        results[1].forEach(function (r) {
          if (r.aircraft_id === aircraftId) outboxByFlight[r.flight_id] = r;
        });

        state.entries = ((snapshot && snapshot.entries) || []).map(function (e) {
          var baseFields = {};
          FIELDS.forEach(function (f) { baseFields[f] = e.fields[f] || ''; });
          var mergedFields = shallowCopy(baseFields);
          var ob = outboxByFlight[e.id];
          var effectiveBase = baseFields;
          if (ob) {
            for (var k in ob.fields) { mergedFields[k] = ob.fields[k]; }
            effectiveBase = ob.base;
          }

          var pilot = null;
          if (e.pilot) {
            var pilotBase = {};
            PILOT_FIELDS.forEach(function (f) { pilotBase[f] = e.pilot.fields[f] || ''; });
            var mergedPilot = shallowCopy(pilotBase);
            var pilotEffectiveBase = pilotBase;
            if (ob && ob.pilot) {
              for (var pk in ob.pilot.fields) { mergedPilot[pk] = ob.pilot.fields[pk]; }
              pilotEffectiveBase = ob.pilot.base;
            }
            pilot = {
              entryId: e.pilot.entry_id,
              fields: mergedPilot,
              baseFields: pilotEffectiveBase,
              derived: e.pilot.derived || {}
            };
          }

          return {
            id: e.id,
            fields: mergedFields,
            baseFields: effectiveBase,
            meta: e.meta,
            pilot: pilot,
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
        case 'duplicate':
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
        duplicate: i18n.statusDuplicate,
        error: i18n.statusError
      };
      return labels[status] || labels.pending;
    }

    function render() {
      tbody.innerHTML = '';
      if (!state.entries.length) {
        var emptyRow = document.createElement('tr');
        var emptyCell = document.createElement('td');
        emptyCell.colSpan = 9;
        emptyCell.className = 'text-center text-muted py-4';
        emptyCell.textContent = i18n.noEntries || '';
        emptyRow.appendChild(emptyCell);
        tbody.appendChild(emptyRow);
        return;
      }

      state.entries.forEach(function (entry, idx) {
        var prev = idx > 0 ? state.entries[idx - 1] : null;
        var frag = rowTpl.content.cloneNode(true);
        var mainRow = frag.querySelector('[data-row]');
        var detailRow = frag.querySelector('[data-detail-row]');
        mainRow.dataset.flightId = String(entry.id);
        detailRow.dataset.flightId = String(entry.id);

        FIELDS.forEach(function (field) {
          var input = frag.querySelector('[data-field="' + field + '"]');
          if (!input) return;
          input.value = entry.fields[field] || '';
          input.addEventListener('change', function () {
            onFieldChange(entry, field, input, mainRow, detailRow);
          });
        });

        CONTINUITY_PAIRS.forEach(function (prefix) {
          var startInput = mainRow.querySelector('[data-field="' + prefix + '_start"]');
          if (!startInput || !prev) return;
          var prevEnd = prev.fields[prefix + '_end'];
          var curStart = entry.fields[prefix + '_start'];
          if (prevEnd && curStart && prevEnd !== curStart) {
            startInput.classList.add('border-warning');
            startInput.title = (i18n.continuityWarning || '') + ' (' + prevEnd + ')';
          }
        });

        renderPilotSection(entry, detailRow);

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

    function fieldValue(row, field) {
      var el = row.querySelector('[data-field="' + field + '"]');
      return el ? window.OhOffline.ohCanon(field, el.value) : '';
    }

    function validate(entry, field, canonValue, mainRow) {
      var errors = [];
      if ((field === 'date' || field === 'departure_icao' || field === 'arrival_icao') && !canonValue) {
        errors.push(i18n.requiredField || '');
      }
      if (field === 'crew_name_0' && !canonValue) {
        errors.push(i18n.requiredField || '');
      }

      var fs = fieldValue(mainRow, 'flight_time_counter_start');
      var fe = fieldValue(mainRow, 'flight_time_counter_end');
      if (fs && fe && parseFloat(fe) < parseFloat(fs)) {
        errors.push(i18n.counterOrderError || '');
      }
      var es = fieldValue(mainRow, 'engine_time_counter_start');
      var ee = fieldValue(mainRow, 'engine_time_counter_end');
      if (es && ee && parseFloat(ee) < parseFloat(es)) {
        errors.push(i18n.counterOrderError || '');
      }
      return errors;
    }

    function onFieldChange(entry, field, input, mainRow, detailRow) {
      var canon = window.OhOffline.ohCanon(field, input.value);
      input.value = canon;

      var errors = validate(entry, field, canon, mainRow);
      var errorEl = detailRow.querySelector('[data-errors]');
      if (errors.length) {
        input.classList.add('is-invalid');
        if (errorEl) errorEl.textContent = errors.join(' ');
        return; /* don't queue invalid values */
      }
      input.classList.remove('is-invalid');
      if (errorEl) errorEl.textContent = '';

      entry.fields[field] = canon;

      window.OhOffline.upsertOutboxForFlight(entry.id, aircraftId, {
        fields: shallowCopy(entry.fields),
        base: entry.baseFields
      }).then(function () {
        return window.OhOffline.flush();
      }).then(load);
    }

    /* "My logbook" section — the current user's own PilotLogbookEntry linked
     * to this flight (38h/38i). Only the user-entered subset is editable;
     * everything else is derived from the flight fields above and rendered
     * read-only. Rows with no linked entry show a disabled placeholder. */
    function renderPilotSection(entry, detailRow) {
      var noEntryEl = detailRow.querySelector('[data-pilot-no-entry]');
      var fieldsWrap = detailRow.querySelector('[data-pilot-fields]');
      if (!noEntryEl || !fieldsWrap) return;

      if (!entry.pilot) {
        noEntryEl.classList.remove('d-none');
        fieldsWrap.classList.add('d-none');
        var addLink = noEntryEl.querySelector('[data-pilot-add-link]');
        if (addLink) addLink.href = '/flights/' + entry.id + '/edit';
        return;
      }

      noEntryEl.classList.add('d-none');
      fieldsWrap.classList.remove('d-none');

      PILOT_FIELDS.forEach(function (field) {
        var input = fieldsWrap.querySelector('[data-pilot-field="' + field + '"]');
        if (!input) return;
        input.value = entry.pilot.fields[field] || '';
        if ((field === 'departure_time' || field === 'arrival_time') && !input.value) {
          input.placeholder = entry.fields[field] || '';
        }
        input.addEventListener('change', function () {
          onPilotFieldChange(entry, field, input);
        });
      });

      var derivedEl = fieldsWrap.querySelector('[data-pilot-derived]');
      if (derivedEl) {
        var d = entry.pilot.derived || {};
        var parts = [d.aircraft_type, d.aircraft_registration, d.remarks].filter(Boolean);
        derivedEl.textContent = parts.length ? ((i18n.pilotDerivedHint || '') + ' ' + parts.join(' · ')) : '';
      }
    }

    function onPilotFieldChange(entry, field, input) {
      var canon = window.OhOffline.ohCanonPilot(field, input.value);
      input.value = canon;
      entry.pilot.fields[field] = canon;

      window.OhOffline.upsertOutboxForFlight(entry.id, aircraftId, {
        fields: shallowCopy(entry.fields),
        base: entry.baseFields,
        pilot: {
          fields: shallowCopy(entry.pilot.fields),
          base: entry.pilot.baseFields
        }
      }).then(function () {
        return window.OhOffline.flush();
      }).then(load);
    }

    document.addEventListener('oh-offline-sync', function () { load(); });
    document.addEventListener('oh-snapshot-updated', function (e) {
      if (e.detail && e.detail.aircraftId === aircraftId) load();
    });
    window.addEventListener('online', function () { updateBanner(); load(); });
    window.addEventListener('offline', updateBanner);

    load();
  }

  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
