(function () {
  function init() {
    var btn = document.getElementById('minimums-print-btn');
    if (!btn || btn.dataset.ohInited) return;
    btn.dataset.ohInited = '1';
    btn.addEventListener('click', function () {
      window.print();
    });
  }

  document.addEventListener('DOMContentLoaded', init);
  document.body.addEventListener('htmx:afterSettle', init);
})();
