(function () {
  var TAG = "[payment-result]";
  var node = document.getElementById("pay-result-poll");
  if (!node) return;
  var cfg;
  try {
    cfg = JSON.parse(node.textContent || "{}");
  } catch (e) {
    console.error(TAG, "bootstrap-parse", e && e.message, e);
    return;
  }
  var interval = Number(cfg.interval_ms) || 3000;
  var deadline = Date.now() + (Number(cfg.max_ms) || 60000);
  var query = "?paymentId=" + encodeURIComponent(cfg.payment_id || "") + "&intent=" + encodeURIComponent(cfg.intent || "");

  function timedOut() {
    var pending = document.getElementById("prPending");
    var timeout = document.getElementById("prTimeout");
    if (pending) pending.hidden = true;
    if (timeout) timeout.hidden = false;
    var card = document.getElementById("payResult");
    if (card) card.setAttribute("data-state", "timeout");
  }

  function next() {
    if (Date.now() >= deadline) {
      timedOut();
      return;
    }
    setTimeout(check, interval);
  }

  function check() {
    fetch(cfg.url + query, { credentials: "same-origin", headers: { Accept: "application/json" }, cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (out) {
        var state = (out && out.state) || "pending";
        if (state === "paid" || state === "failed") {
          window.location.reload();
          return;
        }
        next();
      })
      .catch(function (err) {
        console.error(TAG, "poll", err && err.message, err);
        next();
      });
  }

  next();
})();
