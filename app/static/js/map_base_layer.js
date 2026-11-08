/* The base map under every Leaflet map: OpenStreetMap, toned down by the
 * .oh-basemap CSS filter (base.css) so the OpenAIP airspace layer and the
 * track stand out — darkened the same way in dark mode. Free, no key.
 * (CARTO's light/dark base maps, used before, now require an API key.)
 * The server-side track images use the same tiles and toning
 * (utils._tone_down). */
(function () {
  window.ohAddBaseLayers = function (map, opiKey) {
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      className: 'oh-basemap',
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);
    if (opiKey) {
      L.tileLayer('https://api.tiles.openaip.net/api/data/openaip/{z}/{x}/{y}.png?apiKey=' + opiKey, {
        maxZoom: 14,
        attribution: '&copy; <a href="https://www.openaip.net">OpenAIP</a>'
      }).addTo(map);
    }
  };
})();
