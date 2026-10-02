// Slot picker: keeps the selection to consecutive hours and updates the summary.
(function () {
  var form = document.getElementById("slot-form");
  var dataEl = document.getElementById("slot-data");
  if (!form || !dataEl) return;
  var data = JSON.parse(dataEl.textContent);
  var boxes = Array.prototype.slice.call(form.querySelectorAll('input[name="hours"]'));
  var btn = document.getElementById("continue-btn");
  var peso = function (n) {
    return "₱" + Number(n).toLocaleString("en-PH", { maximumFractionDigits: 0 });
  };

  function selectedHours() {
    return boxes.filter(function (b) { return b.checked; })
      .map(function (b) { return parseInt(b.value, 10); })
      .sort(function (a, b) { return a - b; });
  }

  function refresh() {
    var hours = selectedHours();
    var time = document.getElementById("sum-time");
    var dur = document.getElementById("sum-dur");
    var total = document.getElementById("sum-total");
    var calc = document.getElementById("sum-calc");
    var hint = document.getElementById("slot-hint");
    if (!hours.length) {
      time.textContent = "Pick slots";
      dur.textContent = "0 hours";
      total.textContent = "₱0";
      calc.textContent = "Before discounts";
      btn.disabled = true;
      hint.textContent = "Pick one or more slots next to each other.";
      return;
    }
    var start = hours[0], end = hours[hours.length - 1] + 1;
    var sum = hours.reduce(function (acc, h) { return acc + parseFloat(data.prices[h] || 0); }, 0);
    time.textContent = data.labels[start] + " to " + data.labels[end];
    dur.textContent = hours.length + (hours.length === 1 ? " hour" : " hours");
    total.textContent = peso(sum);
    calc.textContent = hours.length + " hr" + (hours.length === 1 ? "" : "s");
    btn.disabled = false;
    hint.textContent = "Student, member, and senior/PWD discounts are applied in the next step.";
  }

  boxes.forEach(function (box) {
    box.addEventListener("change", function () {
      var h = parseInt(box.value, 10);
      var hours = selectedHours();
      if (box.checked && hours.length > 1) {
        // Keep only a consecutive run that includes the hour just picked.
        var min = h, max = h;
        while (hours.indexOf(min - 1) !== -1) min--;
        while (hours.indexOf(max + 1) !== -1) max++;
        boxes.forEach(function (b) {
          var v = parseInt(b.value, 10);
          if (b.checked && (v < min || v > max)) b.checked = false;
        });
      }
      if (!box.checked) {
        // Unchecking a middle hour splits the run: keep the part after it.
        var rest = selectedHours();
        var after = rest.filter(function (v) { return v > h; });
        var before = rest.filter(function (v) { return v < h; });
        if (after.length && before.length) {
          before.forEach(function (v) {
            boxes.forEach(function (b) { if (parseInt(b.value, 10) === v) b.checked = false; });
          });
        }
      }
      refresh();
    });
  });
  refresh();
})();
