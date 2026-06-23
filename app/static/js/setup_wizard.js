(function () {
  function init() {
    /* Operating-model step — card selection + step-4 visibility */
    var wizardCards = document.querySelectorAll('.wizard-choice-card');
    if (wizardCards.length && !wizardCards[0].dataset.ohInited) {
      var complexModels = new Set(['shared_ownership', 'flight_club', 'flight_school']);
      wizardCards.forEach(function (card) { card.dataset.ohInited = '1'; });
      document.querySelectorAll('.wizard-radio').forEach(function (radio) {
        radio.addEventListener('change', function () {
          document.querySelectorAll('.wizard-choice-card').forEach(function (card) {
            card.classList.remove('selected');
          });
          if (this.checked) this.closest('.wizard-choice-card').classList.add('selected');
          var complex = complexModels.has(this.value);
          ['step-4-line', 'step-4-dot', 'step-4-label'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.classList.toggle('d-none', !complex);
          });
        });
      });
    }

    /* Co-owners step — add row */
    var addBtn = document.getElementById('add-co-owner');
    if (addBtn && !addBtn.dataset.ohInited) {
      addBtn.dataset.ohInited = '1';
      addBtn.addEventListener('click', function () {
        var rows = document.getElementById('co-owner-rows');
        var first = rows.querySelector('.co-owner-row');
        var clone = first.cloneNode(true);
        clone.querySelectorAll('input').forEach(function (inp) { inp.value = ''; });
        rows.appendChild(clone);
      });
    }
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
