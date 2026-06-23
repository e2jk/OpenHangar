(function () {
  function init() {
    var gatusBadgesEl = document.getElementById('gatus-badges');
    if (gatusBadgesEl && !gatusBadgesEl.dataset.ohInited) {
      gatusBadgesEl.dataset.ohInited = '1';
      var badges = gatusBadgesEl.querySelectorAll('.gatus-badge');
      var errors = 0;
      badges.forEach(function (img) {
        img.addEventListener('error', function () {
          if (++errors === badges.length) {
            gatusBadgesEl.style.display = 'none';
            document.getElementById('gatus-error').classList.remove('d-none');
          }
        });
      });
    }

    var dataEl = document.getElementById('om-descs');
    if (dataEl && !dataEl.dataset.ohInited) {
      dataEl.dataset.ohInited = '1';
      var descs = JSON.parse(dataEl.textContent);
      window._updateOmDesc = function () {
        var omSel = document.getElementById('operating_model');
        var omDesc = document.getElementById('om-desc');
        if (omSel && omDesc) omDesc.textContent = descs[omSel.value] || '';
      };
      window._handleOmChange = function () {
        var acFields = document.getElementById('aircraft-fields');
        if (acFields) acFields.classList.toggle('d-none', this.value === 'sole_pilot');
        window._updateOmDesc();
      };
      window._updateOmDesc();
    }
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
