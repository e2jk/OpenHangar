(function () {
  function init() {
    var acSelector = document.getElementById('aircraft-selector');
    if (!acSelector || acSelector.dataset.ohInited) return;
    acSelector.dataset.ohInited = '1';
    var modeRadios = document.querySelectorAll('input[name="mode"]');
    function updateVisibility() {
      var selected = document.querySelector('input[name="mode"]:checked');
      acSelector.classList.toggle('d-none', !selected || selected.value !== 'one_aircraft');
    }
    modeRadios.forEach(function (r) { r.addEventListener('change', updateVisibility); });
    updateVisibility();
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
