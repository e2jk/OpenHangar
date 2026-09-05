(function () {
  function storageKey(aircraftId, itemId) {
    return 'oh-adsb-checklist-' + aircraftId + '-' + itemId;
  }

  function init() {
    var root = document.getElementById('adsb-checklist-root');
    if (!root || root.dataset.ohInited) return;
    root.dataset.ohInited = '1';

    var aircraftId = root.dataset.aircraftId;
    var boxes = root.querySelectorAll('.adsb-checklist-checkbox');

    boxes.forEach(function (box) {
      var key = storageKey(aircraftId, box.dataset.itemId);
      try {
        box.checked = localStorage.getItem(key) === '1';
      } catch (e) {
        /* private-browsing / storage blocked — leave unchecked */
      }
      box.addEventListener('change', function () {
        try {
          if (box.checked) {
            localStorage.setItem(key, '1');
          } else {
            localStorage.removeItem(key);
          }
        } catch (e) {
          /* ignore — nothing to persist to */
        }
      });
    });

    var copyBtn = document.getElementById('adsb-copy-checklist-btn');
    if (copyBtn) {
      copyBtn.addEventListener('click', function () {
        var lines = [];
        boxes.forEach(function (box) {
          var mark = box.checked ? '[x]' : '[ ]';
          lines.push(mark + ' ' + box.dataset.label);
        });
        var text = lines.join('\n');
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text);
        }
      });
    }
  }

  function initPrintButton() {
    var btn = document.getElementById('adsb-print-btn');
    if (!btn || btn.dataset.ohInited) return;
    btn.dataset.ohInited = '1';
    btn.addEventListener('click', function () {
      window.print();
    });
  }

  document.addEventListener('DOMContentLoaded', init);
  document.body.addEventListener('htmx:afterSettle', init);
  document.addEventListener('DOMContentLoaded', initPrintButton);
  document.body.addEventListener('htmx:afterSettle', initPrintButton);
})();
