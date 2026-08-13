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

    function isOtherAircraft() { return acSelect.value === 'other'; }
    function hasManagedAircraft() {
      // Not-yet-decided (blank dropdown) counts as "managed" too, same as
      // edit mode — only an explicit "other aircraft" pick hides the
      // crew/counters/photos sections. Keeps the new-flight form's field
      // set consistent with editing instead of hiding most of it until a
      // specific aircraft is chosen.
      return !isOtherAircraft();
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

    var fuelHint = document.getElementById('fuel-consumption-hint');
    var fuelFlow = fuelHint ? parseFloat(fuelHint.dataset.fuelFlow) : NaN;

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
    ['engine_time_counter_start', 'engine_time_counter_end'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener('input', updateFuelHint);
    });
    updateFuelHint();

    var fuelFractionGroup = document.getElementById('fuel-fraction-buttons');
    var fuelRemainingInput = document.getElementById('fuel_remaining_qty');
    if (fuelFractionGroup && fuelRemainingInput) {
      var capacity = parseFloat(fuelFractionGroup.dataset.capacity);
      if (!isNaN(capacity)) {
        fuelFractionGroup.querySelectorAll('.fuel-fraction-btn').forEach(function (btn) {
          btn.addEventListener('click', function () {
            var numerator = parseFloat(btn.dataset.numerator);
            var denominator = parseFloat(btn.dataset.denominator);
            var raw = (numerator / denominator) * capacity;
            var rounded = Math.round(raw / 5) * 5;
            fuelRemainingInput.value = rounded.toFixed(1);
            fuelRemainingInput.dispatchEvent(new Event('input'));
            fuelFractionGroup.querySelectorAll('.fuel-fraction-btn').forEach(function (b) {
              b.classList.toggle('active', b === btn);
            });
          });
        });
      }
    }

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

    // engine_time/flight_time are read-only, always recomputed here — the
    // server independently recomputes and validates the same way (see
    // flights/form_parsing.py), so this is purely a live visual aid, not
    // the source of truth. Duration mismatch tolerance mirrors the
    // server's _DURATION_MISMATCH_TOLERANCE_HOURS.
    var DURATION_MISMATCH_TOLERANCE_HOURS = 0.2;
    var mismatchGroups = {};

    function parseTimeToHours(value) {
      if (!value) return NaN;
      var parts = value.split(':');
      if (parts.length !== 2) return NaN;
      var h = parseInt(parts[0], 10);
      var m = parseInt(parts[1], 10);
      if (isNaN(h) || isNaN(m)) return NaN;
      return h + m / 60;
    }
    function clockDuration(startVal, endVal) {
      var s = parseTimeToHours(startVal);
      var e = parseTimeToHours(endVal);
      if (isNaN(s) || isNaN(e)) return NaN;
      var diff = e - s;
      if (diff < 0) diff += 24; // crossed midnight
      return diff;
    }
    function updateSubmitState() {
      var submitBtn = document.getElementById('flight-form-submit');
      if (!submitBtn) return;
      var anyMismatch = Object.keys(mismatchGroups).some(function (k) { return mismatchGroups[k]; });
      submitBtn.disabled = anyMismatch;
    }
    function wireDurationGroup(key, counterStartId, counterEndId, clockStartId, clockEndId, targetId, hintId) {
      var counterStartEl = document.getElementById(counterStartId);
      var counterEndEl = document.getElementById(counterEndId);
      var clockStartEl = document.getElementById(clockStartId);
      var clockEndEl = document.getElementById(clockEndId);
      var targetEl = document.getElementById(targetId);
      var hintEl = document.getElementById(hintId);
      if (!targetEl) return;
      var defaultHintText = hintEl ? hintEl.textContent : '';
      var mismatchHintText = hintEl ? hintEl.dataset.mismatchText : '';

      function recalc() {
        var counterDur = NaN;
        if (counterStartEl && counterEndEl) {
          var cs = parseFloat(counterStartEl.value);
          var ce = parseFloat(counterEndEl.value);
          if (!isNaN(cs) && !isNaN(ce) && ce >= cs) counterDur = ce - cs;
        }
        var clockDur = (clockStartEl && clockEndEl)
          ? clockDuration(clockStartEl.value, clockEndEl.value)
          : NaN;

        var mismatch = !isNaN(counterDur) && !isNaN(clockDur) &&
          Math.abs(counterDur - clockDur) > DURATION_MISMATCH_TOLERANCE_HOURS;
        mismatchGroups[key] = mismatch;

        if (mismatch) {
          targetEl.value = '';
          targetEl.classList.add('is-invalid');
          if (hintEl) { hintEl.textContent = mismatchHintText; hintEl.classList.add('text-danger'); }
        } else {
          var dur = !isNaN(counterDur) ? counterDur : clockDur;
          targetEl.value = !isNaN(dur) ? dur.toFixed(1) : '';
          targetEl.classList.remove('is-invalid');
          if (hintEl) { hintEl.textContent = defaultHintText; hintEl.classList.remove('text-danger'); }
        }
        updateSubmitState();
      }
      [counterStartEl, counterEndEl, clockStartEl, clockEndEl].forEach(function (el) {
        if (el) el.addEventListener('input', recalc);
      });
      recalc();
    }
    wireDurationGroup('engine', 'engine_time_counter_start', 'engine_time_counter_end',
      'departure_time', 'arrival_time', 'engine_time', 'engine-time-hint');
    wireDurationGroup('flight', 'flight_time_counter_start', 'flight_time_counter_end',
      'takeoff_time', 'landing_time', 'flight_time', 'flight-time-hint');

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
