(function () {
  function init() {
    var root = document.getElementById('passport-builder');
    if (!root || root.dataset.ohInited) return;
    root.dataset.ohInited = '1';
    var mapEl = document.getElementById('passport-selection-map');
    var dataEl = document.getElementById('passport-map-data');
    if (!mapEl || !dataEl || typeof L === 'undefined') return;
    var data = JSON.parse(dataEl.dataset.map);
    var selected = new Set(data.selected);
    var map = L.map(mapEl, { zoomAnimation: false, fadeAnimation: false, markerZoomAnimation: false }).setView([50.5, 5], 5);
    window.ohAddBaseLayers(map, null);
    var markers = new Map();
    var positions = [];
    data.fields.forEach(function (field) {
      if (field.latitude === null || field.longitude === null) return;
      var label = document.createElement('span');
      label.textContent = (field.ident ? field.ident + ' — ' : '') + field.name;
      var marker = L.circleMarker([field.latitude, field.longitude], { radius: 6 });
      marker.bindTooltip(label).addTo(map);
      markers.set(field.key, marker);
      positions.push([field.latitude, field.longitude]);
    });
    function repaint() {
      root.querySelectorAll('input[name="choices"]').forEach(function (input) {
        if (input.checked) selected.add(input.value); else selected.delete(input.value);
      });
      markers.forEach(function (marker, key) {
        var active = selected.has(key);
        marker.setStyle({ color: active ? '#0d6efd' : '#6c757d', fillOpacity: active ? 0.8 : 0.25, weight: active ? 3 : 1 });
      });
    }
    repaint();
    root.addEventListener('change', repaint);
    if (positions.length) map.fitBounds(positions, { padding: [20, 20], maxZoom: 12 });
    else map.setView([50.5, 5], 5);
    function cleanup(event) {
      if (event.detail.elt === root || event.detail.elt.contains(root)) {
        root.removeEventListener('change', repaint);
        map.remove();
        document.removeEventListener('htmx:beforeCleanupElement', cleanup);
      }
    }
    document.addEventListener('htmx:beforeCleanupElement', cleanup);
  }
  document.addEventListener('htmx:beforeSwap', function (event) {
    var xhr = event.detail.xhr;
    if (xhr && (xhr.status === 400 || xhr.status === 404 || xhr.status === 409) && xhr.getResponseHeader('X-OH-Passport-Builder') === '1') {
      event.detail.shouldSwap = true;
      event.detail.isError = false;
    }
  });
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
