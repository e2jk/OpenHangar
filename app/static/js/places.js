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
    JSON.parse(root.dataset.links || '[]').forEach(function (link) {
      L.polyline([link.start, link.end], { color: '#d97706', weight: 1, opacity: 0.6, dashArray: '4 4' }).addTo(map);
    });
    JSON.parse(root.dataset.airfields || '[]').forEach(function (field) {
      var popup = document.createElement('div');
      var name = document.createElement('strong');
      name.textContent = field.name;
      popup.appendChild(name);
      var list = document.createElement('ul');
      field.places.forEach(function (place) {
        var item = document.createElement('li');
        var link = document.createElement('a');
        link.textContent = place.name;
        link.href = place.url;
        item.appendChild(link);
        list.appendChild(item);
      });
      popup.appendChild(list);
      L.circleMarker([field.latitude, field.longitude], { radius: 5, color: '#d97706', weight: 2, fillOpacity: 0.5 }).bindPopup(popup).addTo(map);
      bounds.push([field.latitude, field.longitude]);
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
    var marker = null;
    function setPoint(point, focus) {
      point = point.wrap();
      lat.value = point.lat.toFixed(7);
      lon.value = point.lng.toFixed(7);
      if (!marker) {
        marker = L.marker(point, { draggable: true }).addTo(map);
        marker.on('dragend', function () { setPoint(marker.getLatLng(), false); });
      } else {
        marker.setLatLng(point);
      }
      if (focus) map.setView(point, 11);
    }
    function fromInputs() {
      if (!lat.value.trim() || !lon.value.trim()) return;
      var latitude = Number(lat.value), longitude = Number(lon.value);
      if (!Number.isFinite(latitude) || !Number.isFinite(longitude) || Math.abs(latitude) > 90 || Math.abs(longitude) > 180) return;
      setPoint(L.latLng(latitude, longitude), true);
    }
    fromInputs();
    // Where the chosen photo was taken, offered as the pin (never applied by itself).
    var photo = document.getElementById('place-photo');
    var photoHint = document.getElementById('place-photo-hint');
    if (photo && photoHint && root.dataset.photoPositionUrl) {
      photo.addEventListener('change', function () {
        var file = photo.files && photo.files[0];
        photoHint.replaceChildren();
        if (!file) return;
        var body = new FormData();
        body.append('photo', file);
        fetch(root.dataset.photoPositionUrl, { method: 'POST', credentials: 'same-origin',
          headers: { 'X-CSRFToken': root.querySelector('input[name="csrf_token"]').value }, body: body
        }).then(function (response) {
          return response.ok ? response.json() : null;
        }).then(function (data) {
          if (!data || !data.position || !root.isConnected || photo.files[0] !== file) return;
          var position = data.position;
          photoHint.textContent = root.dataset.photoPositionText
            .replace('{latitude}', position.latitude.toFixed(5))
            .replace('{longitude}', position.longitude.toFixed(5)) + ' ';
          var use = document.createElement('button');
          use.type = 'button';
          use.className = 'btn btn-sm btn-ac-ghost';
          use.textContent = root.dataset.photoPositionUse;
          use.addEventListener('click', function () {
            setPoint(L.latLng(position.latitude, position.longitude), true);
          });
          photoHint.appendChild(use);
        }).catch(function () { /* No suggestion: the form works as before. */ });
      });
    }
    lat.addEventListener('change', fromInputs);
    lon.addEventListener('change', fromInputs);
    map.on('click', function (event) { setPoint(event.latlng, false); });
    var search = root.querySelector('#place-address-search');
    if (search) {
      var query = root.querySelector('#place-address-query');
      var results = root.querySelector('#place-address-results');
      var searchError = root.querySelector('#place-address-error');
      var automaticTitle = null;
      function searchAddress() {
        var requested = query.value;
        search.disabled = true;
        searchError.hidden = true;
        results.replaceChildren();
        fetch(root.dataset.geocoderUrl, { method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8', 'X-CSRFToken': root.querySelector('input[name="csrf_token"]').value },
          body: new URLSearchParams({ query: requested }).toString()
        }).then(function (response) {
          return response.json().then(function (data) {
            if (!root.isConnected || query.value !== requested) return;
            if (!response.ok) {
              searchError.textContent = data.error || root.dataset.geocoderError;
              searchError.hidden = false;
              return;
            }
            if (data.position) {
              setPoint(L.latLng(data.position.latitude, data.position.longitude), true);
              return;
            }
            if (!data.results.length) {
              searchError.textContent = root.dataset.noResults;
              searchError.hidden = false;
            }
            data.results.forEach(function (result) {
              var choice = document.createElement('button');
              choice.type = 'button';
              choice.className = 'list-group-item list-group-item-action text-break';
              choice.setAttribute('aria-pressed', 'false');
              choice.textContent = result.name + ' — ' + result.address;
              choice.addEventListener('click', function () {
                setPoint(L.latLng(result.latitude, result.longitude), true);
                var title = root.querySelector('#place-title');
                if (!title.value.trim() || title.value === automaticTitle) {
                  automaticTitle = (result.name || result.address).slice(0, 200);
                  title.value = automaticTitle;
                }
                root.querySelector('#place-address').value = result.address.slice(0, 500);
                var previous = results.querySelector('[aria-pressed="true"]');
                if (previous) {
                  previous.classList.remove('active');
                  previous.setAttribute('aria-pressed', 'false');
                }
                choice.classList.add('active');
                choice.setAttribute('aria-pressed', 'true');
              });
              results.appendChild(choice);
            });
          });
        }).catch(function () {
          if (root.isConnected && query.value === requested) {
            searchError.textContent = root.dataset.geocoderError;
            searchError.hidden = false;
          }
        }).finally(function () { search.disabled = false; });
      }
      search.addEventListener('click', searchAddress);
      query.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') { event.preventDefault(); if (!search.disabled) searchAddress(); }
      });
    }
    cleanMap(root, map);
  }

  function init() { initMap(); initEditor(); }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
