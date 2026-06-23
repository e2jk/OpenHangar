(function () {
  function init() {
    var mapEl = document.getElementById('detail-map');
    if (!mapEl || mapEl.dataset.ohInited) return;
    mapEl.dataset.ohInited = '1';

    var geojsonEl = document.getElementById('detail-map-geojson');
    if (!geojsonEl) return;
    var geojson = JSON.parse(geojsonEl.textContent);
    var opiKey = mapEl.dataset.openaipKey || null;
    var dep = mapEl.dataset.dep || '';
    var arr = mapEl.dataset.arr || '';
    var isDark = document.documentElement.getAttribute('data-bs-theme') === 'dark';

    var map = L.map(mapEl);
    if (opiKey) {
      L.tileLayer(isDark ? 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png' : 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
        { maxZoom: 19, subdomains: 'abcd', attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>' }
      ).addTo(map);
      L.tileLayer('https://api.tiles.openaip.net/api/data/openaip/{z}/{x}/{y}.png?apiKey=' + opiKey,
        { maxZoom: 14, attribution: '&copy; <a href="https://www.openaip.net">OpenAIP</a>' }
      ).addTo(map);
    } else {
      L.tileLayer(isDark ? 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png' : 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        isDark
          ? { maxZoom: 19, subdomains: 'abcd', attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>' }
          : { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }
      ).addTo(map);
    }

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
})();
