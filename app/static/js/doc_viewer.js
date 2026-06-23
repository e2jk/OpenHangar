(function () {
  function init() {
    var modal = document.getElementById('docModal');
    if (!modal || modal.dataset.ohInited) return;
    modal.dataset.ohInited = '1';
    modal.addEventListener('show.bs.modal', function (e) {
      var btn = e.relatedTarget;
      var url = btn.getAttribute('data-url');
      var mime = btn.getAttribute('data-mime') || '';
      var title = btn.getAttribute('data-title') || '';
      document.getElementById('docModalLabel').textContent = title;
      var body = document.getElementById('docModalBody');
      body.innerHTML = '';
      if (mime.startsWith('image/')) {
        var img = document.createElement('img');
        img.src = url;
        img.className = 'img-fluid d-block mx-auto p-2';
        img.style.maxHeight = '80vh';
        body.appendChild(img);
      } else {
        var iframe = document.createElement('iframe');
        iframe.src = url;
        iframe.style.width = '100%';
        iframe.style.height = '80vh';
        iframe.style.border = 'none';
        body.appendChild(iframe);
      }
    });
    modal.addEventListener('hidden.bs.modal', function () {
      document.getElementById('docModalBody').innerHTML = '';
    });
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
