// Walk-in & sales: availability strip, live cart total, and change.
(function () {
  var form = document.getElementById("pos");
  var dataEl = document.getElementById("pos-data");
  if (!form || !dataEl) return;
  var D = JSON.parse(dataEl.textContent);
  var peso = window.RMS.peso, round2 = window.RMS.round2;
  var $ = function (sel) { return form.querySelector(sel); };
  var $$ = function (sel) { return Array.prototype.slice.call(form.querySelectorAll(sel)); };

  function checked(name) { var el = $('input[name="' + name + '"]:checked'); return el ? el.value : ""; }

  // Highlight chips / segmented labels whose radio is checked.
  function syncChips() {
    $$('label input[type="radio"]').forEach(function (r) {
      var label = r.closest("label");
      if (label && (label.classList.contains("chip") || label.parentElement.classList.contains("segmented"))) {
        label.classList.toggle("is-on", r.checked);
      }
    });
  }

  // Sport filter for the court cards.
  $$("[data-sport]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var sport = btn.getAttribute("data-sport");
      $$("[data-sport]").forEach(function (b) { b.classList.toggle("is-on", b === btn); });
      $$("[data-court-sport]").forEach(function (card) {
        card.hidden = sport && card.getAttribute("data-court-sport") !== sport;
      });
    });
  });

  document.getElementById("date-input").addEventListener("change", function (e) {
    window.location.search = "?date=" + e.target.value;
  });

  function promoPct(court, hour) {
    var best = 0;
    D.promos.forEach(function (p) {
      if (p.days.indexOf(D.weekday) !== -1 && hour >= p.start && hour < p.end && (!p.sport || p.sport === court.sport)) {
        best = Math.max(best, p.pct);
      }
    });
    return best;
  }

  function renderStrip() {
    var strip = document.getElementById("strip");
    var courtId = checked("court");
    var states = D.states[courtId] || {};
    var start = parseInt($("#start").value, 10), end = parseInt($("#end").value, 10);
    strip.innerHTML = "";
    Object.keys(states).map(Number).sort(function (a, b) { return a - b; }).forEach(function (h) {
      var st = states[h];
      var el = document.createElement(st === "open" ? "button" : "div");
      el.className = "slot" + (st === "open" ? "" : (st === "taken" ? " taken" : st === "maintenance" ? " maint" : " past"));
      var inRange = h >= start && h < end;
      el.innerHTML = '<span class="face" style="height: 44px;' + (inRange && st === "open" ? "background:#0F3D2E;color:#fff;border-color:#0F3D2E" : "") +
        '"><span class="t">' + D.labels[h].replace(":00", "") + "</span></span>";
      if (st === "open") {
        el.type = "button";
        el.style.cssText = "border:0;background:none;padding:0;cursor:pointer";
        el.setAttribute("aria-label", "Start at " + D.labels[h]);
        el.addEventListener("click", function () {
          $("#start").value = h;
          if (parseInt($("#end").value, 10) <= h) $("#end").value = h + 1;
          refresh();
        });
      }
      strip.appendChild(el);
    });
    var court = D.courts[courtId];
    document.getElementById("strip-label").textContent = court ? "Availability · " + court.name : "Availability";
  }

  function courtLine() {
    if (checked("mode") !== "booking") return null;
    var court = D.courts[checked("court")];
    var start = parseInt($("#start").value, 10), end = parseInt($("#end").value, 10);
    if (!court || !(end > start)) return null;
    var typePct = D.typePct[checked("player_type")] || 0;
    var total = 0;
    for (var h = start; h < end; h++) {
      var base = parseFloat(h >= D.eveningStart ? court.evening : court.day);
      var pct = Math.max(typePct, promoPct(court, h));
      total += base - round2(base * pct / 100);
    }
    var pkg = $('input[name="use_package"]').checked;
    return {
      label: court.name + " · " + D.labels[start] + " to " + D.labels[end] + (pkg ? " (package hours)" : ""),
      amount: pkg ? 0 : round2(total)
    };
  }

  function refresh() {
    syncChips();
    var booking = checked("mode") === "booking";
    document.getElementById("booking-fields").hidden = !booking;
    document.getElementById("items-note").hidden = booking;
    if (booking) renderStrip();

    var cart = document.getElementById("cart");
    cart.innerHTML = "";
    var total = 0;
    var line = courtLine();
    if (line) {
      cart.insertAdjacentHTML("beforeend", '<div class="cart-line"><span>' + line.label + "</span><b>" + peso(line.amount) + "</b></div>");
      total += line.amount;
    }
    $$("input[data-qty]").forEach(function (inp) {
      var id = inp.getAttribute("data-qty");
      var qty = parseInt(inp.value, 10) || 0;
      var badge = form.querySelector('[data-qty-badge="' + id + '"]');
      var btn = form.querySelector('[data-add="' + id + '"]');
      badge.hidden = !qty; badge.textContent = qty;
      btn.classList.toggle("has", qty > 0);
      if (qty) {
        var amt = qty * parseFloat(D.items[id]);
        total += amt;
        var row = document.createElement("div");
        row.className = "cart-line";
        row.innerHTML = "<span>" + btn.getAttribute("data-name") + " × " + qty +
          ' <button type="button" aria-label="Remove one">remove</button></span><b>' + peso(amt) + "</b>";
        row.querySelector("button").addEventListener("click", function () {
          inp.value = Math.max(0, qty - 1); refresh();
        });
        cart.appendChild(row);
      }
    });
    var plan = $("#plan");
    if (plan && plan.value) {
      var price = parseFloat(D.plans[plan.value]);
      total += price;
      cart.insertAdjacentHTML("beforeend", '<div class="cart-line"><span>' + plan.options[plan.selectedIndex].text.split(" · ")[0] + "</span><b>" + peso(price) + "</b></div>");
    }
    if (!cart.children.length) cart.innerHTML = '<p class="small muted" style="margin: 0">Nothing added yet.</p>';

    total = round2(total);
    document.getElementById("cart-total").textContent = peso(total);
    var name = $('input[name="name"]').value.trim();
    document.getElementById("cart-who").textContent = name || "Walk-in";

    var method = checked("method");
    document.getElementById("cash-row").hidden = method !== "cash";
    document.getElementById("ref-row").hidden = method === "cash";
    var cash = parseFloat(($("#cash").value || "0").replace(/[^\d.]/g, "")) || 0;
    document.getElementById("change").textContent = peso(Math.max(0, cash - total));
    document.getElementById("charge").textContent = total > 0 ? "Charge " + peso(total) + " and print receipt" : "Save";
  }

  $$("[data-add]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var inp = form.querySelector('input[data-qty="' + btn.getAttribute("data-add") + '"]');
      inp.value = (parseInt(inp.value, 10) || 0) + 1;
      refresh();
    });
  });
  form.addEventListener("change", refresh);
  form.addEventListener("input", function (e) { if (e.target.id !== "date-input") refresh(); });
  refresh();
})();
