(function () {
  /* WeakSet guard: survives hx-boost history restore (dataset attrs are
     serialised into the sessionStorage snapshot; WeakSet references are not). */
  var _mapInited = typeof WeakSet !== 'undefined' ? new WeakSet() : null;

  function init() {
    var mapEl = document.getElementById('detail-map');
    if (!mapEl) return;
    if (_mapInited ? _mapInited.has(mapEl) : mapEl.dataset.ohInited) return;
    if (_mapInited) _mapInited.add(mapEl); else mapEl.dataset.ohInited = '1';

    var geojsonEl = document.getElementById('detail-map-geojson');
    if (!geojsonEl) return;
    var geojson = JSON.parse(geojsonEl.textContent);
    var opiKey = mapEl.dataset.openaipKey || null;
    var dep = mapEl.dataset.dep || '';
    var arr = mapEl.dataset.arr || '';

    var map = L.map(mapEl);
    window.ohAddBaseLayers(map, opiKey);

    var layer = L.geoJSON(geojson, { style: { color: '#d526e0', weight: 6, opacity: 0.85 } });
    var bounds = layer.getBounds();
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [30, 30] });
    else map.setView([0, 0], 2);
    layer.addTo(map);

    var coords = null;
    if (geojson.type === 'Feature' && geojson.geometry && geojson.geometry.coordinates) {
      coords = geojson.geometry.coordinates;
    } else if (geojson.type === 'FeatureCollection' && geojson.features && geojson.features[0]) {
      var g = geojson.features[0].geometry;
      if (g && g.coordinates) coords = g.coordinates;
    }
    if (coords && coords.length > 0) {
      L.circleMarker([coords[0][1], coords[0][0]], { radius: 7, color: '#28a745', fillColor: '#28a745', fillOpacity: 1 }).bindTooltip(dep).addTo(map);
      L.circleMarker([coords[coords.length - 1][1], coords[coords.length - 1][0]], { radius: 7, color: '#dc3545', fillColor: '#dc3545', fillOpacity: 1 }).bindTooltip(arr).addTo(map);
    }
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
  document.addEventListener('htmx:historyRestore', init);
})();
