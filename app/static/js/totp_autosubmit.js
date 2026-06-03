/* Auto-submit the TOTP form when 6 digits are entered.
 * The submit button must carry data-verifying="<translated label>"
 * so the spinner label is already translated server-side.
 */
(function () {
  'use strict';

  var el = document.getElementById('totp_code');
  if (!el) return;

  el.addEventListener('input', function () {
    var digits = el.value.replace(/\D/g, '');
    if (digits.length !== 6) return;

    el.value = digits;
    el.readOnly = true;

    var form = el.closest('form');
    var btn = form && form.querySelector('[type=submit]');
    if (btn) {
      var label = btn.dataset.verifying || 'Verifying…';
      btn.disabled = true;
      btn.innerHTML =
        '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>' +
        label;
    }

    if (form) {
      if (form.requestSubmit) {
        form.requestSubmit();
      } else {
        form.submit();
      }
    }
  });
})();
