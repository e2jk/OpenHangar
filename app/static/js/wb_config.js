(function () {
  function init() {
    var container = document.getElementById('stations-container');
    if (!container || container.dataset.ohInited) return;
    container.dataset.ohInited = '1';

    var addBtn = document.getElementById('add-station');
    var fuelUnitEl = document.getElementById('fuel_unit');
    var LABEL_MAX = container.dataset.labelMax;
    var LABEL_CAP = container.dataset.labelCap;

    function getFuelUnit() { return fuelUnitEl ? fuelUnitEl.value : 'L'; }

    function updateLimitLabel(row) {
      var cb = row.querySelector('.station-fuel-cb');
      var label = row.querySelector('.station-limit-label');
      if (!label) return;
      label.textContent = (cb && cb.checked)
        ? LABEL_CAP + ' (' + getFuelUnit() + ')'
        : LABEL_MAX + ' (kg)';
    }

    function reindex() {
      container.querySelectorAll('.station-row').forEach(function (row, i) {
        var cb = row.querySelector('.station-fuel-cb');
        if (cb) cb.value = i;
      });
    }

    function addListeners(row) {
      row.querySelector('.remove-station').addEventListener('click', function () {
        if (container.querySelectorAll('.station-row').length > 1) { row.remove(); reindex(); }
      });
      var cb = row.querySelector('.station-fuel-cb');
      if (cb) cb.addEventListener('change', function () { updateLimitLabel(row); });
    }

    var envContainer = document.getElementById('env-points-container');
    var addEnvBtn = document.getElementById('add-env-point');

    function addEnvRemoveListener(row) {
      row.querySelector('.remove-env-point').addEventListener('click', function () { row.remove(); });
    }

    if (envContainer) envContainer.querySelectorAll('.env-point-row').forEach(addEnvRemoveListener);

    if (addEnvBtn) {
      addEnvBtn.addEventListener('click', function () {
        var tmpl = document.getElementById('env-point-row-template');
        var clone = tmpl.content.cloneNode(true);
        var row = clone.firstElementChild;
        envContainer.appendChild(row);
        addEnvRemoveListener(row);
      });
    }

    if (fuelUnitEl) {
      fuelUnitEl.addEventListener('change', function () {
        container.querySelectorAll('.station-row').forEach(updateLimitLabel);
      });
    }

    container.querySelectorAll('.station-row').forEach(function (row) { addListeners(row); });

    addBtn.addEventListener('click', function () {
      var idx = container.querySelectorAll('.station-row').length;
      var tmpl = document.getElementById('station-row-template');
      var clone = tmpl.content.cloneNode(true);
      var row = clone.firstElementChild;
      var cb = row.querySelector('.station-fuel-cb');
      if (cb) cb.value = idx;
      var lbl = row.querySelector('.station-limit-label');
      if (lbl) lbl.textContent = LABEL_MAX + ' (kg)';
      container.appendChild(row);
      addListeners(row);
    });
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
