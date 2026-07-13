(function () {
  /* WeakSet guard: survives hx-boost history restore (dataset attrs are
     serialised into the sessionStorage snapshot; WeakSet references are not). */
  var _mapInited = typeof WeakSet !== 'undefined' ? new WeakSet() : null;

  function init() {
    var tracksEl = document.getElementById('tracks-data');
    if (!tracksEl) return;
    if (_mapInited ? _mapInited.has(tracksEl) : tracksEl.dataset.ohInited) return;
    if (_mapInited) _mapInited.add(tracksEl); else tracksEl.dataset.ohInited = '1';

    var data   = JSON.parse(tracksEl.textContent);
    var tracks = data.tracks;
    var opiKey = data.opiKey || null;

    var mapEl    = document.getElementById('tracks-map');
    var playBtn  = document.getElementById('anim-play');
    var stopBtn  = document.getElementById('anim-stop');
    var progress = document.getElementById('anim-progress');
    var pbarWrap = document.getElementById('anim-pbar-wrap');
    if (!mapEl || !playBtn) return;

    var LABEL_ANIMATE = playBtn.dataset.labelAnimate || 'Animate';
    var LABEL_REPLAY  = playBtn.dataset.labelReplay  || 'Replay';

    var pbar = document.createElement('div');
    pbar.id  = 'anim-pbar';
    if (pbarWrap) pbarWrap.appendChild(pbar);

    var isDark = document.documentElement.getAttribute('data-bs-theme') === 'dark';
    var map = L.map(mapEl);

    if (opiKey) {
      L.tileLayer(
        isDark ? 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'
               : 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
        { maxZoom: 19, subdomains: 'abcd', attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>' }
      ).addTo(map);
      L.tileLayer(
        'https://api.tiles.openaip.net/api/data/openaip/{z}/{x}/{y}.png?apiKey=' + opiKey,
        { maxZoom: 14, attribution: '&copy; <a href="https://www.openaip.net">OpenAIP</a>' }
      ).addTo(map);
    } else {
      L.tileLayer(
        isDark ? 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'
               : 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        isDark
          ? { maxZoom: 19, subdomains: 'abcd', attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>' }
          : { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }
      ).addTo(map);
    }

    function trackOpacity(idx, total) {
      return Math.max(0.3, 0.8 - (total - 1 - idx) * 0.05);
    }

    function makeLayer(t, opacity) {
      var lyr = L.geoJSON(t.geojson, { style: { color: '#d526e0', weight: 4, opacity: opacity } });
      lyr.finalOpacity = opacity;
      lyr.bindTooltip(t.date + ' ' + t.dep + '→' + t.arr);
      lyr.on('mouseover', function () { lyr.setStyle({ color: '#3068fa', weight: 4, opacity: 1 }); });
      lyr.on('mouseout', function () { lyr.setStyle({ color: '#d526e0', weight: 4, opacity: lyr.finalOpacity }); });
      lyr.on('click', function () { window.location.href = t.view_url; });
      return lyr;
    }

    function fitAll(bounds) {
      if (!bounds.length) { map.setView([0, 0], 2); return; }
      var combined = bounds[0];
      for (var i = 1; i < bounds.length; i++) { combined.extend(bounds[i]); }
      map.fitBounds(combined, { padding: [20, 20] });
    }

    var allBounds = [], layerObjs = [];
    tracks.forEach(function (t, idx) {
      if (!t.geojson) return;
      var lyr = makeLayer(t, trackOpacity(idx, tracks.length));
      var b = lyr.getBounds();
      if (b.isValid()) allBounds.push(b);
      layerObjs.push(lyr);
    });
    fitAll(allBounds);
    layerObjs.forEach(function (l) { l.addTo(map); });

    var animTimer = null, animRaf = null, animDrawLayer = null;
    var animIdx = 0, animBounds = [], animLayers = [];
    var STEP_MS = 600, DRAW_MS = 300, PAUSE_MS = STEP_MS - DRAW_MS;

    function extractLatLngs(geojson) {
      var pts = [];
      function fromGeom(g) {
        if (!g) return;
        if (g.type === 'LineString') {
          g.coordinates.forEach(function (c) { pts.push([c[1], c[0]]); });
        } else if (g.type === 'MultiLineString') {
          g.coordinates.forEach(function (seg) { seg.forEach(function (c) { pts.push([c[1], c[0]]); }); });
        } else if (g.type === 'Feature') {
          fromGeom(g.geometry);
        } else if (g.type === 'FeatureCollection') {
          (g.features || []).forEach(fromGeom);
        }
      }
      fromGeom(geojson);
      return pts;
    }

    function animClear() {
      clearTimeout(animTimer);
      if (animRaf !== null) { cancelAnimationFrame(animRaf); animRaf = null; }
      if (animDrawLayer) { animDrawLayer.remove(); animDrawLayer = null; }
      animLayers.forEach(function (l) { l.remove(); });
      animLayers = []; animBounds = []; animIdx = 0;
      if (pbar) pbar.style.width = '0%';
    }

    function applyAnimOpacities() {
      var n = animLayers.length;
      animLayers.forEach(function (l, i) {
        var op = trackOpacity(i, n);
        l.finalOpacity = op;
        l.setStyle({ opacity: op });
      });
    }

    function animStep() {
      if (animIdx >= tracks.length) {
        playBtn.innerHTML = '<i class="bi bi-arrow-counterclockwise me-1"></i>' + LABEL_REPLAY;
        playBtn.classList.remove('d-none');
        stopBtn.classList.add('d-none');
        if (progress) progress.textContent = tracks.length + ' / ' + tracks.length;
        return;
      }
      var t = tracks[animIdx];
      animIdx++;
      if (progress) progress.textContent = animIdx + ' / ' + tracks.length;
      if (pbar) pbar.style.width = (animIdx / tracks.length * 100) + '%';

      if (!t.geojson) { animTimer = setTimeout(animStep, STEP_MS); return; }

      var pts = extractLatLngs(t.geojson);
      var trackBounds = pts.length >= 2 ? L.latLngBounds(pts) : null;
      if (trackBounds && trackBounds.isValid()) { animBounds.push(trackBounds); fitAll(animBounds); }

      if (pts.length < 2) {
        var lyrD = makeLayer(t, 1.0);
        lyrD.addTo(map); animLayers.push(lyrD); applyAnimOpacities();
        animTimer = setTimeout(animStep, STEP_MS); return;
      }

      animDrawLayer = L.polyline([pts[0]], { color: '#d526e0', weight: 4, opacity: 1.0 }).addTo(map);
      var drawStart = null;

      function drawFrame(ts) {
        if (drawStart === null) drawStart = ts;
        var frac = Math.min((ts - drawStart) / DRAW_MS, 1.0);
        animDrawLayer.setLatLngs(pts.slice(0, Math.max(2, Math.round(frac * pts.length))));
        if (frac < 1.0) {
          animRaf = requestAnimationFrame(drawFrame);
        } else {
          animRaf = null;
          var lyr = makeLayer(t, 1.0);
          lyr.addTo(map);
          animDrawLayer.remove(); animDrawLayer = null;
          animLayers.push(lyr); applyAnimOpacities();
          animTimer = setTimeout(animStep, PAUSE_MS);
        }
      }
      animRaf = requestAnimationFrame(drawFrame);
    }

    function animStart() {
      layerObjs.forEach(function (l) { l.remove(); });
      animClear();
      playBtn.classList.add('d-none');
      stopBtn.classList.remove('d-none');
      if (progress) { progress.classList.remove('d-none'); progress.textContent = '0 / ' + tracks.length; }
      if (pbarWrap) pbarWrap.classList.remove('d-none');
      var dashWrap = document.querySelector('.dash-tracks-map-wrap');
      var panel = mapEl.closest('.dash-panel') || mapEl.closest('.ac-form-card') || mapEl.parentElement;
      var mapOffsetInPanel = mapEl.getBoundingClientRect().top - panel.getBoundingClientRect().top;
      mapEl.style.height = (window.innerHeight - mapOffsetInPanel - 32) + 'px';
      if (dashWrap) dashWrap.classList.add('dash-map-animating');
      map.invalidateSize();
      panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
      var prezoomed = false;
      for (var i = 0; i < tracks.length; i++) {
        if (tracks[i].geojson) {
          var p0 = extractLatLngs(tracks[i].geojson);
          if (p0.length >= 2) {
            var b0 = L.latLngBounds(p0);
            if (b0.isValid()) { map.fitBounds(b0, { padding: [20, 20] }); prezoomed = true; }
          }
          break;
        }
      }
      if (!prezoomed) map.setView([0, 0], 2);
      animTimer = setTimeout(animStep, 700);
    }

    function animStop() {
      clearTimeout(animTimer);
      if (animRaf !== null) { cancelAnimationFrame(animRaf); animRaf = null; }
      if (animDrawLayer) { animDrawLayer.remove(); animDrawLayer = null; }
      animLayers.forEach(function (l) { l.remove(); });
      layerObjs.forEach(function (l) { l.addTo(map); });
      fitAll(allBounds);
      playBtn.innerHTML = '<i class="bi bi-play-fill me-1"></i>' + LABEL_ANIMATE;
      playBtn.classList.remove('d-none');
      stopBtn.classList.add('d-none');
      if (progress) { progress.classList.add('d-none'); }
      if (pbarWrap) pbarWrap.classList.add('d-none');
      animIdx = 0;
    }

    playBtn.addEventListener('click', animStart);
    stopBtn.addEventListener('click', animStop);

    var gifBtn = document.getElementById('gif-export-trigger-btn');
    if (gifBtn && gifBtn.dataset.gifUrl) {
      var gifBaseUrl  = gifBtn.dataset.gifUrl;
      var LABEL_EXPORTING = gifBtn.dataset.labelExporting || 'Export GIF…';
      var modalExport = document.getElementById('gif-modal-export-btn');
      var modalExportAll = document.getElementById('gif-modal-export-all-btn');
      var origHtml    = gifBtn.innerHTML;

      /* Fetches one GIF variant and triggers a browser download of the blob.
       * Returns a Promise so callers can sequence multiple downloads. */
      function fetchAndDownload(url) {
        return fetch(url)
          .then(function (r) {
            var cd = r.headers.get('Content-Disposition') || '';
            var m = cd.match(/filename="([^"]+)"/);
            var filename = m ? m[1] : 'tracks.gif';
            return r.blob().then(function (blob) { return { blob: blob, filename: filename }; });
          })
          .then(function (data) {
            var blobUrl = URL.createObjectURL(data.blob);
            var a = document.createElement('a');
            a.href = blobUrl; a.download = data.filename;
            document.body.appendChild(a); a.click(); document.body.removeChild(a);
            setTimeout(function () { URL.revokeObjectURL(blobUrl); }, 1000);
          });
      }

      function gifFetch(url) {
        gifBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status"></span>' + LABEL_EXPORTING;
        gifBtn.classList.add('disabled');
        gifBtn.setAttribute('aria-disabled', 'true');
        var restore = function () {
          gifBtn.innerHTML = origHtml;
          gifBtn.classList.remove('disabled');
          gifBtn.removeAttribute('aria-disabled');
        };
        fetchAndDownload(url).then(restore).catch(restore);
      }

      if (modalExport) {
        modalExport.addEventListener('click', function () {
          var orientEl = document.querySelector('[name="gif-orientation"]:checked');
          var qualEl   = document.querySelector('[name="gif-quality"]:checked');
          var orient   = orientEl ? orientEl.value : 'landscape';
          var qual     = qualEl   ? qualEl.value   : 'hires';
          var url = gifBaseUrl + '?orientation=' + encodeURIComponent(orient)
                               + '&quality='     + encodeURIComponent(qual);
          var modalEl = document.getElementById('gifExportModal');
          if (modalEl && typeof bootstrap !== 'undefined') {
            var bsModal = bootstrap.Modal.getInstance(modalEl);
            if (bsModal) bsModal.hide();
          }
          gifFetch(url);
        });
      }

      /* "Download all formats" — sequential (not parallel) fetch/download of
       * all four orientation x quality combinations, so the browser doesn't
       * choke on four simultaneous high-res GIF renders. */
      var GIF_ALL_VARIANTS = [
        { orientation: 'landscape', quality: 'lores' },
        { orientation: 'portrait',  quality: 'lores' },
        { orientation: 'landscape', quality: 'hires' },
        { orientation: 'portrait',  quality: 'hires' }
      ];

      if (modalExportAll) {
        var LABEL_GENERATING = modalExportAll.dataset.labelGenerating || 'Generating {i} / {n}…';
        var origAllHtml = modalExportAll.innerHTML;

        modalExportAll.addEventListener('click', function () {
          modalExportAll.classList.add('disabled');
          modalExportAll.setAttribute('aria-disabled', 'true');
          var i = 0;
          function next() {
            if (i >= GIF_ALL_VARIANTS.length) {
              modalExportAll.innerHTML = origAllHtml;
              modalExportAll.classList.remove('disabled');
              modalExportAll.removeAttribute('aria-disabled');
              return;
            }
            i += 1;
            var label = LABEL_GENERATING.replace('{i}', i).replace('{n}', GIF_ALL_VARIANTS.length);
            modalExportAll.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status"></span>' + label;
            var v = GIF_ALL_VARIANTS[i - 1];
            var url = gifBaseUrl + '?orientation=' + encodeURIComponent(v.orientation)
                                 + '&quality='     + encodeURIComponent(v.quality);
            fetchAndDownload(url).then(next).catch(next);
          }
          next();
        });
      }
    }
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
  document.addEventListener('htmx:historyRestore', init);
})();
