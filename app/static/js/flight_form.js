(function () {
  function init() {
    var acSelect = document.getElementById('aircraft_id');
    if (!acSelect || acSelect.dataset.ohFlightFormInited) return;
    acSelect.dataset.ohFlightFormInited = '1';

    var form = document.getElementById('flight-form');
    var editMode = !!(form && form.dataset.editMode === 'true');
    var pilotNameHint = form ? (form.dataset.pilotNameHint || '') : '';
    var otherHidden = document.getElementById('other_aircraft_hidden');
    var otherFields = document.getElementById('other-aircraft-fields');
    var otherWarning = document.getElementById('other-aircraft-warning');
    var acLogSection = document.getElementById('aircraft-log-section');
    var pilotLogSection = document.getElementById('pilot-log-section');
    var pilotRoleNoneOpt = document.getElementById('pilot-role-none-option');
    var crewSection = document.getElementById('crew-section');
    var detachSection = document.getElementById('detach-pilot-section');

    function isOtherAircraft() { return acSelect.value === 'other'; }
    function hasManagedAircraft() {
      if (editMode) return true;
      if (isOtherAircraft()) return false;
      return acSelect.value !== '';
    }
    function getPilotRole() {
      var checked = document.querySelector('input[name="pilot_role"]:checked');
      return checked ? checked.value : 'none';
    }
    function updateVisibility() {
      var other = isOtherAircraft();
      var managed = hasManagedAircraft();
      var role = getPilotRole();
      if (otherFields) otherFields.classList.toggle('d-none', !other);
      if (otherWarning) otherWarning.classList.toggle('d-none', !other);
      if (otherHidden) otherHidden.value = other ? '1' : '0';
      if (acLogSection) acLogSection.classList.toggle('d-none', !managed);
      if (crewSection) crewSection.classList.toggle('d-none', !managed);
      if (pilotRoleNoneOpt) pilotRoleNoneOpt.classList.toggle('d-none', other);
      if (other && getPilotRole() === 'none') {
        var picRadio = document.getElementById('pilot_role_pic');
        if (picRadio) picRadio.checked = true;
      }
      if (pilotLogSection) pilotLogSection.classList.toggle('d-none', !(role === 'pic' || role === 'dual'));
      if (detachSection) detachSection.classList.toggle('d-none', role !== 'none');
      var picNameRow = document.getElementById('pic-name-row');
      if (picNameRow) picNameRow.classList.toggle('d-none', role !== 'dual');
    }

    acSelect.addEventListener('change', updateVisibility);
    document.querySelectorAll('input[name="pilot_role"]').forEach(function (r) { r.addEventListener('change', updateVisibility); });
    updateVisibility();

    if (!editMode && pilotNameHint) {
      var crewName0 = document.getElementById('crew_name_0');
      var crewName1 = document.getElementById('crew_name_1');
      var crewRole1 = document.getElementById('crew_role_1');
      function applyRoleHint() {
        var role = getPilotRole();
        if (role === 'pic') {
          if (crewName0 && !crewName0.value) crewName0.value = pilotNameHint;
          if (crewName1 && crewName1.value === pilotNameHint) { crewName1.value = ''; if (crewRole1) crewRole1.value = 'COPILOT'; }
        } else if (role === 'dual') {
          if (crewName0 && crewName0.value === pilotNameHint) crewName0.value = '';
          if (crewName1 && !crewName1.value) crewName1.value = pilotNameHint;
          if (crewRole1) crewRole1.value = 'STUDENT';
        }
      }
      document.querySelectorAll('input[name="pilot_role"]').forEach(function (r) { r.addEventListener('change', applyRoleHint); });
    }

    var fuelAddedFields = document.getElementById('fuel-added-fields');
    var fuelHint = document.getElementById('fuel-consumption-hint');
    var fuelFlow = fuelHint ? parseFloat(fuelHint.dataset.fuelFlow) : NaN;
    var fuelRadios = document.querySelectorAll('input[name="fuel_event"]');

    function updateFuelHint() {
      if (!fuelHint || isNaN(fuelFlow)) return;
      var eStart = parseFloat((document.getElementById('engine_time_counter_start') || {}).value);
      var eEnd = parseFloat((document.getElementById('engine_time_counter_end') || {}).value);
      if (!isNaN(eStart) && !isNaN(eEnd) && eEnd > eStart) {
        fuelHint.textContent = fuelHint.dataset.template.replace('__EST__', ((eEnd - eStart) * fuelFlow).toFixed(1));
      } else {
        fuelHint.textContent = '';
      }
    }
    function updateFuelUI() {
      var checked = document.querySelector('input[name="fuel_event"]:checked');
      var val = checked ? checked.value : '';
      if (fuelAddedFields) fuelAddedFields.classList.toggle('d-none', !(val === 'before' || val === 'after'));
      updateFuelHint();
    }
    fuelRadios.forEach(function (r) { r.addEventListener('change', updateFuelUI); });
    ['engine_time_counter_start', 'engine_time_counter_end'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener('input', updateFuelHint);
    });
    updateFuelUI();

    var counterWarn = document.getElementById('counter-warn');
    if (counterWarn) {
      ['flight_time_counter_start', 'engine_time_counter_start'].forEach(function (id) {
        var el = document.getElementById(id);
        if (!el || !el.dataset.expected) return;
        el.addEventListener('input', function () {
          var differs = ['flight_time_counter_start', 'engine_time_counter_start'].some(function (i) {
            var inp = document.getElementById(i);
            return inp && inp.dataset.expected && inp.value !== '' && inp.value !== inp.dataset.expected;
          });
          counterWarn.classList.toggle('d-none', !differs);
        });
      });
    }

    var regInput = document.getElementById('other_ac_reg');
    var typeInput = document.getElementById('other_ac_make_model');
    var icaoInput = document.querySelector('[name="aircraft_type_icao"]');
    if (regInput && typeInput) {
      var lookupUrl = regInput.dataset.regLookupUrl;
      var debounceId = null;
      regInput.addEventListener('input', function () {
        var q = regInput.value.trim();
        clearTimeout(debounceId);
        if (q.length < 2) return;
        debounceId = setTimeout(function () {
          fetch(lookupUrl + '?q=' + encodeURIComponent(q))
            .then(function (r) { return r.json(); })
            .then(function (data) {
              if (data.result && !typeInput.value.trim()) {
                typeInput.value = data.result.aircraft_type || '';
                if (icaoInput) icaoInput.value = data.result.aircraft_type_icao || '';
                typeInput.dispatchEvent(new Event('input'));
              }
            })
            .catch(function () {});
        }, 300);
      });
    }
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
