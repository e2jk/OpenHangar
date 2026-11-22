(function () {
  document.addEventListener('htmx:beforeSwap', function (event) {
    var xhr = event.detail.xhr;
    if (xhr && xhr.status === 400 && xhr.getResponseHeader('X-OH-Places-Form-Error') === '1') {
      event.detail.shouldSwap = true;
      event.detail.isError = false;
    }
  });

  function cleanMap(root, map) {
    function cleanup(event) {
      if (event.detail.elt === root || event.detail.elt.contains(root)) {
        map.remove();
        document.removeEventListener('htmx:beforeCleanupElement', cleanup);
      }
    }
    document.addEventListener('htmx:beforeCleanupElement', cleanup);
  }

  function initMap() {
    var root = document.getElementById('places-map');
    if (!root || root.dataset.ohInited || typeof L === 'undefined') return;
    root.dataset.ohInited = '1';
    var map = L.map('places-map-canvas', { zoomAnimation: false, fadeAnimation: false, markerZoomAnimation: false }).setView([50.5, 5], 5);
    window.ohAddBaseLayers(map, null);
    var bounds = [];
    JSON.parse(root.dataset.points).forEach(function (point) {
      var text = document.createElement(point.url ? 'a' : 'strong');
      text.textContent = point.name;
      if (point.url) text.href = point.url;
      L.circleMarker([point.latitude, point.longitude], { radius: 7, color: point.visited ? '#0d6efd' : '#6c757d', weight: point.visited ? 3 : 1, fillOpacity: point.visited ? 0.8 : 0.3 }).bindPopup(text).addTo(map);
      bounds.push([point.latitude, point.longitude]);
    });
    if (bounds.length) map.fitBounds(bounds, { padding: [25, 25], maxZoom: 13 });
    cleanMap(root, map);
  }

  function initEditor() {
    var root = document.getElementById('place-editor');
    if (!root || root.dataset.ohInited || typeof L === 'undefined') return;
    root.dataset.ohInited = '1';
    var map = L.map('place-editor-map', { zoomAnimation: false, fadeAnimation: false, markerZoomAnimation: false }).setView([50.5, 5], 5);
    window.ohAddBaseLayers(map, null);
    var lat = root.querySelector('#place-latitude');
    var lon = root.querySelector('#place-longitude');
    var pasted = root.querySelector('#place-coordinates');
    var error = root.querySelector('#place-coordinate-error');
    var marker = null;
    function setPoint(point, focus) {
      point = point.wrap();
      lat.value = point.lat.toFixed(7);
      lon.value = point.lng.toFixed(7);
      pasted.value = '';
      error.hidden = true;
      if (!marker) {
        marker = L.marker(point, { draggable: true }).addTo(map);
        marker.on('dragend', function () { setPoint(marker.getLatLng(), false); });
      } else {
        marker.setLatLng(point);
      }
      if (focus) map.setView(point, 13);
    }
    function fromInputs(clearPaste) {
      if (!lat.value.trim() || !lon.value.trim()) return;
      var latitude = Number(lat.value), longitude = Number(lon.value);
      if (!Number.isFinite(latitude) || !Number.isFinite(longitude) || Math.abs(latitude) > 90 || Math.abs(longitude) > 180) return;
      // Preserve an unparsed input on initial render after a validation error.
      var original = pasted.value;
      setPoint(L.latLng(latitude, longitude), true);
      if (!clearPaste) pasted.value = original;
    }
    fromInputs(false);
    lat.addEventListener('change', function () { fromInputs(true); });
    lon.addEventListener('change', function () { fromInputs(true); });
    map.on('click', function (event) { setPoint(event.latlng, false); });
    var use = root.querySelector('#place-use-coordinates');
    use.addEventListener('click', function () {
      var requested = pasted.value;
      var body = new URLSearchParams({ coordinates: requested });
      use.disabled = true;
      fetch(root.dataset.coordinatesUrl, { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8', 'X-CSRFToken': root.querySelector('input[name="csrf_token"]').value }, body: body.toString()
      }).then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok) {
            if (root.isConnected && pasted.value === requested) {
              error.textContent = data.error || root.dataset.coordinateError;
              error.hidden = false;
            }
            return;
          }
          if (root.isConnected && pasted.value === requested) setPoint(L.latLng(data.latitude, data.longitude), true);
        });
      }).catch(function () {
        if (root.isConnected && pasted.value === requested) {
          error.textContent = root.dataset.coordinateError;
          error.hidden = false;
        }
      }).finally(function () { use.disabled = false; });
    });
    cleanMap(root, map);
  }

  function init() { initMap(); initEditor(); }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
