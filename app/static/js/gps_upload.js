(function () {
  function init() {
    var gpsFile = document.querySelector('[data-gps-upload-init]');
    if (!gpsFile || gpsFile.dataset.ohInited) return;
    gpsFile.dataset.ohInited = '1';

    var pfx = gpsFile.dataset.prefix;
    var statusEl = document.getElementById(gpsFile.dataset.statusId);
    var helpEl = document.getElementById(gpsFile.dataset.helpId);
    var parseUrl = gpsFile.dataset.parseUrl;
    var fieldMap = JSON.parse((document.getElementById(pfx + '-field-map') || { textContent: '{}' }).textContent);
    var i18n = JSON.parse((document.getElementById(pfx + '-i18n') || { textContent: '{}' }).textContent);
    var opiKey = gpsFile.dataset.openaipKey || null;

    var _map = null, _layer = null;

    function renderMap(geojsonStr) {
      var mapEl = document.getElementById(pfx + '-map');
      if (!mapEl || !geojsonStr) return;
      var geojson;
      try { geojson = JSON.parse(geojsonStr); } catch (e) { return; }
      mapEl.style.display = '';
      if (!_map) {
        _map = L.map(mapEl);
        if (opiKey) {
          L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
            { maxZoom: 19, subdomains: 'abcd', attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>' }
          ).addTo(_map);
          L.tileLayer('https://api.tiles.openaip.net/api/data/openaip/{z}/{x}/{y}.png?apiKey=' + opiKey,
            { maxZoom: 14, attribution: '&copy; <a href="https://www.openaip.net">OpenAIP</a>' }
          ).addTo(_map);
        } else {
          L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
            { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }
          ).addTo(_map);
        }
        _layer = L.geoJSON(geojson, { style: { color: '#d526e0', weight: 3 } }).addTo(_map);
      } else {
        _layer.clearLayers().addData(geojson);
      }
      var b = _layer.getBounds();
      if (b.isValid()) _map.fitBounds(b, { padding: [16, 16] });
    }

    function setStatus(ok, msg) {
      if (!statusEl) return;
      var cls = ok ? 'alert-info' : 'alert-warning';
      var icon = ok ? 'check-circle' : 'exclamation-triangle';
      statusEl.innerHTML = '<div class="alert ' + cls + ' py-2 small"><i class="bi bi-' + icon + ' me-1"></i>' + msg + '</div>';
    }

    var initScript = document.getElementById(pfx + '-initial-geojson');
    var geojsonInp = document.getElementById(pfx + '-inp-geojson');
    if (initScript) {
      var initStr = initScript.textContent.trim();
      if (geojsonInp) geojsonInp.value = initStr;
      renderMap(initStr);
      if (helpEl) helpEl.style.display = 'none';
    } else if (geojsonInp && geojsonInp.value) {
      renderMap(geojsonInp.value);
      if (helpEl) helpEl.style.display = 'none';
    }

    gpsFile.addEventListener('change', function () {
      if (!gpsFile.files[0]) return;
      var fd = new FormData();
      fd.append('gps_file', gpsFile.files[0]);
      var acSelect = document.getElementById('aircraft_id');
      if (acSelect && acSelect.value) fd.append('aircraft_id', acSelect.value);
      if (statusEl) {
        statusEl.innerHTML = '<div class="alert alert-secondary py-2 small"><span class="spinner-border spinner-border-sm me-1" role="status"></span>' + i18n.parsing + '</div>';
      }
      gpsFile.disabled = true;
      fetch(parseUrl, { method: 'POST', body: fd })
        .then(function (r) { return r.json(); })
        .then(function (resp) {
          if (resp.success) {
            var d = resp.data;
            Object.keys(fieldMap).forEach(function (gpsKey) {
              var el = document.getElementById(fieldMap[gpsKey]);
              var val = d[gpsKey];
              if (el && val !== undefined && val !== null && String(val) !== '') el.value = val;
            });
            document.getElementById(pfx + '-inp-filename').value = d.filename || '';
            document.getElementById(pfx + '-inp-device-id').value = d.device_id || '';
            document.getElementById(pfx + '-inp-block-off').value = d.block_off_utc || '';
            document.getElementById(pfx + '-inp-block-on').value = d.block_on_utc || '';
            document.getElementById(pfx + '-inp-geojson').value = d.geojson || '';
            renderMap(d.geojson || '');
            if (helpEl) helpEl.style.display = 'none';
            if (resp.suggested_aircraft_id) {
              var ac = document.getElementById('aircraft_id');
              if (ac && !ac.value) { ac.value = String(resp.suggested_aircraft_id); ac.dispatchEvent(new Event('change')); }
            }
            var msg = resp.message;
            if (resp.duplicate) {
              var dup = resp.duplicate;
              msg += '<br><i class="bi bi-exclamation-triangle me-1"></i><strong>' + i18n.dupLabel + '</strong> '
                + dup.dep + '→' + dup.arr + ' ' + dup.date + ' ' + i18n.exists + ' ' + i18n.review;
            }
            setStatus(true, msg);
          } else {
            setStatus(false, resp.error || i18n.parseError);
          }
        })
        .catch(function () { setStatus(false, i18n.parseError); })
        .finally(function () { gpsFile.disabled = false; });
    });
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
