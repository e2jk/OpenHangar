(function () {
  function init() {
    var canvas = document.getElementById('envelope-canvas');
    if (!canvas || canvas.dataset.ohInited) return;
    canvas.dataset.ohInited = '1';

    var cfg = JSON.parse(document.getElementById('wb-config-data').textContent);
    var EMPTY_W       = cfg.empty_weight;
    var EMPTY_ARM     = cfg.empty_cg_arm;
    var MTOW          = cfg.max_takeoff_weight;
    var FWD           = cfg.forward_cg_limit;
    var AFT           = cfg.aft_cg_limit;
    var ENVELOPE_POLY = cfg.envelope_points || [];
    var HAS_POLY      = ENVELOPE_POLY.length >= 3;
    var GAL_TO_L      = 3.78541;

    function inPolygon(cgX, wY, pts) {
      var inside = false, j = pts.length - 1;
      for (var i = 0; i < pts.length; i++) {
        var xi = pts[i][0], yi = pts[i][1], xj = pts[j][0], yj = pts[j][1];
        if ((yi > wY) !== (yj > wY) && cgX < (xj - xi) * (wY - yi) / (yj - yi) + xi)
          inside = !inside;
        j = i;
      }
      return inside;
    }

    function compute() {
      var totalW = EMPTY_W;
      var totalM = EMPTY_W * EMPTY_ARM;
      document.querySelectorAll('.station-weight').forEach(function (inp) {
        var arm = parseFloat(inp.dataset.arm) || 0;
        var isFuel = !!inp.dataset.isFuel;
        var w_kg;
        if (isFuel) {
          var vol = parseFloat(inp.value) || 0;
          var density = parseFloat(inp.dataset.fuelDensity) || 0.72;
          var factor = inp.dataset.fuelUnit === 'gal' ? GAL_TO_L : 1.0;
          w_kg = vol * density * factor;
          var sid = inp.id.replace('volume_', '');
          var span = document.querySelector('.fuel-kg-' + sid);
          if (span) span.textContent = w_kg.toFixed(1);
        } else {
          w_kg = parseFloat(inp.value) || 0;
        }
        totalW += w_kg;
        totalM += w_kg * arm;
      });
      var cg = totalW > 0 ? totalM / totalW : 0;
      var inEnv = HAS_POLY ? inPolygon(cg, totalW, ENVELOPE_POLY)
                           : (totalW <= MTOW && cg >= FWD && cg <= AFT);

      document.querySelectorAll('.station-weight').forEach(function (inp) {
        var max = parseFloat(inp.getAttribute('max'));
        var over = !isNaN(max) && (parseFloat(inp.value) || 0) > max;
        inp.classList.toggle('is-invalid', over);
        var hint = inp.closest('.input-group').parentElement.querySelector('.station-max-hint');
        if (hint) hint.style.color = over ? 'var(--bs-danger)' : 'var(--bs-secondary)';
      });

      document.getElementById('res-weight').textContent = totalW.toFixed(1);
      document.getElementById('res-cg').textContent = cg.toFixed(3);

      var envEl = document.getElementById('res-env');
      if (totalW === EMPTY_W) {
        envEl.textContent = '—';
        envEl.style.color = 'var(--bs-secondary)';
      } else if (inEnv) {
        envEl.textContent = envEl.dataset.labelOk;
        envEl.style.color = 'var(--bs-success)';
      } else {
        envEl.textContent = envEl.dataset.labelOut;
        envEl.style.color = 'var(--bs-danger)';
      }
      drawEnvelope(totalW, cg);
    }

    document.querySelectorAll('.station-weight').forEach(function (inp) {
      inp.addEventListener('input', compute);
    });

    function drawEnvelope(loadedW, loadedCG) {
      var dpr = window.devicePixelRatio || 1;
      var W = canvas.offsetWidth || 400;
      var H = Math.round(W * 0.55);
      canvas.width = W * dpr;
      canvas.height = H * dpr;
      canvas.style.height = H + 'px';
      var ctx = canvas.getContext('2d');
      ctx.scale(dpr, dpr);

      var PAD = { t: 16, r: 16, b: 36, l: 52 };
      var cw = W - PAD.l - PAD.r;
      var ch = H - PAD.t - PAD.b;

      var cgMin, cgMax, wMin, wMax, envMtow;
      if (HAS_POLY) {
        var cgVals = ENVELOPE_POLY.map(function (p) { return p[0]; });
        var wVals = ENVELOPE_POLY.map(function (p) { return p[1]; });
        var fwdP = Math.min.apply(null, cgVals), aftP = Math.max.apply(null, cgVals);
        envMtow = Math.max.apply(null, wVals);
        var cgMarg = (aftP - fwdP) * 0.15 || 0.02;
        cgMin = fwdP - cgMarg; cgMax = aftP + cgMarg;
        wMin = Math.min(EMPTY_W * 0.85, Math.min.apply(null, wVals) * 0.9);
        wMax = envMtow + envMtow * 0.12;
      } else {
        var cgMarg2 = (AFT - FWD) * 0.15 || 0.02;
        cgMin = FWD - cgMarg2; cgMax = AFT + cgMarg2;
        wMin = EMPTY_W * 0.85; wMax = MTOW + MTOW * 0.12; envMtow = MTOW;
      }

      function px(cg, w) {
        return [PAD.l + (cg - cgMin) / (cgMax - cgMin) * cw, PAD.t + ch - (w - wMin) / (wMax - wMin) * ch];
      }

      ctx.fillStyle = '#f8f9fa';
      ctx.fillRect(0, 0, W, H);

      ctx.beginPath();
      if (HAS_POLY) {
        var p0 = px(ENVELOPE_POLY[0][0], ENVELOPE_POLY[0][1]);
        ctx.moveTo(p0[0], p0[1]);
        for (var i = 1; i < ENVELOPE_POLY.length; i++) {
          var pi = px(ENVELOPE_POLY[i][0], ENVELOPE_POLY[i][1]);
          ctx.lineTo(pi[0], pi[1]);
        }
      } else {
        var corners = [px(FWD, EMPTY_W), px(AFT, EMPTY_W), px(AFT, MTOW), px(FWD, MTOW)];
        ctx.moveTo(corners[0][0], corners[0][1]);
        corners.slice(1).forEach(function (c) { ctx.lineTo(c[0], c[1]); });
      }
      ctx.closePath();
      ctx.fillStyle = 'rgba(25,135,84,0.12)';
      ctx.strokeStyle = 'rgba(25,135,84,0.7)';
      ctx.lineWidth = 1.5;
      ctx.fill(); ctx.stroke();

      var mtowY = px(cgMin, envMtow)[1];
      ctx.beginPath();
      ctx.moveTo(PAD.l, mtowY); ctx.lineTo(W - PAD.r, mtowY);
      ctx.strokeStyle = 'rgba(220,53,69,0.4)';
      ctx.setLineDash([4, 3]); ctx.lineWidth = 1; ctx.stroke(); ctx.setLineDash([]);

      ctx.strokeStyle = '#adb5bd'; ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(PAD.l, PAD.t); ctx.lineTo(PAD.l, PAD.t + ch); ctx.lineTo(PAD.l + cw, PAD.t + ch);
      ctx.stroke();

      ctx.fillStyle = '#6c757d'; ctx.font = '10px system-ui,sans-serif';
      ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
      for (var wi = 0; wi <= 5; wi++) {
        var wv = wMin + (wMax - wMin) * wi / 5;
        var wy = PAD.t + ch - (wv - wMin) / (wMax - wMin) * ch;
        ctx.fillText(Math.round(wv), PAD.l - 4, wy);
        ctx.beginPath(); ctx.moveTo(PAD.l - 2, wy); ctx.lineTo(PAD.l, wy); ctx.stroke();
      }
      ctx.textAlign = 'center'; ctx.textBaseline = 'top';
      for (var ci = 0; ci <= 4; ci++) {
        var cgv = cgMin + (cgMax - cgMin) * ci / 4;
        var cx2 = PAD.l + (cgv - cgMin) / (cgMax - cgMin) * cw;
        ctx.fillText(cgv.toFixed(2), cx2, PAD.t + ch + 4);
        ctx.beginPath(); ctx.moveTo(cx2, PAD.t + ch); ctx.lineTo(cx2, PAD.t + ch + 2); ctx.stroke();
      }

      ctx.fillStyle = '#6c757d'; ctx.font = '9px system-ui,sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
      ctx.fillText(canvas.dataset.labelCg, PAD.l + cw / 2, H);
      ctx.save();
      ctx.translate(10, PAD.t + ch / 2); ctx.rotate(-Math.PI / 2);
      ctx.textBaseline = 'middle';
      ctx.fillText(canvas.dataset.labelWeight, 0, 0);
      ctx.restore();

      if (loadedW > EMPTY_W) {
        var pt = px(loadedCG, loadedW);
        var inE = HAS_POLY ? inPolygon(loadedCG, loadedW, ENVELOPE_POLY)
                           : (loadedW <= MTOW && loadedCG >= FWD && loadedCG <= AFT);
        ctx.beginPath();
        ctx.arc(pt[0], pt[1], 6, 0, Math.PI * 2);
        ctx.fillStyle = inE ? 'rgba(25,135,84,0.9)' : 'rgba(220,53,69,0.9)';
        ctx.fill();
        ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke();
      }
    }

    compute();

    if (!document.documentElement.dataset.ohWbResizeBound) {
      document.documentElement.dataset.ohWbResizeBound = '1';
      window.addEventListener('resize', function () { if (window._ohWbCompute) window._ohWbCompute(); });
    }
    window._ohWbCompute = compute;
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
