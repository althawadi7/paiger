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
  var packs = boot.packs || [];
  var selectedPack = boot.pack || null;
  var loadedKey = "";
  var intents = {};
  var saveMode = "";

  var summaryPlan = document.getElementById("summaryPlanName");
  var summaryNote = document.getElementById("summaryNote");
  var summaryDue = document.getElementById("summaryDueAmount");
  var payBtn = document.getElementById("subscribePay");
  var scheduleBtn = document.getElementById("schedulePlanBtn");
  var saveCard = document.getElementById("saveCardSub");
  var saveCardRow = document.getElementById("saveCardRow");
  var saveCardText = document.getElementById("saveCardText");
  var payAhead = document.getElementById("payAheadBtn");
  var buyPoints = document.getElementById("buyPointsBtn");
  var packPicker = document.getElementById("packPicker");
  var packItems = packPicker ? packPicker.querySelectorAll(".pack[role='radio']") : [];
  var cancelChange = document.getElementById("cancelPlanChange");
  var currentCode = boot.current || "";

  function planByCode(code) {
    for (var i = 0; i < plans.length; i++) {
      if (plans[i].code === code) return plans[i];
    }
    return null;
  }

  function packById(id) {
    for (var i = 0; i < packs.length; i++) {
      if (String(packs[i].id) === String(id)) return packs[i];
    }
    return null;
  }

  function isSchedule(plan) {
    return !!plan && plan.mode === "schedule";
  }

  function isPacks(plan) {
    return !!plan && plan.mode === "packs";
  }

  function checkoutKey() {
    var plan = planByCode(selectedCode);
    return isPacks(plan) ? selectedCode + ":" + (selectedPack || "") : selectedCode;
  }

  function syncSaveRow(plan) {
    var mode = isPacks(plan) ? "topup" : "renew";
    if (mode === saveMode) return;
    saveMode = mode;
    if (saveCardText) saveCardText.textContent = mode === "topup" ? messages.save_topup : messages.save_renew;
    if (saveCard) saveCard.checked = mode === "topup" ? !!boot.auto_topup : true;
  }

  function updateSummary() {
    var plan = planByCode(selectedCode);
    var schedule = isSchedule(plan);
    var pack = isPacks(plan) ? packById(selectedPack) : null;
    var name = plan ? plan.summary_name || plan.name : messages.empty_plan || "—";
    if (pack) {
      name = (messages.pack_summary || "{plan} · {n}")
        .replace("{plan}", plan.name)
        .replace("{n}", Number(pack.points).toLocaleString("en-US"));
    }
    var due = "—";
    if (plan && isPacks(plan)) due = pack ? "BHD " + pack.price_label : "—";
    else if (plan) due = "BHD " + (schedule ? "0.000" : plan.price_label);
    if (summaryPlan) summaryPlan.textContent = name;
    if (summaryNote) {
      summaryNote.textContent = (plan && plan.note) || "";
      summaryNote.hidden = !(plan && plan.note);
    }
    if (summaryDue) summaryDue.textContent = due;
    if (payBtn) payBtn.hidden = schedule;
    if (saveCardRow) saveCardRow.hidden = schedule;
    if (scheduleBtn) scheduleBtn.hidden = !schedule;
    if (packPicker) packPicker.hidden = !isPacks(plan);
    syncSaveRow(plan);
  }

  function createIntent(key) {
    var plan = planByCode(selectedCode);
    var body = isPacks(plan)
      ? { purpose: "topup", pack_id: selectedPack, plan_code: selectedCode }
      : { purpose: "subscribe", plan_code: selectedCode };
    return TringsMF.postJson("/api/billing/checkout/intent", body).then(function (res) {
      intents[key] = res.intent.intent_id;
      return intents[key];
    });
  }

  function openSession(code, intentId) {
    return TringsMF.postJson("/api/billing/checkout/session", {
      intent_id: intentId,
      save_card: saveCard ? saveCard.checked : false,
    });
  }

  function getSession() {
    var code = selectedCode;
    var plan = planByCode(code);
    if (!plan) return Promise.reject(new Error("No plan selected"));
    if (isPacks(plan) && !packById(selectedPack)) return Promise.reject(new Error("No pack selected"));
    var key = checkoutKey();
    var known = intents[key];
    var first = known ? openSession(code, known) : createIntent(key).then(function (id) {
      return openSession(code, id);
    });
    return first
      .catch(function (err) {
        if (known && err && err.status === 400) {
          console.warn(TAG, "intent no longer usable, creating a new one", err.detail);
          delete intents[key];
          return createIntent(key).then(function (id) {
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
      if (isPacks(plan)) return !!packById(selectedPack);
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
      loadedKey = "";
      checkout.idle(messages.no_charge);
      return;
    }
    var key = checkoutKey();
    if (key !== loadedKey) {
      loadedKey = key;
      checkout.start();
    }
  }

  function markPacks() {
    Array.prototype.forEach.call(packItems, function (node) {
      var on = String(node.getAttribute("data-pack")) === String(selectedPack);
      node.setAttribute("aria-checked", on ? "true" : "false");
      node.tabIndex = on ? 0 : -1;
    });
  }

  function pickPack(node) {
    selectedPack = Number(node.getAttribute("data-pack"));
    markPacks();
    if (isPacks(planByCode(selectedCode))) choose(selectedCode);
  }

  if (!packById(selectedPack) && packs.length) selectedPack = packs[0].id;
  markPacks();
  Array.prototype.forEach.call(packItems, function (node, idx) {
    node.addEventListener("click", function () {
      pickPack(node);
    });
    node.addEventListener("keydown", function (ev) {
      var step = ev.key === "ArrowRight" || ev.key === "ArrowDown" ? 1 : ev.key === "ArrowLeft" || ev.key === "ArrowUp" ? -1 : 0;
      if (!step) return;
      ev.preventDefault();
      var next = packItems[(idx + step + packItems.length) % packItems.length];
      next.focus();
      pickPack(next);
    });
  });

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

  function pressCardAction(on) {
    if (payAhead) payAhead.setAttribute("aria-pressed", on === payAhead ? "true" : "false");
    if (buyPoints) buyPoints.setAttribute("aria-pressed", on === buyPoints ? "true" : "false");
  }

  function pick(el) {
    if (locked(el)) return;
    markItems(el);
    pressCardAction(null);
    choose(el.getAttribute("data-code") || "");
  }

  function pickCurrent() {
    var button = currentCode === "points" ? buyPoints : payAhead;
    if (!button || !currentCode) return;
    markItems(null);
    pressCardAction(button);
    choose(currentCode);
  }

  if (payAhead) payAhead.addEventListener("click", pickCurrent);
  if (buyPoints) buyPoints.addEventListener("click", pickCurrent);

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
      if (plan && !isSchedule(plan) && checkoutKey() === loadedKey) checkout.start();
    });
  }

  updateSummary();
  var initial = null;
  Array.prototype.forEach.call(items, function (el) {
    if (selectedCode && (el.getAttribute("data-code") || "") === selectedCode) initial = el;
  });
  if (selectedCode && selectedCode === currentCode && (payAhead || buyPoints)) pickCurrent();
  else if (initial && !locked(initial)) pick(initial);
  else {
    selectedCode = "";
    updateSummary();
    markItems(null);
    checkout.idle(messages.pick_plan);
  }
})();
