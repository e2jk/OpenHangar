(function () {
  function init() {
    var root = document.getElementById('passport-print');
    if (!root || root.dataset.ohInited) return;
    root.dataset.ohInited = '1';
    var button = root.querySelector('#passport-print-button');
    button.classList.remove('d-none');
    button.addEventListener('click', function () {
      button.disabled = true;
      var images = root.querySelectorAll('#passport-print-document img');
      Promise.all(Array.from(images).map(function (img) {
        if (img.complete) return Promise.resolve();
        return new Promise(function (resolve) {
          img.addEventListener('load', resolve, { once: true });
          img.addEventListener('error', resolve, { once: true });
        });
      })).then(function () {
        button.disabled = false;
        window.print();
      });
    });
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
