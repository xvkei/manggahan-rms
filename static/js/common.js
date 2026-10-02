// Shared helpers: +/- steppers and confirm prompts.
(function () {
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-stepper] button[data-step]");
    if (btn) {
      var input = btn.parentElement.querySelector("input");
      var step = parseInt(btn.getAttribute("data-step"), 10);
      var min = input.hasAttribute("min") ? parseInt(input.min, 10) : -Infinity;
      var max = input.hasAttribute("max") ? parseInt(input.max, 10) : Infinity;
      var next = Math.min(max, Math.max(min, (parseInt(input.value, 10) || 0) + step));
      input.value = next;
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }
  });
  document.addEventListener("submit", function (e) {
    var msg = e.target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) e.preventDefault();
  });
})();

window.RMS = {
  peso: function (n, decimals) {
    decimals = decimals === undefined ? 2 : decimals;
    return "₱" + Number(n).toLocaleString("en-PH", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  },
  round2: function (n) { return Math.round(n * 100) / 100; }
};
