(function () {
    function init() {
        var form = document.getElementById('share-confirm-form');
        if (!form || form.dataset.ohInited) return;
        form.dataset.ohInited = '1';

        var radios = form.querySelectorAll('input[name="destination"]');
        var aircraftRow = document.getElementById('share-aircraft-row');
        var docFields = document.getElementById('share-doc-fields');

        function update() {
            var checked = form.querySelector('input[name="destination"]:checked');
            var dest = checked ? checked.value : '';
            var needsAircraft = dest === 'document' || dest === 'expense' || dest === 'maintenance';
            if (aircraftRow) aircraftRow.style.display = needsAircraft ? '' : 'none';
            if (docFields) docFields.style.display = dest === 'document' ? '' : 'none';
        }

        radios.forEach(function (r) { r.addEventListener('change', update); });
        update();
    }

    document.addEventListener('DOMContentLoaded', init);
    document.addEventListener('htmx:afterSettle', init);
})();
