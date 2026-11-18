(function () {
  function init() {
    var el = document.getElementById('passport-invitation');
    if (!el || el.dataset.ohInited) return;
    el.dataset.ohInited = '1';
    var button = el.querySelector('[data-copy-invitation]');
    var input = el.querySelector('input');
    button.addEventListener('click', function () {
      input.focus();
      input.select();
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(input.value).then(function () {
          button.textContent = button.dataset.copied;
        }).catch(function () { input.select(); });
      }
    });
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
