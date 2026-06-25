/* Auto-submit the TOTP form when 6 digits are entered.
 * The submit button must carry data-verifying="<translated label>"
 * so the spinner label is already translated server-side.
 *
 * Both 'input' and 'change' are listened to because mobile keyboards and
 * OS-level OTP auto-fill (Android SMS suggestion, iOS one-time-code) can
 * fill the field and fire 'change' without firing 'input'. The readOnly
 * guard prevents a double-submit if both events fire for the same change.
 */
(function () {
  'use strict';

  var el = document.getElementById('totp_code');
  if (!el) return;

  function trySubmit() {
    if (el.readOnly) return;
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
  }

  el.addEventListener('input', trySubmit);
  el.addEventListener('change', trySubmit);
})();
