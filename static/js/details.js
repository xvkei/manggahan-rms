// Live price summary on the details page. The server recomputes the same rules on submit.
(function () {
  var form = document.getElementById("details-form");
  var dataEl = document.getElementById("price-data");
  if (!form || !dataEl) return;
  var data = JSON.parse(dataEl.textContent);
  var peso = window.RMS.peso, round2 = window.RMS.round2;
  var methodLabels = { gcash: "GCash", maya: "Maya", card: "card" };

  function val(name) {
    var el = form.querySelector('input[name="' + name + '"]:checked');
    return el ? el.value : null;
  }

  function refresh() {
    var type = val("player_type") || "walkin";
    var typePct = data.typePct[type] || 0;
    var fee = 0, disc = 0, nets = [], reasons = {};
    data.hours.forEach(function (h) {
      var base = parseFloat(h.base);
      var pct = 0, reason = "";
      if (h.promo > typePct) { pct = h.promo; reason = h.promoName; }
      else if (typePct > 0) { pct = typePct; reason = data.typeLabel[type]; }
      var d = round2(base * pct / 100);
      if (pct) reasons[reason + " " + pct + "%"] = true;
      fee += base; disc += d; nets.push(base - d);
    });
    var redeemBox = document.getElementById("redeem");
    var free = redeemBox && redeemBox.checked ? Math.min.apply(null, nets) : 0;
    var equip = 0;
    form.querySelectorAll("input[data-item]").forEach(function (inp) {
      var q = Math.max(0, parseInt(inp.value, 10) || 0);
      equip += q * parseFloat(data.items[inp.getAttribute("data-item")] || 0);
    });
    var total = Math.max(0, round2(fee - disc - free + equip));

    document.getElementById("s-type").textContent = data.typeLabel[type];
    document.getElementById("s-fee").textContent = peso(fee);
    var names = Object.keys(reasons);
    document.getElementById("s-disc-label").textContent = names.length ? names.join(", ") : "Discount";
    document.getElementById("s-disc").textContent = disc ? "− " + peso(disc) : "None";
    document.getElementById("s-free-row").hidden = !free;
    document.getElementById("s-free").textContent = "− " + peso(free);
    document.getElementById("s-equip").textContent = equip ? peso(equip) : "None added";
    document.getElementById("s-total").textContent = peso(total);

    var method = val("method");
    var btn = document.getElementById("pay-btn");
    if (total <= 0) btn.textContent = "Confirm booking";
    else if (method === "desk") btn.textContent = "Reserve, pay at the desk";
    else btn.textContent = "Pay " + peso(total) + " with " + (methodLabels[method] || "GCash");
  }

  form.addEventListener("change", refresh);
  form.addEventListener("input", refresh);
  refresh();
})();
