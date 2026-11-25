(function () {
  // Marker size. Without the frequency view every marker has the same weight,
  // however often it was visited; with it, size grows with the logarithm of
  // the visit count and is capped, so a busy home base cannot swamp the map.
  function markerRadius(count, frequency) {
    if (!frequency) return 7;
    return count ? Math.min(18, 8 + 2 * Math.log2(count)) : 6;
  }
  window.ohPassportMarkerRadius = markerRadius;

  function init() {
    if (typeof L === 'undefined') return;
    document.querySelectorAll('#passport-progress, [data-passport-progress]').forEach(initMap);
  }

  function initMap(root) {
    if (root.dataset.ohInited) return;
    root.dataset.ohInited = '1';
    var map = L.map(root.querySelector('.passport-selection-map'), { zoomAnimation: false, fadeAnimation: false, markerZoomAnimation: false }).setView([50.5, 5], 5);
    window.ohAddBaseLayers(map, null);
    var positions = [];
    JSON.parse(root.dataset.fields).forEach(function (field) {
      if (field.latitude === null || field.longitude === null) return;
      var radius = markerRadius(field.count, root.dataset.frequency === '1');
      var popup = document.createElement('div');
      var link = document.createElement(root.dataset.targetUrl ? 'a' : 'strong');
      link.textContent = (field.ident ? field.ident + ' — ' : '') + field.name;
      if (root.dataset.targetUrl) link.href = root.dataset.targetUrl.replace(/\/0$/, '/' + field.target_id);
      popup.appendChild(link);
      var info = document.createElement('p');
      info.textContent = (field.state === 'others' ? root.dataset.others : (field.visited ? root.dataset.visited : root.dataset.unvisited)) + ' · ' + root.dataset.visits + ': ' + field.count + (field.is_closed ? ' · ' + root.dataset.closed : '');
      popup.appendChild(info);
      (field.editions || []).forEach(function (edition) {
        var line = document.createElement('div');
        line.textContent = edition.label + ': ' + edition.count;
        popup.appendChild(line);
      });
      (field.friends || []).forEach(function (friend) {
        var line = document.createElement('div');
        line.textContent = friend.name + ' · ' + friend.editions.map(function (edition) {
          return edition.label + ': ' + edition.dates.join(', ');
        }).join(' · ');
        popup.appendChild(line);
      });
      if (field.current === false && root.dataset.notCurrent) {
        var badge = document.createElement('p');
        badge.textContent = root.dataset.notCurrent;
        popup.appendChild(badge);
      }
      L.circleMarker([field.latitude, field.longitude], { radius: radius, color: field.visited || field.state === 'others' ? (field.colour || '#0d6efd') : '#6c757d', fillOpacity: field.visited ? 0.8 : (field.state === 'others' ? 0.5 : 0.25), weight: field.visited ? 4 : (field.state === 'others' ? 2 : 1), dashArray: field.is_closed ? '4 4' : null }).bindPopup(popup).addTo(map);
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
