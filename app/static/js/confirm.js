(function () {
  var modal = document.getElementById("confirmModal");
  if (!modal) return;
  var asks = {};
  try {
    asks = JSON.parse((document.getElementById("confirmAsks") || {}).textContent || "{}");
  } catch (err) {
    asks = {};
  }
  var title = document.getElementById("confirmTitle");
  var text = document.getElementById("confirmText");
  var ok = document.getElementById("confirmOk");
  var cancelBtn = document.getElementById("confirmCancel");
  var pending = null;
  var opener = null;

  function resetOk() {
    ok.disabled = false;
    ok.classList.remove("busy");
    ok.removeAttribute("aria-busy");
  }

  function finish(yes) {
    if (!pending) return;
    var resolve = pending;
    pending = null;
    if (!yes) {
      resetOk();
      modal.hidden = true;
      if (opener && typeof opener.focus === "function") opener.focus();
    }
    resolve(yes);
  }

  function open(opts) {
    if (modal.parentNode !== document.body) document.body.appendChild(modal);
    opener = document.activeElement;
    title.textContent = opts.title || "";
    text.textContent = opts.text || "";
    ok.textContent = opts.ok || "";
    var danger = opts.tone === "danger";
    ok.className = "btn " + (danger ? "btn-stop" : "btn-ink");
    ok.dataset.tone = danger ? "danger" : "ink";
    resetOk();
    modal.hidden = false;
    cancelBtn.focus();
  }

  window.paigerConfirm = function (opts) {
    return new Promise(function (resolve) {
      if (pending) finish(false);
      pending = resolve;
      open(opts || {});
    });
  };

  function accept() {
    if (!pending || ok.disabled) return;
    ok.disabled = true;
    ok.classList.add("busy");
    ok.setAttribute("aria-busy", "true");
    var resolve = pending;
    pending = null;
    resolve(true);
  }

  document.addEventListener("click", function (e) {
    if (modal.hidden || !pending) return;
    if (!e.target.closest("#confirmModal [data-close]")) return;
    e.preventDefault();
    e.stopPropagation();
    finish(false);
  }, true);

  document.addEventListener("keydown", function (e) {
    if (modal.hidden) return;
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      finish(false);
      return;
    }
    if (e.key !== "Enter") return;
    if (ok.dataset.tone === "danger") {
      if (e.target !== cancelBtn) {
        e.preventDefault();
        e.stopPropagation();
      }
      return;
    }
    if (e.target === cancelBtn) return;
    e.preventDefault();
    e.stopPropagation();
    accept();
  }, true);

  ok.addEventListener("click", function () { accept(); });

  function fill(source, spec) {
    var out = spec.text || "";
    out = out.split("{name}").join(source.getAttribute("data-name") || "");
    out = out.split("{number}").join(source.getAttribute("data-number") || "");
    out = out.split("{amount}").join(source.getAttribute("data-amount") || "");
    var days = source.querySelector ? source.querySelector("[name=days]") : null;
    if (days) out = out.split("{days}").join(days.value);
    var delta = source.querySelector ? source.querySelector("[name=delta]") : null;
    if (delta) {
      var n = String(delta.value || "").trim();
      if (n && n.charAt(0) !== "-" && n.charAt(0) !== "+") n = "+" + n;
      out = out.split("{delta}").join(n);
    }
    var until = source.querySelector ? source.querySelector("[name=until]") : null;
    if (until) out = out.split("{date}").join(until.value);
    var sel = source.querySelector ? source.querySelector("select[name=code]") : null;
    if (sel && sel.selectedIndex >= 0) out = out.split("{plan}").join(sel.options[sel.selectedIndex].text);
    return { title: spec.title, text: out, ok: spec.ok, tone: spec.tone };
  }

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form || !form.getAttribute || !form.getAttribute("data-ask")) return;
    if (form.getAttribute("data-asked") === "1") return;
    var spec = asks[form.getAttribute("data-ask")];
    if (!spec) return;
    e.preventDefault();
    e.stopPropagation();
    window.paigerConfirm(fill(form, spec)).then(function (yes) {
      if (!yes) return;
      form.setAttribute("data-asked", "1");
      window.setTimeout(function () { form.submit(); }, 40);
    });
  }, true);
})();
