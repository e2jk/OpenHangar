/* Aircraft type ICAO autocomplete for inputs with data-aircraft-type-ac="<endpoint-url>"
 *
 * When a variant is selected:
 *   - The text input receives the full descriptive name (e.g. "PIPER PA-28-161 Warrior 3")
 *   - The hidden aircraft_type_icao input receives the ICAO code (e.g. "P28A")
 *   - The hint div below the input shows the ICAO code for confirmation
 *
 * In edit mode (page load with pre-filled values):
 *   - The text input already holds the stored name
 *   - The hint shows the stored ICAO code from the hidden input
 */
(function () {
  'use strict';

  function initAircraftTypeAc(input) {
    const endpoint = input.dataset.aircraftTypeAc;
    const icaoInput = input.closest('form') && input.closest('form').querySelector('[name="aircraft_type_icao"]');
    let dropdown = null;
    let items = [];
    let activeIdx = -1;
    let debounceId = null;

    const hint = document.createElement('div');
    hint.className = 'form-text aircraft-type-ac-hint';
    input.insertAdjacentElement('afterend', hint);

    function closeDropdown() {
      if (dropdown) { dropdown.remove(); dropdown = null; }
      items = [];
      activeIdx = -1;
    }

    function setActive(idx) {
      activeIdx = idx;
      if (!dropdown) return;
      Array.from(dropdown.children).forEach(function (el, i) {
        el.classList.toggle('aircraft-type-ac-active', i === idx);
      });
    }

    function selectItem(code, name) {
      input.value = name;      // store full descriptive name in the text field
      hint.textContent = code; // show ICAO code as confirmation below
      if (icaoInput) { icaoInput.value = code; }
      closeDropdown();
    }

    function buildDropdown(results) {
      closeDropdown();
      if (!results.length) return;
      dropdown = document.createElement('ul');
      dropdown.className = 'aircraft-type-ac-list list-unstyled position-absolute bg-white border rounded shadow-sm m-0 p-1';
      items = results;
      results.forEach(function (r, i) {
        var li = document.createElement('li');
        li.className = 'aircraft-type-ac-item px-2 py-1 d-flex gap-2 rounded';
        li.innerHTML =
          '<span class="fw-semibold" style="min-width:3.2em">' + r.code + '</span>' +
          '<span class="text-muted text-truncate small">' + r.name + '</span>';
        li.addEventListener('mousedown', function (e) { e.preventDefault(); selectItem(r.code, r.name); });
        li.addEventListener('mouseover', function () { setActive(i); });
        dropdown.appendChild(li);
      });
      var wrapper = input.parentElement;
      if (getComputedStyle(wrapper).position === 'static') {
        wrapper.style.position = 'relative';
      }
      wrapper.appendChild(dropdown);
    }

    function fetchResults(q) {
      fetch(endpoint + '?q=' + encodeURIComponent(q))
        .then(function (r) { return r.json(); })
        .then(function (d) { buildDropdown(d.results || []); })
        .catch(function () { closeDropdown(); });
    }

    input.addEventListener('input', function () {
      var q = input.value.trim();
      hint.textContent = '';
      if (icaoInput) { icaoInput.value = ''; }
      clearTimeout(debounceId);
      if (q.length < 2) { closeDropdown(); return; }
      debounceId = setTimeout(function () { fetchResults(q); }, 200);
    });

    input.addEventListener('keydown', function (e) {
      if (!dropdown) return;
      if (e.key === 'ArrowDown') {
        e.preventDefault(); setActive(Math.min(activeIdx + 1, items.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault(); setActive(Math.max(activeIdx - 1, 0));
      } else if (e.key === 'Enter' && activeIdx >= 0) {
        e.preventDefault(); selectItem(items[activeIdx].code, items[activeIdx].name);
      } else if (e.key === 'Escape') {
        closeDropdown();
      }
    });

    input.addEventListener('blur', function () { setTimeout(closeDropdown, 150); });

    /* Edit mode: show the stored ICAO code as hint below the pre-filled name */
    var initialCode = icaoInput ? icaoInput.value.trim().toUpperCase() : '';
    if (initialCode.length >= 2) {
      hint.textContent = initialCode;
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-aircraft-type-ac]').forEach(initAircraftTypeAc);
  });
})();
