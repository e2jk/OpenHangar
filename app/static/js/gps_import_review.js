(function () {
  var _mapInited = typeof WeakSet !== 'undefined' ? new WeakSet() : null;

  function init() {
    var dataEl = document.getElementById('gps-review-data');
    if (!dataEl) return;
    if (_mapInited ? _mapInited.has(dataEl) : dataEl.dataset.ohInited) return;
    if (_mapInited) _mapInited.add(dataEl); else dataEl.dataset.ohInited = '1';
    var data = JSON.parse(dataEl.textContent);
    var segments = data.segments;
    var opiKey = data.opiKey;

    function makeTileLayer(map) {
      window.ohAddBaseLayers(map, opiKey);
    }

    segments.forEach(function (seg, i) {
      var el = document.getElementById('map_' + i);
      if (!el || !seg.track_geojson) return;
      var map = L.map(el);
      makeTileLayer(map);
      var geojson = L.geoJSON(seg.track_geojson, { style: { color: '#d526e0', weight: 6, opacity: 0.8 } });
      var bounds = geojson.getBounds();
      if (bounds.isValid()) map.fitBounds(bounds, { padding: [10, 10] });
      else map.setView([0, 0], 2);
      geojson.addTo(map);
    });

    var radios = document.querySelectorAll('input[name="pilot_role_global"]');
    var hiddens = document.querySelectorAll('input.pilot-role-hidden');
    function syncRole() {
      var sel = document.querySelector('input[name="pilot_role_global"]:checked');
      var val = sel ? sel.value : 'pic';
      hiddens.forEach(function (h) { h.value = val; });
    }
    radios.forEach(function (r) { r.addEventListener('change', syncRole); });
    syncRole();

    document.querySelectorAll('.seg-card').forEach(function (card, i) {
      var noneRadio = card.querySelector('input[value=""]');
      if (noneRadio && noneRadio.name === 'matched_flight_id') {
        var noMatchWidget = document.getElementById('no-match-widget-' + i);
        var candidateRadios = card.querySelectorAll('input[name="matched_flight_id"]');
        function toggleNoMatch() {
          var checked = card.querySelector('input[name="matched_flight_id"]:checked');
          var show = checked && checked.value === '';
          if (noMatchWidget) noMatchWidget.classList.toggle('d-none', !show);
        }
        candidateRadios.forEach(function (r) { r.addEventListener('change', toggleNoMatch); });
        toggleNoMatch();
      }
      var resRadios = card.querySelectorAll('input[name="resolution"]');
      var otherFields = document.getElementById('res-other-fields-' + i);
      var acPicker = document.getElementById('res-ac-picker-' + i);
      function toggleResolution() {
        var checked = card.querySelector('input[name="resolution"]:checked');
        var isOther = checked && checked.value === 'other_aircraft';
        if (otherFields) otherFields.classList.toggle('d-none', !isOther);
        if (acPicker) acPicker.classList.toggle('d-none', isOther);
      }
      resRadios.forEach(function (r) { r.addEventListener('change', toggleResolution); });
      toggleResolution();
    });
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
  document.addEventListener('htmx:historyRestore', init);
})();
