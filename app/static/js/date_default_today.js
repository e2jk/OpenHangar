/* A date field that defaults to today (data-oh-default-today) gets the
 * browser's own today when the form opens. Blank new-data forms can be
 * shown from a copy saved on the device (sw.js), possibly on another day:
 * the server's "today" baked into that copy would be stale. The device's
 * local date is also what the user means by "today". */
(function () {
  function localToday() {
    var d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') +
      '-' + String(d.getDate()).padStart(2, '0');
  }
  function init() {
    document.querySelectorAll('input[type="date"][data-oh-default-today]').forEach(function (input) {
      if (input.dataset.ohInited) return;
      input.dataset.ohInited = '1';
      input.value = localToday();
    });
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
