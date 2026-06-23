(function () {
  function init() {
    var makeEl = document.getElementById('make');
    if (!makeEl || makeEl.dataset.ohInited) return;
    makeEl.dataset.ohInited = '1';

    document.addEventListener('aircraft-type-selected', function (e) {
      var mk = document.getElementById('make');
      var mo = document.getElementById('model');
      if (mk && e.detail.manufacturer) mk.value = e.detail.manufacturer;
      if (mo && e.detail.model) mo.value = e.detail.model;
    });

    var cb = document.getElementById('has_flight_counter');
    var row = document.getElementById('flight_counter_offset_row');
    if (cb && row) {
      cb.addEventListener('change', function () { row.classList.toggle('d-none', cb.checked); });
    }

    var sel = document.getElementById('fuel_type');
    var hint = document.getElementById('fuel_type_hint');
    if (sel && hint) {
      function updateFuelHint() {
        var opt = sel.options[sel.selectedIndex];
        var density = opt ? opt.dataset.density : '';
        hint.textContent = density
          ? hint.dataset.withDensity.replace('__DENSITY__', density)
          : hint.dataset.plain;
      }
      sel.addEventListener('change', updateFuelHint);
      updateFuelHint();
    }
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
