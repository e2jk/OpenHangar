(function () {
  function init() {
    var root = document.getElementById('passport-discovery');
    if (!root || root.dataset.ohInited) return;
    root.dataset.ohInited = '1';
    var slider = document.getElementById('discovery-slider');
    var limit = document.getElementById('discovery-limit');
    var mode = document.getElementById('discovery-mode');
    // The slider covers a practical range for the mode (nm or minutes); the
    // number field still accepts larger values, pinning the slider at its end.
    function setSliderRange() {
      slider.max = mode && mode.value === 'time' ? slider.dataset.maxTime : slider.dataset.maxDistance;
      slider.value = limit.value;
    }
    slider.addEventListener('input', function () { limit.value = slider.value; });
    limit.addEventListener('input', function () { slider.value = limit.value; });
    if (mode) mode.addEventListener('change', setSliderRange);
    if (typeof L === 'undefined') return;
    var data = JSON.parse(root.dataset.map);
    var map = L.map('passport-discovery-map', { zoomAnimation: false, fadeAnimation: false, markerZoomAnimation: false }).setView([50.5, 5], 5);
    window.ohAddBaseLayers(map, null);
    if (data.origin && data.origin.latitude !== null && data.origin.longitude !== null) {
      var point = [data.origin.latitude, data.origin.longitude];
      var label = document.createElement('span');
      label.textContent = data.origin.name;
      L.circleMarker(point, { radius: 9, color: '#0d6efd' }).bindTooltip(label).addTo(map);
      if (data.radius_nm !== null) {
        var circle = L.circle(point, { radius: data.radius_nm * 1852, color: '#0d6efd', fillOpacity: 0.05 }).addTo(map);
        map.fitBounds(circle.getBounds(), { padding: [20, 20], maxZoom: 12 });
      } else map.setView(point, 8);
    }
    data.fields.forEach(function (field) {
      var link = document.createElement('a');
      link.textContent = field.name + ' · ' + field.distance_nm.toFixed(1) + ' nm';
      // A same-site path built by the server for the passport target page.
      if (typeof field.url === 'string' && /^\/[^/\\]/.test(field.url)) link.href = field.url;
      L.circleMarker([field.latitude, field.longitude], { radius: 6, color: '#6c757d' }).bindPopup(link).addTo(map);
    });
    function cleanup(event) {
      if (event.detail.elt === root || event.detail.elt.contains(root)) {
        map.remove();
        document.removeEventListener('htmx:beforeCleanupElement', cleanup);
      }
    }
    document.addEventListener('htmx:beforeCleanupElement', cleanup);
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
