// Suggestions from a chosen counter/fuel photo (Phase 46): when it was taken
// (UTC) and the airfield it was taken at, read by the server and discarded.
// Shown under the photo input with "Use" buttons; nothing is filled in by
// itself. A post-flight photo suggests the arrival, a pre-flight one the
// departure.
(function () {
  var SLOTS = {
    flight_counter_photo: 'arrival',
    engine_counter_photo: 'arrival',
    fuel_photo: 'arrival',
    flight_counter_photo_preflight: 'departure',
    engine_counter_photo_preflight: 'departure'
  };

  function init() {
    var root = document.getElementById('photo-hints');
    if (!root || root.dataset.ohInited) return;
    root.dataset.ohInited = '1';
    var form = document.getElementById('flight-form');
    if (!form) return;
    var labels = JSON.parse(root.dataset.labels);

    function fill(text, values) {
      return text.replace(/\{(\w+)\}/g, function (_m, key) { return values[key] || ''; });
    }

    function button(text, target, value) {
      var el = document.createElement('button');
      el.type = 'button';
      el.className = 'btn btn-sm btn-ac-ghost ms-1';
      el.textContent = text;
      el.addEventListener('click', function () {
        var field = form.querySelector('#' + target);
        if (!field) return;
        field.value = value;
        field.dispatchEvent(new Event('input', { bubbles: true }));
        field.dispatchEvent(new Event('change', { bubbles: true }));
      });
      return el;
    }

    function show(input, phase, data) {
      var box = input.parentNode.querySelector('.oh-photo-hint');
      if (!box) {
        box = document.createElement('div');
        box.className = 'oh-photo-hint form-text';
        input.insertAdjacentElement('afterend', box);
      }
      box.replaceChildren();
      if (data.time) {
        var line = document.createElement('div');
        var key = data.time_source === 'camera' ? 'taken' : 'taken_about';
        line.textContent = fill(labels[key], { time: data.time, date: data.date });
        line.appendChild(button(labels['use_' + phase + '_time'], phase + '_time', data.time));
        box.appendChild(line);
      }
      if (data.airfield) {
        var where = document.createElement('div');
        where.textContent = fill(labels.taken_at, {
          ident: data.airfield.ident,
          name: data.airfield.name,
          km: String(data.airfield.distance_km)
        });
        where.appendChild(button(labels['use_' + phase + '_airfield'], phase + '_icao', data.airfield.ident));
        box.appendChild(where);
      }
    }

    Object.keys(SLOTS).forEach(function (name) {
      var input = form.querySelector('input[type="file"][name="' + name + '"]');
      if (!input) return;
      input.addEventListener('change', function () {
        var file = input.files && input.files[0];
        if (!file) return;
        var body = new FormData();
        body.append('photo', file);
        body.append('offset', String(-new Date().getTimezoneOffset()));
        var token = form.querySelector('input[name="csrf_token"]') ||
          document.querySelector('meta[name="csrf-token"]');
        fetch(root.dataset.url, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'X-CSRFToken': token ? (token.value || token.content) : '' },
          body: body
        }).then(function (response) {
          return response.ok ? response.json() : null;
        }).then(function (data) {
          if (data && input.isConnected && input.files[0] === file) show(input, SLOTS[name], data);
        }).catch(function () { /* No suggestion: the form works as before. */ });
      });
    });
  }

  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
