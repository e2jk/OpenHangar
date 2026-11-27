/* Airport ICAO autocomplete for inputs with data-airport-ac="<endpoint-url>" */
(function () {
  'use strict';

  var nextListId = 0;

  function initAirportAc(input) {
    if (input.dataset.ohInited) return;
    input.dataset.ohInited = '1';
    const endpoint = input.dataset.airportAc;
    const reference = input.dataset.airportAcReference &&
      document.getElementById(input.dataset.airportAcReference);
    let dropdown = null;
    let items = [];
    let activeIdx = -1;
    let debounceId = null;
    const listId = 'airport-ac-list-' + (nextListId++);

    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', listId);

    const hint = document.createElement('div');
    hint.className = 'form-text airport-ac-hint';
    input.insertAdjacentElement('afterend', hint);

    function closeDropdown() {
      if (dropdown) { dropdown.remove(); dropdown = null; }
      items = [];
      activeIdx = -1;
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
    }

    function setActive(idx) {
      activeIdx = idx;
      if (!dropdown) return;
      Array.from(dropdown.children).forEach(function (el, i) {
        el.classList.toggle('airport-ac-active', i === idx);
      });
      if (idx >= 0 && dropdown.children[idx]) {
        input.setAttribute('aria-activedescendant', dropdown.children[idx].id);
      } else {
        input.removeAttribute('aria-activedescendant');
      }
    }

    function selectItem(item) {
      input.value = item.code;
      if (reference) reference.value = item.reference || '';
      hint.textContent = item.name;
      closeDropdown();
    }

    function buildDropdown(results) {
      closeDropdown();
      if (!results.length) return;
      dropdown = document.createElement('ul');
      dropdown.className = 'airport-ac-list list-unstyled position-absolute bg-body border rounded shadow-sm m-0 p-1';
      dropdown.id = listId;
      dropdown.setAttribute('role', 'listbox');
      items = results;
      results.forEach(function (r, i) {
        var li = document.createElement('li');
        li.id = listId + '-opt-' + i;
        li.className = 'airport-ac-item px-2 py-1 d-flex gap-2 rounded';
        li.setAttribute('role', 'option');
        // As text: the codes and names come from data files, never markup.
        var code = document.createElement('span');
        code.className = 'fw-semibold ac-code-col';
        code.textContent = r.code;
        var name = document.createElement('span');
        name.className = 'text-muted text-truncate small';
        name.textContent = r.name;
        li.append(code, name);
        li.addEventListener('mousedown', function (e) { e.preventDefault(); selectItem(r); });
        li.addEventListener('mouseover', function () { setActive(i); });
        dropdown.appendChild(li);
      });
      input.setAttribute('aria-expanded', 'true');
      var wrapper = input.parentElement;
      if (getComputedStyle(wrapper).position === 'static') {
        wrapper.style.position = 'relative';
      }
      wrapper.appendChild(dropdown);
    }

    function fetchResults(q) {
      fetch(endpoint + '?q=' + encodeURIComponent(q))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (input.value.trim() === q) buildDropdown(d.results || []);
        })
        .catch(function () { closeDropdown(); });
    }

    input.addEventListener('airport-ac:selected', function (event) {
      clearTimeout(debounceId);
      selectItem(event.detail);
    });
    input.addEventListener('input', function () {
      if (reference) reference.value = '';
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
        e.preventDefault(); selectItem(items[activeIdx]);
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

  function _initAll() {
    document.querySelectorAll('[data-airport-ac]').forEach(initAirportAc);
  }
  document.addEventListener('DOMContentLoaded', _initAll);
  document.addEventListener('htmx:afterSettle', _initAll);
  document.addEventListener('htmx:historyRestore', _initAll);
})();
