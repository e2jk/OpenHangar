(function () {
  function init() {
    var modal = document.getElementById('photoViewModal');
    if (modal && !modal.dataset.ohInited) {
      modal.dataset.ohInited = '1';
      modal.addEventListener('show.bs.modal', function (e) {
        var t = e.relatedTarget;
        document.getElementById('photoViewImg').src = t.dataset.imgSrc;
        document.getElementById('photoViewImg').alt = t.dataset.imgAlt;
        document.getElementById('photoViewCaption').textContent = t.dataset.imgAlt;
      });
    }

    var fileInput = document.querySelector('input[name="photos"]');
    var submitBtn = document.getElementById('photo-upload-submit');
    if (fileInput && submitBtn && !fileInput.dataset.ohInited) {
      fileInput.dataset.ohInited = '1';
      fileInput.addEventListener('change', function () {
        submitBtn.disabled = fileInput.files.length === 0;
      });
    }

    var gallery = document.getElementById('photo-gallery');
    if (!gallery || gallery.dataset.ohInited) return;
    gallery.dataset.ohInited = '1';
    var reorderUrl = gallery.dataset.reorderUrl;
    var coverLabel = gallery.dataset.coverLabel;
    var dragged = null;

    gallery.querySelectorAll('.photo-thumb').forEach(function (thumb) {
      thumb.addEventListener('dragstart', function () { dragged = thumb; thumb.style.opacity = '0.4'; });
      thumb.addEventListener('dragend', function () {
        thumb.style.opacity = '';
        dragged = null;
        var ids = Array.from(gallery.querySelectorAll('.photo-thumb')).map(function (t) { return t.dataset.photoId; });
        var fd = new FormData();
        ids.forEach(function (id) { fd.append('photo_order[]', id); });
        fetch(reorderUrl, { method: 'POST', body: fd });
      });
      thumb.addEventListener('dragover', function (e) {
        e.preventDefault();
        if (!dragged || dragged === thumb) return;
        var rect = thumb.getBoundingClientRect();
        gallery.insertBefore(dragged, e.clientX < rect.left + rect.width / 2 ? thumb : thumb.nextSibling);
        gallery.querySelectorAll('.photo-thumb').forEach(function (t, i) {
          var badge = t.querySelector('.badge.bg-primary');
          if (i === 0) {
            if (!badge) {
              badge = document.createElement('span');
              badge.className = 'position-absolute top-0 start-0 badge bg-primary oh-fs-065 m-1';
              badge.textContent = coverLabel;
              t.appendChild(badge);
            }
          } else if (badge) { badge.remove(); }
        });
      });
    });
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
