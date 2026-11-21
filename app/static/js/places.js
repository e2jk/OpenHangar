(function () {
  // A validation error contains the form and an explanation. Display it after
  // boosted navigation just as a full-page form submission would.
  document.addEventListener('htmx:beforeSwap', function (event) {
    var xhr = event.detail.xhr;
    if (xhr && xhr.status === 400 && xhr.getResponseHeader('X-OH-Places-Form-Error') === '1') {
      event.detail.shouldSwap = true;
      event.detail.isError = false;
    }
  });
})();
