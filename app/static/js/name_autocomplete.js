/* Pilot-name autocomplete for inputs with data-name-ac="<datalist-id>".
 *
 * The suggestions come from a <datalist> already rendered in the page (e.g.
 * this tenant's pilots on the flight form). The input is deliberately NOT
 * linked to it with list="…": the native datalist popup is unreliable across
 * browsers — Firefox suppresses it on autocomplete="off" inputs, Chrome only
 * opens it once you type, a pre-filled value hides every other option, and
 * mobile Safari barely shows it. This renders the same dropdown as the
 * airport autocomplete instead, identically everywhere:
 *   - focusing (or clicking) the field lists every pilot;
 *   - typing filters by any part of the name, case-insensitively;
 *   - arrow keys / Enter / Escape and mouse selection work as expected;
 *   - picking a name fires "input" and "change" so listeners (e.g. the
 *     flight form's crew-invite hint) react as if it were typed.
 */
(function () {
  'use strict';

  var MAX_ITEMS = 8;

  function initNameAc(input) {
    if (input.dataset.ohNameAcInited) return;
    var source = document.getElementById(input.dataset.nameAc);
    if (!source) return;
    input.dataset.ohNameAcInited = '1';

    var dropdown = null;
    var items = [];
    var activeIdx = -1;

    function options() {
      return Array.prototype.map.call(source.options, function (opt) { return opt.value; });
    }

    function closeDropdown() {
      if (dropdown) { dropdown.remove(); dropdown = null; }
      items = [];
      activeIdx = -1;
    }

    function setActive(idx) {
      activeIdx = idx;
      if (!dropdown) return;
      Array.prototype.forEach.call(dropdown.children, function (el, i) {
        el.classList.toggle('airport-ac-active', i === idx);
      });
    }

    function selectItem(name) {
      input.value = name;
      closeDropdown();
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function matches(query) {
      var q = query.trim().toLowerCase();
      var all = options();
      if (!q) return all.slice(0, MAX_ITEMS);
      var starts = [];
      var contains = [];
      all.forEach(function (name) {
        var n = name.toLowerCase();
        if (n === q) return;
        if (n.indexOf(q) === 0 || n.split(/\s+/).some(function (w) { return w.indexOf(q) === 0; })) {
          starts.push(name);
        } else if (n.indexOf(q) !== -1) {
          contains.push(name);
        }
      });
      return starts.concat(contains).slice(0, MAX_ITEMS);
    }

    function openFor(query) {
      closeDropdown();
      if (input.readOnly || input.disabled) return;
      var results = matches(query);
      if (!results.length) return;
      dropdown = document.createElement('ul');
      dropdown.className = 'airport-ac-list list-unstyled position-absolute bg-body border rounded shadow-sm m-0 p-1';
      dropdown.setAttribute('role', 'listbox');
      dropdown.dataset.nameAcList = input.id || '';
      items = results;
      results.forEach(function (name, i) {
        var li = document.createElement('li');
        li.className = 'airport-ac-item px-2 py-1 rounded';
        li.setAttribute('role', 'option');
        li.textContent = name;
        li.addEventListener('mousedown', function (e) { e.preventDefault(); selectItem(name); });
        li.addEventListener('mouseover', function () { setActive(i); });
        dropdown.appendChild(li);
      });
      var wrapper = input.parentElement;
      if (getComputedStyle(wrapper).position === 'static') {
        wrapper.style.position = 'relative';
      }
      wrapper.appendChild(dropdown);
    }

    // A pre-filled value (your own name as PIC) must not hide the others:
    // on focus, list everyone; filtering starts once you type.
    input.addEventListener('focus', function () { openFor(''); });
    input.addEventListener('click', function () { if (!dropdown) openFor(''); });
    input.addEventListener('input', function (e) {
      if (e.isTrusted === false && !dropdown) return;
      openFor(input.value);
    });

    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (!dropdown) { openFor(''); return; }
        setActive(Math.min(activeIdx + 1, items.length - 1));
      } else if (!dropdown) {
        return;
      } else if (e.key === 'ArrowUp') {
        e.preventDefault(); setActive(Math.max(activeIdx - 1, 0));
      } else if (e.key === 'Enter' && activeIdx >= 0) {
        e.preventDefault(); selectItem(items[activeIdx]);
      } else if (e.key === 'Escape' || e.key === 'Tab') {
        closeDropdown();
      }
    });

    input.addEventListener('blur', function () { setTimeout(closeDropdown, 150); });
  }

  function _initAll() {
    document.querySelectorAll('[data-name-ac]').forEach(initNameAc);
  }
  document.addEventListener('DOMContentLoaded', _initAll);
  document.addEventListener('htmx:afterSettle', _initAll);
  document.addEventListener('htmx:historyRestore', _initAll);
})();
