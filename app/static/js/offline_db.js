/* OpenHangar offline data layer — the read-only logbook snapshots in
 * IndexedDB, one database per account (see offline_identity.js). While
 * online, opening a logbook refreshes its snapshot so the offline logbook
 * pages (offline_logbook.js) can show it later without a connection.
 *
 * Offline editing was removed: database version 4 drops the old write
 * stores (queue, outbox, pilot_outbox), and the legacy unowned database
 * from before per-account storage is deleted. */
(function () {
  'use strict';

  var _DB_NAME = 'openhangar-offline';
  var _DB_VERSION = 4;
  var _WRITE_STORES = ['queue', 'outbox', 'pilot_outbox'];

  function _identity() { return window.OhOfflineIdentity; }

  function _upgrade(event) {
    var db = event.target.result;
    _WRITE_STORES.forEach(function (store) {
      if (db.objectStoreNames.contains(store)) db.deleteObjectStore(store);
    });
    if (!db.objectStoreNames.contains('snapshots')) db.createObjectStore('snapshots', { keyPath: 'aircraft_id' });
    if (!db.objectStoreNames.contains('pilot_snapshot')) db.createObjectStore('pilot_snapshot', { keyPath: 'id' });
    if (event.oldVersion > 0 && event.oldVersion < 4) {
      /* Snapshot rows changed shape with the read-only pages: refetch them. */
      var tx = event.target.transaction;
      tx.objectStore('snapshots').clear();
      tx.objectStore('pilot_snapshot').clear();
    }
  }

  function _open(name) {
    return new Promise(function (resolve, reject) {
      var req = indexedDB.open(name, _DB_VERSION);
      req.onupgradeneeded = _upgrade;
      req.onsuccess = function (event) { resolve(event.target.result); };
      req.onerror = function (event) { reject(event.target.error); };
      req.onblocked = function () { reject(new Error('offline-db-blocked')); };
    });
  }

  function _openDb() {
    var who = _identity().current();
    if (!who) return Promise.reject(new Error('offline-account-changed'));
    return _open(_DB_NAME + '-' + who.owner);
  }

  /* Remove what offline editing left in this browser: the legacy unowned
   * database, and the write stores of every account's database (another
   * account's is upgraded here too, so its pending records do not linger
   * until that person signs in again). Best effort, once per page load. */
  function _removeEditingData() {
    if (!window.indexedDB) return;
    try { indexedDB.deleteDatabase(_DB_NAME); } catch (e) { /* best effort */ }
    if (!indexedDB.databases) return;
    indexedDB.databases().then(function (databases) {
      databases.forEach(function (info) {
        if (info.name && info.name.indexOf(_DB_NAME + '-') === 0 && info.version < _DB_VERSION) {
          _open(info.name).then(function (db) { db.close(); }).catch(function () {});
        }
      });
    }).catch(function () {});
  }

  function _operation(store, mode, operation) {
    var expected = _identity().scope();
    if (!expected) return Promise.reject(new Error('offline-account-changed'));
    return _openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx;
        var result;
        try {
          _identity().assertScope(expected);
          tx = db.transaction(store, mode);
          var req = operation(tx.objectStore(store));
          req.onsuccess = function () { result = req.result; };
          tx.oncomplete = function () {
            db.close();
            try { _identity().assertScope(expected); resolve(result); }
            catch (error) { reject(error); }
          };
          tx.onerror = tx.onabort = function () { db.close(); reject(tx.error || new Error('offline-write-aborted')); };
        } catch (error) { db.close(); reject(error); }
      });
    });
  }
  function _get(store, key) {
    if (!_identity().current()) return Promise.resolve(null);
    return _operation(store, 'readonly', function (objectStore) { return objectStore.get(key); })
      .then(function (record) {
        /* Snapshots are permission-sensitive; a new login must refresh them. */
        return record && record._scope === _identity().scope() ? record : null;
      });
  }
  function _put(store, value) {
    value = Object.assign({}, value, { _scope: _identity().scope() });
    return _operation(store, 'readwrite', function (objectStore) { return objectStore.put(value); });
  }

  /* ── Snapshots store — one record per aircraft ── */
  function getSnapshot(aircraftId) { return _get('snapshots', aircraftId); }
  function _putSnapshot(aircraftId, snapshot) {
    return _put('snapshots', Object.assign({}, snapshot, { aircraft_id: aircraftId, fetched_at: Date.now() }));
  }

  /* ── Pilot snapshot store — a single record ("me") per account database ── */
  function getPilotSnapshot() { return _get('pilot_snapshot', 'me'); }
  function _putPilotSnapshot(snapshot) {
    return _put('pilot_snapshot', Object.assign({}, snapshot, { id: 'me', fetched_at: Date.now() }));
  }

  function _persistStorage() {
    if (navigator.storage && navigator.storage.persist) {
      navigator.storage.persist().catch(function () {});
    }
  }

  /* ── Automatic snapshot refresh: no explicit "take offline" action ── */
  function _refresh(url, store, eventName, detail) {
    var expected = _identity().scope();
    if (!navigator.onLine || !expected) return;
    _identity().fetch(url)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        _identity().assertScope(expected);
        return store(data).then(function () {
          /* The offline page may have rendered before this background fetch
           * resolved (e.g. a first visit): let it re-render. */
          document.dispatchEvent(new CustomEvent(eventName, { detail: detail }));
          _persistStorage();
        });
      })
      .catch(function () {});
  }

  /* Cache the offline logbook page too, so one online visit to a logbook is
   * enough to read it offline later. */
  function _precache(url) {
    if (!navigator.onLine || !('serviceWorker' in navigator)) return;
    var who = _identity().current();
    if (!who) return;
    navigator.serviceWorker.ready.then(function (reg) {
      if (!reg.active) return;
      reg.active.postMessage({ type: 'OH_PRECACHE', owner: who.owner, session: who.session, urls: [url] });
    }).catch(function () {});
  }

  function _init() {
    var root = document.querySelector('[data-oh-aircraft-id]');
    if (root && !root.dataset.ohOfflineDbInited) {
      root.dataset.ohOfflineDbInited = '1';
      var aircraftId = parseInt(root.getAttribute('data-oh-aircraft-id'), 10);
      if (aircraftId) {
        _refresh('/api/offline/aircraft/' + aircraftId + '/logbook',
          function (data) { return _putSnapshot(aircraftId, data); },
          'oh-snapshot-updated', { aircraftId: aircraftId });
        _precache('/aircraft/' + aircraftId + '/logbook/offline');
      }
    }
    var pilotRoot = document.querySelector('[data-oh-pilot-logbook]');
    if (pilotRoot && !pilotRoot.dataset.ohOfflineDbInited) {
      pilotRoot.dataset.ohOfflineDbInited = '1';
      _refresh('/api/offline/pilot/logbook', _putPilotSnapshot, 'oh-pilot-snapshot-updated');
      _precache('/pilot/logbook/offline');
    }
  }
  _removeEditingData();
  document.addEventListener('DOMContentLoaded', _init);
  document.addEventListener('htmx:afterSettle', _init);

  window.OhOffline = {
    getSnapshot: getSnapshot,
    getPilotSnapshot: getPilotSnapshot
  };
})();
