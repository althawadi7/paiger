/* MyFatoorah Embedded Payment v3 (sessions/v1/session.js, global `myfatoorah`).
   The payment box never collapses: idle/loading show a skeleton, failures show the
   error and a retry button under it. */
(function () {
  var EVENTS = [
    "VIEW_READY",
    "PAYMENT_STARTED",
    "PAYMENT_COMPLETED",
    "3DS_CHALLENGE_INITIATED",
    "OTP_REQUESTED",
  ];
  var SDK_WAIT_MS = 10000;
  var VIEW_WAIT_MS = 15000;
  var SUBMIT_WAIT_MS = 6000;

  function detailOf(err) {
    if (!err) return "";
    if (typeof err === "string") return err;
    if (err.detail) return String(err.detail);
    if (err.message) return String(err.message);
    return String(err);
  }

  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body || {}),
    }).then(function (res) {
      return res
        .json()
        .catch(function () {
          return {};
        })
        .then(function (data) {
          if (!res.ok) {
            var err = new Error(data.detail || data.error || "HTTP " + res.status);
            err.detail = data.detail || data.error || "HTTP " + res.status;
            err.status = res.status;
            throw err;
          }
          return data;
        });
    });
  }

  function waitForSdk(timeoutMs) {
    return new Promise(function (resolve, reject) {
      if (window.myfatoorah) return resolve(window.myfatoorah);
      var waited = 0;
      var timer = setInterval(function () {
        waited += 100;
        if (window.myfatoorah) {
          clearInterval(timer);
          resolve(window.myfatoorah);
        } else if (waited >= timeoutMs) {
          clearInterval(timer);
          reject(new Error("session.js loaded but window.myfatoorah is undefined"));
        }
      }, 100);
    });
  }

  function defaultSettings(lang) {
    return {
      loader: { display: "none" },
      card: {
        language: lang,
        style: {
          hideNetworkIcons: false,
          cardHeight: "190px",
          input: {
            color: "#1C1B1F",
            fontSize: "15px",
            fontFamily: "'Outfit', 'IBM Plex Sans Arabic', sans-serif",
            inputHeight: "44px",
            inputMargin: "6px",
            borderColor: "#e6e6e8",
            borderWidth: "1px",
            borderRadius: "10px",
            backgroundColor: "#ffffff",
          },
          error: { borderColor: "#b42318", borderRadius: "10px" },
          button: { useCustomButton: true },
        },
      },
    };
  }

  function mount(opts) {
    var log = "[" + (opts.tag || "mf") + "]";
    var container = document.getElementById(opts.containerId || "mf-form");
    var box = container && container.closest(".mf-box");
    var payBtn = opts.payButton || null;
    var msg = opts.messages || {};
    var testMode = !!opts.testMode;
    if (!container || !box) {
      console.error(log, "mount", "missing .mf-box / #" + (opts.containerId || "mf-form"));
      return null;
    }

    var skeleton = box.querySelector(".mf-skeleton");
    if (!skeleton) {
      skeleton = document.createElement("div");
      skeleton.className = "mf-skeleton";
      skeleton.setAttribute("aria-hidden", "true");
      skeleton.innerHTML = "<span></span><div class=\"mf-sk-row\"><span></span><span></span></div><span class=\"mf-sk-short\"></span>";
      box.insertBefore(skeleton, container);
    }
    var idleEl = document.createElement("p");
    idleEl.className = "mf-idle";
    box.insertBefore(idleEl, container);
    var status = document.createElement("div");
    status.className = "mf-status";
    status.innerHTML =
      "<p class=\"mf-hint\" aria-live=\"polite\"></p>" +
      "<p class=\"pay-err\" role=\"alert\" hidden></p>" +
      "<button type=\"button\" class=\"mf-retry\" hidden></button>";
    box.parentNode.insertBefore(status, box.nextSibling);
    var hintEl = status.querySelector(".mf-hint");
    var errEl = status.querySelector(".pay-err");
    var retryBtn = status.querySelector(".mf-retry");
    retryBtn.textContent = msg.retry || "Try again";

    var payLabel = payBtn ? payBtn.textContent : "";
    var state = "idle";
    var run = 0;
    var ctx = null;
    var viewTimer = null;
    var submitTimer = null;

    function setState(next, text) {
      state = next;
      box.setAttribute("data-state", next);
      box.setAttribute("aria-busy", next === "loading" || next === "paying" ? "true" : "false");
      var calm = next === "idle";
      idleEl.textContent = calm ? text || "" : "";
      hintEl.textContent = calm ? "" : text || "";
      hintEl.hidden = calm || !text;
      if (next !== "error") {
        errEl.hidden = true;
        errEl.textContent = "";
        retryBtn.hidden = true;
      }
      syncPay();
    }

    function syncPay() {
      if (!payBtn) return;
      var extra = typeof opts.canPay === "function" ? opts.canPay() : true;
      payBtn.disabled = state !== "ready" || !extra;
      payBtn.textContent = state === "paying" ? msg.processing || payLabel : payLabel;
    }

    function clearTimers() {
      if (viewTimer) clearTimeout(viewTimer);
      if (submitTimer) clearTimeout(submitTimer);
      viewTimer = submitTimer = null;
    }

    function fail(step, err, generic, retryable) {
      var detail = detailOf(err);
      console.error(log, step, detail || generic, err || "");
      clearTimers();
      setState("error");
      errEl.textContent = testMode ? (generic ? generic + " — " : "") + (detail || step) : generic || detail || step;
      errEl.hidden = false;
      retryBtn.hidden = retryable === false;
    }

    function markReady() {
      if (state !== "loading") return;
      clearTimers();
      setState("ready");
    }

    function onEvent(evt) {
      var name = evt && evt.name;
      console.info(log, "event", name, evt && evt.data ? evt.data : "");
      if (name === "VIEW_READY") markReady();
      else if (name === "PAYMENT_STARTED") {
        if (submitTimer) clearTimeout(submitTimer);
        submitTimer = null;
        setState("paying");
      }
    }

    function onPayment(response) {
      var mine = run;
      console.info(log, "callback", response ? {
        isSuccess: response.isSuccess,
        paymentCompleted: response.paymentCompleted,
        paymentType: response.paymentType,
        redirectionUrl: response.redirectionUrl,
      } : response);
      if (submitTimer) clearTimeout(submitTimer);
      submitTimer = null;
      if (!response || !response.isSuccess) {
        fail("payment-callback", (response && (response.message || response.error)) || "isSuccess=false", msg.failed);
        return;
      }
      if (response.paymentCompleted === false && response.redirectionUrl) {
        window.location.href = response.redirectionUrl;
        return;
      }
      setState("paying");
      var body = opts.processBody(ctx, {
        session_id: response.sessionId || (ctx && ctx.sessionId) || "",
        payment_data: response.paymentData || "",
        redirection_url: response.redirectionUrl || "",
      });
      postJson(opts.processUrl, body)
        .then(function (out) {
          if (mine !== run) return;
          var st = (out.settlement && out.settlement.status) || "";
          console.info(log, "process", st, out);
          if (st === "paid" || st === "already" || st === "pending") {
            if (typeof opts.onPaid === "function") opts.onPaid(out, ctx);
            return;
          }
          fail("process-declined", (out.settlement && out.settlement.error) || st || "declined", msg.declined);
        })
        .catch(function (err) {
          if (mine !== run) return;
          fail("process", err, msg.failed);
        });
    }

    function start() {
      var mine = ++run;
      clearTimers();
      ctx = null;
      container.innerHTML = "";
      setState("loading", msg.loading || "");
      waitForSdk(SDK_WAIT_MS)
        .catch(function (err) {
          throw { step: "sdk", err: err, generic: msg.sdk };
        })
        .then(function () {
          return Promise.resolve(opts.getSession()).catch(function (err) {
            throw { step: "session", err: err, generic: msg.session };
          });
        })
        .then(function (session) {
          if (mine !== run) return;
          if (!session || !session.sessionId) {
            throw { step: "session", err: "No SessionId in response", generic: msg.session };
          }
          ctx = session;
          var config = {
            sessionId: session.sessionId,
            containerId: container.id,
            callback: onPayment,
            shouldHandlePaymentUrl: true,
            eventListener: onEvent,
            subscribedEvents: EVENTS,
            language: opts.language || "en",
            settings: defaultSettings(opts.language || "en"),
          };
          try {
            window.myfatoorah.init(config);
          } catch (err) {
            throw { step: "init", err: err, generic: msg.init };
          }
          console.info(log, "init", session.sessionId);
          viewTimer = setTimeout(function () {
            if (mine !== run || state !== "loading") return;
            if (container.querySelector("iframe")) {
              console.warn(log, "VIEW_READY not received; iframe present, enabling form");
              markReady();
            } else {
              fail("init-timeout", "No VIEW_READY and no iframe after " + VIEW_WAIT_MS / 1000 + "s", msg.init);
            }
          }, VIEW_WAIT_MS);
        })
        .catch(function (e) {
          if (mine !== run) return;
          if (e && e.step) fail(e.step, e.err, e.generic);
          else fail("start", e, msg.init);
        });
    }

    function idle(text) {
      ++run;
      clearTimers();
      ctx = null;
      container.innerHTML = "";
      setState("idle", text || "");
    }

    retryBtn.addEventListener("click", function () {
      console.info(log, "retry");
      start();
    });

    if (payBtn) {
      payBtn.addEventListener("click", function () {
        if (payBtn.disabled || state !== "ready") return;
        try {
          window.myfatoorah.submitCardPayment();
        } catch (err) {
          fail("submit", err, msg.failed);
          return;
        }
        payBtn.disabled = true;
        submitTimer = setTimeout(function () {
          if (state === "ready") syncPay();
        }, SUBMIT_WAIT_MS);
      });
    }

    setState("idle");
    return { start: start, idle: idle, fail: fail, sync: syncPay };
  }

  window.TringsMF = { mount: mount, postJson: postJson };
})();
