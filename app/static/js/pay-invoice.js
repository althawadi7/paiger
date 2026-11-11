(function () {
  var TAG = "[pay-invoice]";
  var node = document.getElementById("pay-checkout-bootstrap");
  if (!node) return;
  var boot;
  try {
    boot = JSON.parse(node.textContent || "{}");
  } catch (e) {
    console.error(TAG, "bootstrap-parse", e && e.message, e);
    return;
  }
  var cfg = boot.config || {};
  var m = boot.messages || {};
  var intentId = boot.intent_id || "";
  var guest = !!boot.guest;
  var auth = guest ? { number: boot.number || "", token: boot.token || "" } : {};
  var sessionPath = guest ? "/api/billing/pay-link/session" : "/api/billing/checkout/session";
  var processPath = guest ? "/api/billing/pay-link/process" : "/api/payment/myfatoorah/process";

  function withAuth(body) {
    for (var k in auth) body[k] = auth[k];
    return body;
  }

  var checkout = TringsMF.mount({
    tag: "pay-invoice",
    containerId: "mf-form",
    payButton: document.getElementById("payBtn"),
    testMode: !!cfg.is_test,
    language: document.documentElement.lang === "ar" ? "ar" : "en",
    messages: {
      loading: m.loading,
      sdk: m.err_sdk,
      session: m.err_session,
      init: m.err_init,
      declined: m.err_declined,
      failed: m.err_failed,
      retry: m.retry,
      processing: m.processing,
    },
    getSession: function () {
      return TringsMF.postJson(sessionPath, withAuth({ intent_id: intentId, save_card: false })).then(function (data) {
        return { sessionId: (data.session && data.session.SessionId) || "", intentId: intentId };
      });
    },
    processUrl: processPath,
    processBody: function (ctx, result) {
      return withAuth({
        intent_id: ctx.intentId,
        session_id: result.session_id,
        payment_data: result.payment_data,
        redirection_url: result.redirection_url,
      });
    },
    onPaid: function (out) {
      window.location.href =
        "/payment/myfatoorah/return?paymentId=" +
        encodeURIComponent(out.payment_id || "") +
        "&intent=" +
        encodeURIComponent(intentId);
    },
  });
  if (checkout) checkout.start();
})();
