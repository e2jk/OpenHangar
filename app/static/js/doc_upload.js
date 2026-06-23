(function () {
  function init() {
    var zone = document.getElementById('drop-zone');
    if (!zone || zone.dataset.ohInited) return;
    zone.dataset.ohInited = '1';
    var input = document.getElementById('file');
    var nameEl = document.getElementById('file-name');
    var previewEl = document.getElementById('file-preview');

    zone.addEventListener('click', function () { input.click(); });
    zone.addEventListener('dragover', function (e) { e.preventDefault(); zone.classList.add('dragover'); });
    zone.addEventListener('dragleave', function () { zone.classList.remove('dragover'); });
    zone.addEventListener('drop', function (e) {
      e.preventDefault();
      zone.classList.remove('dragover');
      if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; showFile(e.dataTransfer.files[0]); }
    });
    input.addEventListener('change', function () { if (input.files.length) showFile(input.files[0]); });

    function showFile(file) {
      nameEl.textContent = file.name + ' (' + formatSize(file.size) + ')';
      previewEl.innerHTML = '';
      if (file.type.startsWith('image/')) {
        var img = document.createElement('img');
        img.src = URL.createObjectURL(file);
        previewEl.appendChild(img);
      }
    }

    function formatSize(bytes) {
      if (bytes < 1024) return bytes + ' B';
      if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
      return (bytes / 1048576).toFixed(1) + ' MB';
    }
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
