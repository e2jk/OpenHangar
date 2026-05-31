/* Airport ICAO autocomplete for inputs with data-airport-ac="<endpoint-url>" */
(function () {
  'use strict';

  function initAirportAc(input) {
    const endpoint = input.dataset.airportAc;
    let dropdown = null;
    let items = [];
    let activeIdx = -1;
    let debounceId = null;

    const hint = document.createElement('div');
    hint.className = 'form-text airport-ac-hint';
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
        el.classList.toggle('airport-ac-active', i === idx);
      });
    }

    function selectItem(code, name) {
      input.value = code;
      hint.textContent = name;
      closeDropdown();
    }

    function buildDropdown(results) {
      closeDropdown();
      if (!results.length) return;
      dropdown = document.createElement('ul');
      dropdown.className = 'airport-ac-list list-unstyled position-absolute bg-white border rounded shadow-sm m-0 p-1';
      items = results;
      results.forEach(function (r, i) {
        var li = document.createElement('li');
        li.className = 'airport-ac-item px-2 py-1 d-flex gap-2 rounded';
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

    /* Edit mode: show name for pre-filled value */
    var initial = input.value.trim().toUpperCase();
    if (initial.length >= 2) {
      fetch(endpoint + '?q=' + encodeURIComponent(initial))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var match = (d.results || []).find(function (r) { return r.code === initial; });
          if (match) { hint.textContent = match.name; }
        })
        .catch(function () {});
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-airport-ac]').forEach(initAirportAc);
  });
})();
