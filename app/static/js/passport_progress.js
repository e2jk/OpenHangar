(function () {
  function init() {
    var root = document.getElementById('passport-progress');
    if (!root || root.dataset.ohInited || typeof L === 'undefined') return;
    root.dataset.ohInited = '1';
    var map = L.map('passport-progress-map', { zoomAnimation: false, fadeAnimation: false, markerZoomAnimation: false }).setView([50.5, 5], 5);
    window.ohAddBaseLayers(map, null);
    var positions = [];
    JSON.parse(root.dataset.fields).forEach(function (field) {
      if (field.latitude === null || field.longitude === null) return;
      var radius = root.dataset.frequency === '1' ? (field.count ? Math.min(18, 8 + 2 * Math.log2(field.count)) : 6) : 7;
      var popup = document.createElement('div');
      var link = document.createElement('a');
      link.textContent = (field.ident ? field.ident + ' — ' : '') + field.name;
      link.href = root.dataset.targetUrl.replace(/\/0$/, '/' + field.target_id);
      popup.appendChild(link);
      var info = document.createElement('p');
      info.textContent = (field.visited ? root.dataset.visited : root.dataset.unvisited) + ' · ' + root.dataset.visits + ': ' + field.count + (field.is_closed ? ' · ' + root.dataset.closed : '');
      popup.appendChild(info);
      L.circleMarker([field.latitude, field.longitude], { radius: radius, color: field.visited ? '#0d6efd' : '#6c757d', fillOpacity: field.visited ? 0.8 : 0.25, weight: field.visited ? 3 : 1, dashArray: field.is_closed ? '4 4' : null }).bindPopup(popup).addTo(map);
      positions.push([field.latitude, field.longitude]);
    });
    if (positions.length) map.fitBounds(positions, { padding: [25, 25], maxZoom: 12 });
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
