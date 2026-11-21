(function () {
  function init() {
    var settings = document.getElementById('passport-public-settings');
    if (settings && !settings.dataset.ohInited) {
      settings.dataset.ohInited = '1';
      var copy = settings.querySelector('[data-public-copy]');
      if (copy) copy.addEventListener('click', function () {
        var input = document.getElementById('passport-public-url');
        input.focus(); input.select();
        function copied() { copy.textContent = copy.dataset.copied; }
        function fallback() {
          input.select();
          try { if (document.execCommand('copy')) copied(); }
          catch (error) { input.select(); }
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(input.value).then(copied).catch(fallback);
        } else fallback();
      });
    }
    var root = document.getElementById('passport-public');
    if (!root || root.dataset.ohInited) return;
    root.dataset.ohInited = '1';
    var el = document.getElementById('passport-public-map');
    if (!el || !window.L) return;
    var map = L.map(el).setView([50, 5], 5);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', maxZoom: 18
    }).addTo(map);
    var targets = JSON.parse(root.dataset.targets);
    var markers = [], points = [];
    targets.forEach(function (target) {
      if (target.latitude == null || target.longitude == null) return;
      var point = [target.latitude, target.longitude];
      points.push(point);
      var text = document.createElement('span');
      text.textContent = target.name + ' — ' + (target.visited ? root.dataset.visited : root.dataset.unvisited) + (target.month ? ' (' + target.month + ')' : '');
      var marker = L.circleMarker(point, { radius: target.visited ? 8 : 5, color: target.visited ? '#166534' : '#6b7280', fillOpacity: target.visited ? 0.85 : 0.25 }).addTo(map).bindPopup(text);
      markers.push({ marker: marker, target: target });
    });
    if (points.length) map.fitBounds(points, { padding: [20, 20], maxZoom: 10 });
    var toggle = root.querySelector('[data-public-frequency]');
    if (toggle) toggle.addEventListener('change', function () {
      markers.forEach(function (row) {
        row.marker.setRadius(toggle.checked && row.target.frequency != null ? Math.min(22, 5 + 3 * Math.sqrt(row.target.frequency)) : (row.target.visited ? 8 : 5));
      });
    });
    document.body.addEventListener('htmx:beforeSwap', function cleanup() {
      map.remove(); document.body.removeEventListener('htmx:beforeSwap', cleanup);
    });
  }
  window.addEventListener('pageshow', function (event) {
    if (event.persisted && document.getElementById('passport-public')) window.location.reload();
  });
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
