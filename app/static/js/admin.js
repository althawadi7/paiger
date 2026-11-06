(function () {
  var openers = new WeakMap();

  function toBody(node) {
    if (node && node.parentNode !== document.body) document.body.appendChild(node);
  }

  function lift() {
    var pageScrim = document.getElementById("scrim");
    var drawer = document.getElementById("drawer");
    toBody(pageScrim);
    toBody(drawer);
    document.querySelectorAll(".modal").forEach(toBody);
  }

  function firstField(root) {
    return root.querySelector("input:not([type=hidden]), select, textarea") || root.querySelector(".box button");
  }

  function openDialog(dialog, from) {
    if (!dialog) return;
    lift();
    openers.set(dialog, from || document.activeElement);
    var pageScrim = document.getElementById("scrim");
    if (dialog.classList.contains("modal")) {
      toBody(dialog);
      if (pageScrim) pageScrim.hidden = true;
    }
    if (dialog.id === "drawer") {
      toBody(pageScrim);
      toBody(dialog);
      if (pageScrim) pageScrim.hidden = false;
    }
    dialog.hidden = false;
    var field = firstField(dialog);
    if (field) field.focus();
  }

  function closeDialog(dialog) {
    if (!dialog || dialog.hidden) return;
    dialog.hidden = true;
    var pageScrim = document.getElementById("scrim");
    var drawer = document.getElementById("drawer");
    if (dialog.id === "drawer") {
      if (pageScrim) pageScrim.hidden = true;
    } else if (pageScrim) {
      pageScrim.hidden = !(drawer && !drawer.hidden);
    }
    var back = openers.get(dialog);
    openers.delete(dialog);
    if (back && typeof back.focus === "function") back.focus();
  }

  window.paigerDialog = { open: openDialog, close: closeDialog };
  lift();

  document.addEventListener("click", function (e) {
    if (e.target.id === "scrim") {
      closeDialog(document.getElementById("drawer"));
      return;
    }
    var closer = e.target.closest("[data-close]");
    if (!closer) return;
    var modal = closer.closest(".modal");
    if (modal) closeDialog(modal);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var modal = document.querySelector(".modal:not([hidden])");
    if (modal) { closeDialog(modal); return; }
    var drawer = document.getElementById("drawer");
    if (drawer && !drawer.hidden) closeDialog(drawer);
  });

  var toast = document.getElementById("toast");
  if (toast && toast.textContent.trim()) {
    toast.hidden = false;
    var url = new URL(window.location.href);
    ["notice", "error", "undo", "id", "handoff"].forEach(function (key) { url.searchParams.delete(key); });
    var next = url.pathname + (url.searchParams.toString() ? "?" + url.searchParams.toString() : "") + url.hash;
    if (next !== window.location.pathname + window.location.search + window.location.hash) {
      history.replaceState(null, "", next);
    }
    var hold = parseFloat(toast.getAttribute("data-hold") || "2.5");
    if (!isFinite(hold) || hold < 0) hold = 2.5;
    setTimeout(function () { toast.hidden = true; }, hold * 1000);
  }
  var menu = document.getElementById("menuBtn");
  if (menu) {
    menu.addEventListener("click", function () {
      document.getElementById("side").classList.toggle("open");
    });
  }
  var chart = document.getElementById("chart");
  var tip = document.getElementById("tip");
  if (chart && tip) {
    chart.addEventListener("mousemove", function (e) {
      var g = e.target.closest("g[data-m]");
      if (!g) { tip.hidden = true; return; }
      var bar = g.querySelector(".bar");
      var wr = document.getElementById("chartWrap").getBoundingClientRect();
      var r = bar.getBoundingClientRect();
      tip.textContent = g.getAttribute("data-m") + ": BHD " + g.getAttribute("data-v");
      tip.hidden = false;
      tip.style.left = (r.left - wr.left + r.width / 2) + "px";
      tip.style.top = (r.top - wr.top) + "px";
    });
    chart.addEventListener("mouseleave", function () { tip.hidden = true; });
  }
})();
