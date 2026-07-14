/* OpenHangar offline data layer — shared IndexedDB access for the PWA
 * offline queue (Phase 35, new-flight submissions) and the offline logbook
 * editing workbench (Phase 38, snapshot + outbox). Loaded before pwa.js;
 * pwa.js keeps its own queue business logic but reads/writes the `queue`
 * store through window.OhOffline so there is a single DB-version owner. */
(function () {
  'use strict';

  var _DB_NAME = 'openhangar-offline';
  var _DB_VERSION = 3;

  function _openDb() {
    return new Promise(function (resolve, reject) {
      var req = indexedDB.open(_DB_NAME, _DB_VERSION);
      req.onupgradeneeded = function (e) {
        var db = e.target.result;
        if (!db.objectStoreNames.contains('queue')) {
          db.createObjectStore('queue', { keyPath: 'id', autoIncrement: true });
        }
        if (!db.objectStoreNames.contains('snapshots')) {
          db.createObjectStore('snapshots', { keyPath: 'aircraft_id' });
        }
        if (!db.objectStoreNames.contains('outbox')) {
          db.createObjectStore('outbox', { keyPath: 'id', autoIncrement: true });
        }
        if (!db.objectStoreNames.contains('pilot_snapshot')) {
          db.createObjectStore('pilot_snapshot', { keyPath: 'id' });
        }
        if (!db.objectStoreNames.contains('pilot_outbox')) {
          db.createObjectStore('pilot_outbox', { keyPath: 'id', autoIncrement: true });
        }
      };
      req.onsuccess = function (e) { resolve(e.target.result); };
      req.onerror = function (e) { reject(e.target.error); };
    });
  }

  function _getAll(store) {
    return _openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(store, 'readonly');
        var req = tx.objectStore(store).getAll();
        req.onsuccess = function () { resolve(req.result); };
        req.onerror = function () { reject(req.error); };
      });
    });
  }

  function _get(store, key) {
    return _openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(store, 'readonly');
        var req = tx.objectStore(store).get(key);
        req.onsuccess = function () { resolve(req.result || null); };
        req.onerror = function () { reject(req.error); };
      });
    });
  }

  function _put(store, value) {
    return _openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(store, 'readwrite');
        var req = tx.objectStore(store).put(value);
        req.onsuccess = function () { resolve(req.result); };
        req.onerror = function () { reject(req.error); };
      });
    });
  }

  function _add(store, value) {
    return _openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(store, 'readwrite');
        var req = tx.objectStore(store).add(value);
        req.onsuccess = function () { resolve(req.result); };
        req.onerror = function () { reject(req.error); };
      });
    });
  }

  function _delete(store, key) {
    return _openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(store, 'readwrite');
        var req = tx.objectStore(store).delete(key);
        req.onsuccess = function () { resolve(); };
        req.onerror = function () { reject(req.error); };
      });
    });
  }

  /* ── Queue store (Phase 35 new-flight offline queue) ──
   * Raw CRUD only — the "when to queue / how to replay" business logic
   * stays in pwa.js. */
  function getQueue() { return _getAll('queue'); }
  function addQueueEntry(entry) { return _add('queue', entry); }
  function updateQueueEntry(entry) { return _put('queue', entry); }
  function deleteQueueEntry(id) { return _delete('queue', id); }

  /* ── Snapshots store — one record per aircraft, from the 38a snapshot API ── */
  function getSnapshot(aircraftId) { return _get('snapshots', aircraftId); }
  function putSnapshot(aircraftId, snapshot) {
    var record = { fetched_at: Date.now() };
    for (var k in snapshot) {
      if (Object.prototype.hasOwnProperty.call(snapshot, k)) record[k] = snapshot[k];
    }
    record.aircraft_id = aircraftId;
    return _put('snapshots', record);
  }

  /* ── Outbox store — one record per flight; base values preserved across edits ──
   * `delta.pilot` (optional) is a linked PilotLogbookEntry sub-edit:
   * {fields, base} over PILOT_LINKED_EDITABLE_FIELDS only. `delta.fields`/
   * `delta.base` must always carry the *full* flight field set (even when
   * this particular edit only touches the pilot sub-object) — the sync API
   * requires the complete set on every request, and a brand-new outbox
   * record has no prior value to fall back on. */
  function getOutbox() { return _getAll('outbox'); }
  function getOutboxForFlight(flightId) {
    return getOutbox().then(function (rows) {
      var match = rows.filter(function (r) { return r.flight_id === flightId; });
      return match.length ? match[0] : null;
    });
  }
  function upsertOutboxForFlight(flightId, aircraftId, delta) {
    return getOutboxForFlight(flightId).then(function (existing) {
      if (existing) {
        existing.fields = existing.fields || {};
        var deltaFields = delta.fields || {};
        for (var k in deltaFields) {
          if (Object.prototype.hasOwnProperty.call(deltaFields, k)) {
            existing.fields[k] = deltaFields[k];
          }
        }
        if (delta.pilot) {
          existing.pilot = existing.pilot || { fields: {}, base: delta.pilot.base || {} };
          existing.pilot.fields = existing.pilot.fields || {};
          var deltaPilotFields = delta.pilot.fields || {};
          for (var k2 in deltaPilotFields) {
            if (Object.prototype.hasOwnProperty.call(deltaPilotFields, k2)) {
              existing.pilot.fields[k2] = deltaPilotFields[k2];
            }
          }
        }
        return _put('outbox', existing);
      }
      var record = {
        flight_id: flightId,
        aircraft_id: aircraftId,
        queued_at: Date.now(),
        fields: delta.fields || {},
        base: delta.base || {}
      };
      if (delta.pilot) {
        record.pilot = { fields: delta.pilot.fields || {}, base: delta.pilot.base || {} };
      }
      return _add('outbox', record);
    });
  }
  function deleteOutbox(id) { return _delete('outbox', id); }
  function updateOutboxRecord(record) { return _put('outbox', record); }
  function outboxCount() {
    return getOutbox().then(function (rows) { return rows.length; });
  }
  function outboxCountForAircraft(aircraftId) {
    return getOutbox().then(function (rows) {
      return rows.filter(function (r) { return r.aircraft_id === aircraftId; }).length;
    });
  }

  /* ── Pilot snapshot store — single record ("me"), from the 38h standalone
   * pilot-logbook snapshot API ── */
  function getPilotSnapshot() { return _get('pilot_snapshot', 'me'); }
  function putPilotSnapshot(snapshot) {
    var record = { id: 'me', fetched_at: Date.now() };
    for (var k in snapshot) {
      if (Object.prototype.hasOwnProperty.call(snapshot, k)) record[k] = snapshot[k];
    }
    record.id = 'me';
    return _put('pilot_snapshot', record);
  }

  /* ── Pilot outbox store — one record per standalone PilotLogbookEntry ── */
  function getPilotOutbox() { return _getAll('pilot_outbox'); }
  function getPilotOutboxForEntry(entryId) {
    return getPilotOutbox().then(function (rows) {
      var match = rows.filter(function (r) { return r.entry_id === entryId; });
      return match.length ? match[0] : null;
    });
  }
  function upsertPilotOutboxForEntry(entryId, delta) {
    return getPilotOutboxForEntry(entryId).then(function (existing) {
      if (existing) {
        existing.fields = existing.fields || {};
        var deltaFields = delta.fields || {};
        for (var k in deltaFields) {
          if (Object.prototype.hasOwnProperty.call(deltaFields, k)) {
            existing.fields[k] = deltaFields[k];
          }
        }
        return _put('pilot_outbox', existing);
      }
      var record = {
        entry_id: entryId,
        queued_at: Date.now(),
        fields: delta.fields || {},
        base: delta.base || {}
      };
      return _add('pilot_outbox', record);
    });
  }
  function deletePilotOutbox(id) { return _delete('pilot_outbox', id); }
  function updatePilotOutboxRecord(record) { return _put('pilot_outbox', record); }
  function pilotOutboxCount() {
    return getPilotOutbox().then(function (rows) { return rows.length; });
  }

  /* ── Canonical string formatting — must match app/offline/serialize.py ──
   * Given a field name and a raw form-input value, produce the identical
   * canonical string the server would (e.g. "1424.50" -> "1424.5",
   * "lfpg" -> "LFPG"), so offline edits diff correctly against a snapshot. */
  var _FIELD_KIND = {
    date: 'raw',
    departure_time: 'raw',
    arrival_time: 'raw',
    flight_time: 'dec1',
    flight_time_counter_start: 'dec1',
    flight_time_counter_end: 'dec1',
    engine_time_counter_start: 'dec1',
    engine_time_counter_end: 'dec1',
    fuel_added_qty: 'dec2',
    fuel_remaining_qty: 'dec2',
    oil_added_l: 'dec2',
    passenger_count: 'int',
    landing_count: 'int',
    departure_icao: 'icao',
    arrival_icao: 'icao'
  };

  function ohCanon(field, rawValue) {
    var kind = _FIELD_KIND[field] || 'trim';
    var v = (rawValue === null || rawValue === undefined) ? '' : String(rawValue).trim();
    switch (kind) {
      case 'raw':
        return v;
      case 'icao':
        return v.toUpperCase();
      case 'dec1':
      case 'dec2': {
        if (v === '') return '';
        var n = parseFloat(v);
        if (isNaN(n)) return '';
        return n.toFixed(kind === 'dec1' ? 1 : 2);
      }
      case 'int': {
        if (v === '') return '';
        var i = parseInt(v, 10);
        if (isNaN(i)) return '';
        return String(i);
      }
      default:
        return v;
    }
  }

  /* ── Canonical string formatting for PilotLogbookEntry fields — must match
   * app/offline/serialize.py's canonical_pilot_entry/canonical_linked_pilot_fields.
   * `departure_time`/`arrival_time` on a *linked* entry additionally follow
   * mirror-unless-override semantics; callers compare against the flight's
   * current time themselves (see ohPilotTimeMirrors) rather than baking that
   * into this generic per-field canonicalizer. */
  var _PILOT_FIELD_KIND = {
    date: 'raw',
    departure_time: 'raw',
    arrival_time: 'raw',
    entry_type: 'raw',
    fstd_type: 'raw',
    night_time: 'dec1',
    instrument_time: 'dec1',
    single_pilot_se: 'dec1',
    single_pilot_me: 'dec1',
    multi_pilot: 'dec1',
    function_pic: 'dec1',
    function_copilot: 'dec1',
    function_dual: 'dec1',
    function_instructor: 'dec1',
    fstd_duration: 'dec1',
    landings_day: 'int',
    landings_night: 'int'
  };

  function ohCanonPilot(field, rawValue) {
    var kind = _PILOT_FIELD_KIND[field] || 'trim';
    var v = (rawValue === null || rawValue === undefined) ? '' : String(rawValue).trim();
    switch (kind) {
      case 'raw':
        return v;
      case 'dec1': {
        if (v === '') return '';
        var n = parseFloat(v);
        if (isNaN(n)) return '';
        return n.toFixed(1);
      }
      case 'int': {
        if (v === '') return '';
        var i = parseInt(v, 10);
        if (isNaN(i)) return '';
        return String(i);
      }
      default:
        return v;
    }
  }

  /* True when a linked pilot-entry time value mirrors the flight's own time
   * (canonicalizes to "" in the sync/snapshot payload — see
   * canonical_linked_pilot_fields in app/offline/serialize.py). */
  function ohPilotTimeMirrors(pilotTimeCanon, flightTimeCanon) {
    return pilotTimeCanon === flightTimeCanon;
  }

  /* ── Automatic snapshot refresh — the "no explicit take-offline action" requirement ── */
  function _refreshSnapshotIfNeeded(aircraftId) {
    if (!navigator.onLine || !aircraftId) return;
    outboxCountForAircraft(aircraftId).then(function (count) {
      if (count > 0) return; /* frozen while this aircraft has pending edits */
      return fetch('/api/offline/aircraft/' + aircraftId + '/logbook')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data) return;
          return putSnapshot(aircraftId, data).then(function () {
            /* The workbench (and any other page reading this snapshot) may
             * have already rendered before this background fetch resolved
             * — especially on a first-ever visit with nothing cached yet.
             * Let listeners know a fresh snapshot landed so they can
             * re-render instead of being stuck showing "no entries". */
            document.dispatchEvent(new CustomEvent('oh-snapshot-updated', {
              detail: { aircraftId: aircraftId }
            }));
          });
        })
        .then(function () {
          if (navigator.storage && navigator.storage.persist) {
            navigator.storage.persist().catch(function () {});
          }
        });
    }).catch(function () {});
  }

  /* ── Sync engine — flushes the outbox, one flight at a time, serialized. ──
   * Shared by every page: the workbench triggers it after each edit; it
   * also runs on page load, the `online` event, and the SW's
   * OH_SYNC_REQUESTED message (see below). Never reads a page-load CSRF
   * token — a long offline period can outlive the 1 h token lifetime, so a
   * fresh one is fetched per batch (base.html's fetch() wrapper leaves an
   * explicitly-set X-CSRFToken header alone rather than overwriting it). */
  var _flushInProgress = false;

  function _patchSnapshotEntry(aircraftId, flightId, fields, pilotData) {
    return getSnapshot(aircraftId).then(function (snap) {
      if (!snap || !snap.entries) return;
      var idx = -1;
      for (var i = 0; i < snap.entries.length; i++) {
        if (snap.entries[i].id === flightId) { idx = i; break; }
      }
      if (idx === -1) return;
      snap.entries[idx].fields = fields;
      if (pilotData) {
        snap.entries[idx].pilot = pilotData;
      } else {
        delete snap.entries[idx].pilot;
      }
      return putSnapshot(aircraftId, snap);
    });
  }

  function _patchPilotSnapshotEntry(entryId, fields) {
    return getPilotSnapshot().then(function (snap) {
      if (!snap || !snap.entries) return;
      var idx = -1;
      for (var i = 0; i < snap.entries.length; i++) {
        if (snap.entries[i].id === entryId) { idx = i; break; }
      }
      if (idx === -1) return;
      snap.entries[idx].fields = fields;
      return putPilotSnapshot(snap);
    });
  }

  function _fireSyncEvent(detail) {
    document.dispatchEvent(new CustomEvent('oh-offline-sync', { detail: detail }));
  }

  function _syncOneRecord(record, token, summary) {
    var body = {
      fields: record.fields,
      base: record.base,
      force_duplicate: !!record.force_duplicate
    };
    if (record.pilot) {
      body.pilot = { fields: record.pilot.fields, base: record.pilot.base };
    }
    return fetch('/api/offline/flights/' + record.flight_id + '/sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
      body: JSON.stringify(body)
    }).then(function (resp) {
      return resp.json().then(function (data) {
        if (data.status === 'pilot_missing') {
          /* The linked entry was removed server-side since our snapshot —
           * drop the pilot section and retry immediately with flight
           * fields only, per the 38h spec. */
          delete record.pilot;
          return _syncOneRecord(record, token, summary);
        }
        if (resp.ok && data.status === 'ok') {
          summary.synced += 1;
          return deleteOutbox(record.id).then(function () {
            return _patchSnapshotEntry(
              record.aircraft_id, record.flight_id, data.entry, data.pilot
            );
          });
        }
        if (data.status === 'conflict') {
          summary.conflicts += 1;
          record.status = 'conflict';
          record.conflicts = data.conflicts;
          record.pilot_conflicts = data.pilot_conflicts || [];
          record.entry = data.entry;
          record.pilot_entry = data.pilot || null;
          return _put('outbox', record);
        }
        if (data.status === 'duplicate') {
          summary.errors += 1;
          record.status = 'duplicate';
          return _put('outbox', record);
        }
        /* invalid, or any other status the server might send */
        summary.errors += 1;
        record.status = 'error';
        record.errors = data.errors || [];
        return _put('outbox', record);
      });
    });
  }

  function _syncOnePilotRecord(record, token, summary) {
    return fetch('/api/offline/pilot/logbook/' + record.entry_id + '/sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
      body: JSON.stringify({ fields: record.fields, base: record.base })
    }).then(function (resp) {
      return resp.json().then(function (data) {
        if (resp.ok && data.status === 'ok') {
          summary.synced += 1;
          return deletePilotOutbox(record.id).then(function () {
            return _patchPilotSnapshotEntry(record.entry_id, data.entry);
          });
        }
        if (data.status === 'conflict') {
          summary.conflicts += 1;
          record.status = 'conflict';
          record.conflicts = data.conflicts;
          record.entry = data.entry;
          return _put('pilot_outbox', record);
        }
        summary.errors += 1;
        record.status = 'error';
        record.errors = data.errors || [];
        return _put('pilot_outbox', record);
      });
    });
  }

  function flush() {
    if (_flushInProgress || !navigator.onLine) return Promise.resolve();
    _flushInProgress = true;

    return Promise.all([getOutbox(), getPilotOutbox()]).then(function (results) {
      var pending = results[0]
        .filter(function (r) { return !r.status || r.status === 'pending'; })
        .sort(function (a, b) { return a.queued_at - b.queued_at; });
      var pendingPilot = results[1]
        .filter(function (r) { return !r.status || r.status === 'pending'; })
        .sort(function (a, b) { return a.queued_at - b.queued_at; });
      if (!pending.length && !pendingPilot.length) return;

      return fetch('/api/offline/csrf').then(function (r) {
        if (r.status === 401) {
          _fireSyncEvent({ authRequired: true });
          return null;
        }
        return r.json();
      }).then(function (csrfData) {
        if (!csrfData) return;
        var token = csrfData.csrf_token;
        var summary = { synced: 0, conflicts: 0, errors: 0 };
        var total = pending.length + pendingPilot.length;
        var current = 0;
        var chain = Promise.resolve();
        pending.forEach(function (record) {
          chain = chain.then(function () {
            current += 1;
            document.dispatchEvent(new CustomEvent('oh-offline-sync-progress', {
              detail: { current: current, total: total }
            }));
            return _syncOneRecord(record, token, summary);
          });
        });
        pendingPilot.forEach(function (record) {
          chain = chain.then(function () {
            current += 1;
            document.dispatchEvent(new CustomEvent('oh-offline-sync-progress', {
              detail: { current: current, total: total }
            }));
            return _syncOnePilotRecord(record, token, summary);
          });
        });
        return chain.then(function () {
          _fireSyncEvent(summary);
        }, function () {
          /* a request failed at the network level — stop the batch, keep everything queued */
        });
      });
    }).catch(function () {
      /* offline/flaky — nothing to do, records stay queued */
    }).then(function () {
      _flushInProgress = false;
    });
  }

  window.addEventListener('online', function () { flush(); });
  document.addEventListener('DOMContentLoaded', function () {
    if (navigator.onLine) flush();
  });
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', function (e) {
      if (e.data && e.data.type === 'OH_SYNC_REQUESTED') flush();
    });
  }

  /* Precache the workbench + offline-changes page for this aircraft so a
   * single online visit to the logbook is enough to work offline later —
   * no page has to be manually opened first. Safe to send before those
   * routes exist: the SW only caches OK responses (see sw.js). */
  function _precacheOfflinePages(aircraftId) {
    if (!navigator.onLine || !('serviceWorker' in navigator)) return;
    navigator.serviceWorker.ready.then(function (reg) {
      if (!reg.active) return;
      reg.active.postMessage({
        type: 'OH_PRECACHE',
        urls: ['/aircraft/' + aircraftId + '/logbook/offline', '/offline/changes']
      });
    }).catch(function () {});
  }

  /* Same "no explicit take-offline action" auto-caching as
   * _refreshSnapshotIfNeeded, for the current user's own standalone
   * pilot-logbook entries (38h/38i) rather than one aircraft's flights. */
  function _refreshPilotSnapshotIfNeeded() {
    if (!navigator.onLine) return;
    pilotOutboxCount().then(function (count) {
      if (count > 0) return; /* frozen while pilot edits are pending */
      return fetch('/api/offline/pilot/logbook')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data) return;
          return putPilotSnapshot(data).then(function () {
            document.dispatchEvent(new CustomEvent('oh-pilot-snapshot-updated'));
          });
        })
        .then(function () {
          if (navigator.storage && navigator.storage.persist) {
            navigator.storage.persist().catch(function () {});
          }
        });
    }).catch(function () {});
  }

  function _precachePilotOfflinePages() {
    if (!navigator.onLine || !('serviceWorker' in navigator)) return;
    navigator.serviceWorker.ready.then(function (reg) {
      if (!reg.active) return;
      reg.active.postMessage({
        type: 'OH_PRECACHE',
        urls: ['/pilot/logbook/offline', '/offline/changes']
      });
    }).catch(function () {});
  }

  function _init() {
    var root = document.querySelector('[data-oh-aircraft-id]');
    if (root && !root.dataset.ohOfflineDbInited) {
      root.dataset.ohOfflineDbInited = '1';
      var aircraftId = parseInt(root.getAttribute('data-oh-aircraft-id'), 10);
      _refreshSnapshotIfNeeded(aircraftId);
      _precacheOfflinePages(aircraftId);
    }
    var pilotRoot = document.querySelector('[data-oh-pilot-logbook]');
    if (pilotRoot && !pilotRoot.dataset.ohOfflineDbInited) {
      pilotRoot.dataset.ohOfflineDbInited = '1';
      _refreshPilotSnapshotIfNeeded();
      _precachePilotOfflinePages();
    }
  }
  document.addEventListener('DOMContentLoaded', _init);
  document.addEventListener('htmx:afterSettle', _init);

  window.OhOffline = {
    getQueue: getQueue,
    addQueueEntry: addQueueEntry,
    updateQueueEntry: updateQueueEntry,
    deleteQueueEntry: deleteQueueEntry,
    getSnapshot: getSnapshot,
    putSnapshot: putSnapshot,
    getOutbox: getOutbox,
    getOutboxForFlight: getOutboxForFlight,
    upsertOutboxForFlight: upsertOutboxForFlight,
    deleteOutbox: deleteOutbox,
    updateOutboxRecord: updateOutboxRecord,
    outboxCount: outboxCount,
    outboxCountForAircraft: outboxCountForAircraft,
    getPilotSnapshot: getPilotSnapshot,
    putPilotSnapshot: putPilotSnapshot,
    getPilotOutbox: getPilotOutbox,
    getPilotOutboxForEntry: getPilotOutboxForEntry,
    upsertPilotOutboxForEntry: upsertPilotOutboxForEntry,
    deletePilotOutbox: deletePilotOutbox,
    updatePilotOutboxRecord: updatePilotOutboxRecord,
    pilotOutboxCount: pilotOutboxCount,
    ohCanon: ohCanon,
    ohCanonPilot: ohCanonPilot,
    ohPilotTimeMirrors: ohPilotTimeMirrors,
    flush: flush
  };
})();
