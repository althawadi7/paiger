(function () {
  var TAG = "[billing-checkout]";
  var node = document.getElementById("billing-checkout-bootstrap");
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
  var saveCard = document.getElementById("saveCard");

  var checkout = TringsMF.mount({
    tag: "billing-checkout",
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
      return TringsMF.postJson("/api/billing/checkout/session", {
        intent_id: intentId,
        save_card: saveCard ? saveCard.checked : false,
      }).then(function (data) {
        return { sessionId: (data.session && data.session.SessionId) || "", intentId: intentId };
      });
    },
    processUrl: "/api/payment/myfatoorah/process",
    processBody: function (ctx, result) {
      return {
        intent_id: ctx.intentId,
        session_id: result.session_id,
        payment_data: result.payment_data,
        redirection_url: result.redirection_url,
      };
    },
    onPaid: function (out) {
      window.location.href =
        "/payment/myfatoorah/return?paymentId=" +
        encodeURIComponent(out.payment_id || "") +
        "&intent=" +
        encodeURIComponent(intentId);
    },
  });
  if (!checkout) return;
  if (saveCard) saveCard.addEventListener("change", checkout.start);
  checkout.start();
})();
