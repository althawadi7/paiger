(function () {
  var TAG = "[subscription-checkout]";
  var bootNode = document.getElementById("checkout-bootstrap");
  if (!bootNode) {
    console.error(TAG, "bootstrap-missing", "#checkout-bootstrap not found");
    return;
  }
  var boot;
  try {
    boot = JSON.parse(bootNode.textContent || "{}");
  } catch (e) {
    console.error(TAG, "bootstrap-parse", e && e.message, e);
    return;
  }

  var cfg = boot.config || {};
  var plans = boot.plans || [];
  var messages = boot.messages || {};
  var selectedCode = boot.must_pick ? "" : boot.selected || "";
  var loadedCode = "";
  var intents = {};

  var summaryPlan = document.getElementById("summaryPlanName");
  var summaryNote = document.getElementById("summaryNote");
  var summaryDue = document.getElementById("summaryDueAmount");
  var payBtn = document.getElementById("subscribePay");
  var scheduleBtn = document.getElementById("schedulePlanBtn");
  var saveCard = document.getElementById("saveCardSub");
  var saveCardRow = document.getElementById("saveCardRow");
  var payAhead = document.getElementById("payAheadBtn");
  var cancelChange = document.getElementById("cancelPlanChange");
  var currentCode = boot.current || "";

  function planByCode(code) {
    for (var i = 0; i < plans.length; i++) {
      if (plans[i].code === code) return plans[i];
    }
    return null;
  }

  function isSchedule(plan) {
    return !!plan && plan.mode === "schedule";
  }

  function updateSummary() {
    var plan = planByCode(selectedCode);
    var schedule = isSchedule(plan);
    if (summaryPlan) summaryPlan.textContent = plan ? plan.summary_name || plan.name : messages.empty_plan || "—";
    if (summaryNote) {
      summaryNote.textContent = (plan && plan.note) || "";
      summaryNote.hidden = !(plan && plan.note);
    }
    if (summaryDue) summaryDue.textContent = plan ? "BHD " + (schedule ? "0.000" : plan.price_label) : "—";
    if (payBtn) payBtn.hidden = schedule;
    if (saveCardRow) saveCardRow.hidden = schedule;
    if (scheduleBtn) scheduleBtn.hidden = !schedule;
  }

  function createIntent(code) {
    return TringsMF.postJson("/api/billing/checkout/intent", { purpose: "subscribe", plan_code: code }).then(
      function (body) {
        intents[code] = body.intent.intent_id;
        return intents[code];
      }
    );
  }

  function openSession(code, intentId) {
    return TringsMF.postJson("/api/billing/checkout/session", {
      intent_id: intentId,
      save_card: saveCard ? saveCard.checked : false,
    });
  }

  function getSession() {
    var code = selectedCode;
    if (!planByCode(code)) return Promise.reject(new Error("No plan selected"));
    var known = intents[code];
    var first = known ? openSession(code, known) : createIntent(code).then(function (id) {
      return openSession(code, id);
    });
    return first
      .catch(function (err) {
        if (known && err && err.status === 400) {
          console.warn(TAG, "intent no longer usable, creating a new one", err.detail);
          delete intents[code];
          return createIntent(code).then(function (id) {
            return openSession(code, id);
          });
        }
        throw err;
      })
      .then(function (data) {
        return {
          sessionId: (data.session && data.session.SessionId) || "",
          intentId: data.intent_id,
          planCode: code,
        };
      });
  }

  var checkout = TringsMF.mount({
    tag: "subscription-checkout",
    containerId: "mf-form",
    payButton: payBtn,
    testMode: !!(boot.test_mode || cfg.is_test),
    language: document.documentElement.lang === "ar" ? "ar" : "en",
    messages: {
      loading: messages.loading,
      sdk: messages.err_sdk,
      session: messages.err_session,
      init: messages.err_init,
      declined: messages.err_declined,
      failed: messages.err_failed,
      retry: messages.retry,
      processing: messages.processing,
    },
    canPay: function () {
      var plan = planByCode(selectedCode);
      return !!plan && !isSchedule(plan);
    },
    getSession: getSession,
    processUrl: "/api/payment/myfatoorah/process",
    processBody: function (ctx, result) {
      return {
        intent_id: ctx.intentId,
        session_id: result.session_id,
        payment_data: result.payment_data,
        redirection_url: result.redirection_url,
      };
    },
    onPaid: function (out, ctx) {
      window.location.href =
        "/payment/myfatoorah/return?paymentId=" +
        encodeURIComponent(out.payment_id || "") +
        "&intent=" +
        encodeURIComponent(ctx.intentId || "");
    },
  });
  if (!checkout) return;

  function choose(code) {
    var plan = planByCode(code);
    if (!plan) {
      console.error(TAG, "plan-click", "unknown plan code", code);
      return;
    }
    selectedCode = code;
    updateSummary();
    console.info(TAG, "plan-click", code, plan.mode);
    if (isSchedule(plan)) {
      loadedCode = "";
      checkout.idle(messages.no_charge);
      return;
    }
    if (code !== loadedCode) {
      loadedCode = code;
      checkout.start();
    }
  }

  var group = document.querySelector(".plans[role='radiogroup']");
  var items = group ? group.querySelectorAll(".plan[role='radio']") : [];
  if (!items.length) console.error(TAG, "plan-ui", "no .plan[role=radio] items found");

  function locked(el) {
    return el.getAttribute("aria-disabled") === "true";
  }

  function markItems(active) {
    Array.prototype.forEach.call(items, function (node) {
      var on = node === active;
      node.setAttribute("aria-checked", on ? "true" : "false");
      node.tabIndex = on || (!active && !locked(node)) ? 0 : -1;
    });
  }

  function pick(el) {
    if (locked(el)) return;
    markItems(el);
    if (payAhead) payAhead.setAttribute("aria-pressed", "false");
    choose(el.getAttribute("data-code") || "");
  }

  function pickPayAhead() {
    if (!payAhead || !currentCode) return;
    markItems(null);
    payAhead.setAttribute("aria-pressed", "true");
    choose(currentCode);
  }

  if (payAhead) payAhead.addEventListener("click", pickPayAhead);

  if (scheduleBtn) {
    scheduleBtn.addEventListener("click", function () {
      scheduleBtn.disabled = true;
      TringsMF.postJson("/api/billing/plan-change", { plan_code: selectedCode })
        .then(function () {
          window.location.reload();
        })
        .catch(function (err) {
          console.error(TAG, "plan-change", err && (err.detail || err.message), err);
          scheduleBtn.disabled = false;
          if (summaryNote) {
            summaryNote.textContent = messages.schedule_failed || "";
            summaryNote.hidden = false;
          }
        });
    });
  }

  if (cancelChange) {
    cancelChange.addEventListener("click", function () {
      cancelChange.disabled = true;
      TringsMF.postJson("/api/billing/plan-change/cancel", {})
        .then(function () {
          window.location.reload();
        })
        .catch(function (err) {
          console.error(TAG, "plan-change-cancel", err && (err.detail || err.message), err);
          cancelChange.disabled = false;
        });
    });
  }

  Array.prototype.forEach.call(items, function (el, idx) {
    el.addEventListener("click", function () {
      pick(el);
    });
    el.addEventListener("keydown", function (ev) {
      var next = null;
      if (ev.key === "Enter" || ev.key === " ") next = el;
      else if (ev.key === "ArrowRight" || ev.key === "ArrowDown") next = items[(idx + 1) % items.length];
      else if (ev.key === "ArrowLeft" || ev.key === "ArrowUp") next = items[(idx - 1 + items.length) % items.length];
      if (next && next !== el && locked(next)) {
        var step = ev.key === "ArrowRight" || ev.key === "ArrowDown" ? 2 : -2;
        next = items[(idx + step + items.length * 2) % items.length];
      }
      if (!next || locked(next)) return;
      ev.preventDefault();
      next.focus();
      pick(next);
    });
  });

  if (saveCard) {
    saveCard.addEventListener("change", function () {
      var plan = planByCode(selectedCode);
      if (plan && !isSchedule(plan)) checkout.start();
    });
  }

  updateSummary();
  var initial = null;
  Array.prototype.forEach.call(items, function (el) {
    if (selectedCode && (el.getAttribute("data-code") || "") === selectedCode) initial = el;
  });
  if (selectedCode && selectedCode === currentCode && payAhead) pickPayAhead();
  else if (initial && !locked(initial)) pick(initial);
  else {
    selectedCode = "";
    updateSummary();
    markItems(null);
    checkout.idle(messages.pick_plan);
  }
})();
