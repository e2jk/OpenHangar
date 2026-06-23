(function () {
  function init() {
    var rows = document.getElementById('invite-rows');
    if (!rows || rows.dataset.ohInited) return;
    rows.dataset.ohInited = '1';

    var ownerRoles = ['owner', 'admin'];

    function updateAircraftSection(row) {
      var sel = row.querySelector('.invite-role-select');
      var sec = row.querySelector('.invite-aircraft-section');
      if (sec) sec.style.display = ownerRoles.includes(sel.value) ? 'none' : '';
    }

    function syncAircraftHidden(row) {
      var cbs = row.querySelectorAll('.aircraft-cb:checked');
      var ids = Array.from(cbs).map(function (cb) { return cb.dataset.acid; });
      var hidden = row.querySelector('.aircraft-ids-hidden');
      if (hidden) hidden.value = ids.join(',');
    }

    function initRow(row) {
      var sel = row.querySelector('.invite-role-select');
      if (sel) sel.addEventListener('change', function () { updateAircraftSection(row); });
      row.querySelectorAll('.aircraft-cb').forEach(function (cb) {
        cb.addEventListener('change', function () { syncAircraftHidden(row); });
      });
      updateAircraftSection(row);
    }

    rows.querySelectorAll('.invite-row').forEach(initRow);

    var addBtn = document.getElementById('add-invite-row');
    if (addBtn) {
      addBtn.addEventListener('click', function () {
        var first = rows.querySelector('.invite-row');
        var clone = first.cloneNode(true);
        clone.querySelectorAll('input[type=text],input[type=email],input[type=hidden]').forEach(function (inp) { inp.value = ''; });
        clone.querySelectorAll('input[type=checkbox]').forEach(function (cb) { cb.checked = false; });
        clone.querySelectorAll('.invite-role-select').forEach(function (s) { s.selectedIndex = 0; });
        rows.appendChild(clone);
        initRow(clone);
      });
    }
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
