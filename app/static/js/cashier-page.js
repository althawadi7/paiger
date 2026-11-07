/* paiger cashier page v2 */
(() => {
  "use strict";
  const I = window.PAIGER_I18N || {};
  const BOOT = window.PAIGER_CASHIER || {};
  const t = (k, vars) => {
    let s = I[k] == null ? k : I[k];
    if (vars) Object.keys(vars).forEach((key) => { s = String(s).split("{" + key + "}").join(String(vars[key])); });
    return s;
  };
  const $ = (id) => document.getElementById(id);
  const el = {
    display: $("display"), hint: $("hint"), pad: $("pad"), issue: $("issue"), extra: $("extra"), extraToggle: $("extraToggle"),
    fName: $("fName"), fNote: $("fNote"), listPrep: $("listPrep"), listCalled: $("listCalled"), cPrep: $("cPrep"), cCalled: $("cCalled"),
    chips: $("chips"), qrOverlay: $("qrOverlay"), qrBox: $("qrBox"), qrNo: $("qrNo"), qrWait: $("qrWait"),
    pop: $("pop"), toast: $("toast"), toastText: $("toastText"), toastUndo: $("toastUndo"),
    menu: $("menu"), menuBtn: $("menuBtn"), live: $("live"), stToday: $("stToday"), stAvg: $("stAvg"),
  };
  const AVG = Number(BOOT.avgPrep) || 5;
  let orders = [];
  const scannedIds = new Set();
  let value = "";
  let soundOn = true;
  const storeKey = "paiger." + String(BOOT.slug || BOOT.restaurantId || "cashier") + ".cashierSound";
  try { if (localStorage.getItem(storeKey) === "0") soundOn = false; } catch (_) {}
  let todayCount = 0;
  let calledN = 0;
  let calledSum = 0;

  const active = () => orders.filter((o) => o.status !== "done");
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmtElapsed = (ms) => {
    const s = Math.max(0, Math.floor(ms / 1000)), m = Math.floor(s / 60);
    return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}` : `${m}:${String(s % 60).padStart(2, "0")}`;
  };
  const ago = (ms) => { const m = Math.floor(ms / 60e3); return m < 1 ? `${Math.floor(ms / 1000)} ${t("sec")}` : `${m} ${t("min")}`; };
  function zonedParts(date, tz) {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: tz, hourCycle: "h23",
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit"
    }).formatToParts(date);
    const map = {};
    parts.forEach((p) => { if (p.type !== "literal") map[p.type] = Number(p.value); });
    return map;
  }
  function businessStartMs(now) {
    const moment = now || new Date();
    const tz = BOOT.timeZone || "Asia/Bahrain";
    const hour = Number(BOOT.dayStartHour);
    const startHour = Number.isFinite(hour) ? hour : 4;
    const wall = zonedParts(moment, tz);
    const offset = Date.UTC(wall.year, wall.month - 1, wall.day, wall.hour, wall.minute, wall.second) - moment.getTime();
    let day = Date.UTC(wall.year, wall.month - 1, wall.day);
    if (wall.hour < startHour) day -= 86400000;
    return day + startHour * 3600000 - offset;
  }
  function inDay(ms) { return ms != null && ms >= businessStartMs(); }
  function parseStamp(iso) {
    if (!iso) return null;
    if (/[zZ]|[+-]\d\d:\d\d$/.test(iso)) return Date.parse(iso);
    if (BOOT.tz) return Date.parse(String(iso).replace(" ", "T") + BOOT.tz);
    return Date.parse(String(iso).replace(" ", "T"));
  }
  function norm(raw) {
    if (!raw) return null;
        return {
      id: Number(raw.id),
      number: String(raw.order_number ?? raw.number ?? ""),
      status: raw.status,
      created: parseStamp(raw.created_at) || Date.now(),
      called: parseStamp(raw.called_at),
      done: parseStamp(raw.completed_at),
      scanned: !!raw.scanned || scannedIds.has(Number(raw.id)),
      calls: Number(raw.call_count || raw.calls || 0),
      name: raw.customer_name || raw.name || "",
      note: raw.notes || raw.note || "",
      token: raw.token || "",
      restaurant_id: raw.restaurant_id == null || raw.restaurant_id === "" ? null : Number(raw.restaurant_id),
    };
  }
  function mine(o) { return !!(o && o.restaurant_id != null && Number(o.restaurant_id) === Number(BOOT.restaurantId)); }
  function recompute() {
    todayCount = 0; calledN = 0; calledSum = 0;
    orders.forEach((o) => {
      if (inDay(o.created)) todayCount++;
      if (o.called && inDay(o.called)) {
        calledN++;
        calledSum += Math.max(0, (o.called - o.created) / 1000);
      }
    });
    if (orders.length >= 150 && Number(BOOT.todayCount) > todayCount) todayCount = Number(BOOT.todayCount);
    if (orders.length >= 150 && Number(BOOT.calledCount) > calledN && Number(BOOT.calledCount) >= 3) {
      calledN = Number(BOOT.calledCount);
      calledSum = (BOOT.avgSeconds === "" || BOOT.avgSeconds == null ? 0 : Number(BOOT.avgSeconds)) * calledN;
    }
  }

  function duplicate() { return value && active().find((o) => o.number === value); }
  function renderDisplay() {
    el.display.innerHTML = value ? `<span>${esc(value)}</span><span class="caret"></span>` : `<span class="ph">${esc(t("typeNumber"))}</span>`;
    const dup = duplicate();
    el.display.classList.toggle("has", !!value && !dup);
    el.display.classList.toggle("dup", !!dup);
    el.issue.disabled = !value || !!dup;
    if (dup) {
      el.hint.className = "hint warn";
      el.hint.innerHTML = `${esc(t("dup", { n: dup.number }))} ${dup.status === "waiting" ? `<button type="button" id="dupRing">${esc(t("dupRing"))}</button>` : ""}`;
      const b = $("dupRing");
      if (b) b.addEventListener("click", () => { ring(dup.id); value = ""; renderDisplay(); });
    } else { el.hint.className = "hint"; el.hint.textContent = ""; }
  }
  function press(k) {
    if (k === "c") value = "";
    else if (k === "b") value = value.slice(0, -1);
    else if (/^\d$/.test(k) && value.length < 5) value += k;
    renderDisplay();
  }
  el.pad.addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) press(b.dataset.k); });
  el.extraToggle.addEventListener("click", () => {
    el.extra.hidden = !el.extra.hidden;
    el.extraToggle.textContent = t(el.extra.hidden ? "addExtra" : "hideExtra");
    if (!el.extra.hidden) el.fName.focus();
  });
  const AR_DIGITS = "٠١٢٣٤٥٦٧٨٩";
  document.addEventListener("keydown", (e) => {
    if (!el.qrOverlay.hidden) { if (e.key === "Enter" || e.key === "Escape") { e.preventDefault(); closeQr(); } return; }
    if (e.target.matches("input")) { if (e.key === "Enter") { e.preventDefault(); issue(); } return; }
    const k = AR_DIGITS.includes(e.key) ? String(AR_DIGITS.indexOf(e.key)) : e.key;
    if (/^\d$/.test(k)) { e.preventDefault(); press(k); }
    else if (e.key === "Backspace") { e.preventDefault(); press("b"); }
    else if (e.key === "Escape") press("c");
    else if (e.key === "Enter") { e.preventDefault(); issue(); }
  });
  el.issue.addEventListener("click", issue);

  function cardHTML(o) {
    const elapsed = Date.now() - o.created;
    const late = o.status === "waiting" && elapsed > AVG * 60e3;
    const who = [o.name && esc(o.name), o.note && `<span>${esc(o.note)}</span>`].filter(Boolean).join(" ");
    let meta = "", actions = "";
    if (o.status === "waiting") {
      meta = `<span class="timer" data-since="${o.created}">${fmtElapsed(elapsed)}</span>
        <span class="late-tag">${esc(t("late"))}</span>
        <span class="scan ${o.scanned ? "scan-yes" : "scan-no"}"><svg><use href="#${o.scanned ? "i-phone-ok" : "i-phone-no"}"/></svg>${esc(t(o.scanned ? "scanned" : "notScanned"))}</span>`;
      actions = `<button type="button" class="btn btn-ready" data-act="ring">${esc(t("ready"))}</button>`;
    } else if (o.status === "ringing") {
      const since = o.called || o.created;
      meta = `<span class="state state-ring"><i></i>${esc(t("ringing"))}</span><span class="timer" data-since="${since}" data-ago="1">${esc(t("calledAgo", { m: ago(Date.now() - since) }))}</span>${o.calls > 1 ? `<span>${esc(t("rang", { n: o.calls }))}</span>` : ""}`;
      actions = `<button type="button" class="btn btn-line" data-act="ring">${esc(t("ringAgain"))}</button><button type="button" class="btn btn-ink" data-act="pick">${esc(t("picked"))}</button>`;
    } else if (o.status === "onway") {
      const since = o.called || o.created;
      meta = `<span class="state state-way"><svg><use href="#i-walk"/></svg>${esc(t("onway"))}</span><span class="timer" data-since="${since}" data-ago="1">${esc(t("calledAgo", { m: ago(Date.now() - since) }))}</span>`;
      actions = `<button type="button" class="btn btn-ink" data-act="pick">${esc(t("picked"))}</button>`;
    }
    return `<article class="card ${o.status}${late ? " late" : ""}" data-id="${o.id}">
      <div class="no num-font">${esc(o.number)}</div>
      <div class="info"><div class="meta">${meta}</div><div class="who">${who}</div></div>
      <button type="button" class="more" data-act="more" aria-label="${esc(t("more"))}"><svg><use href="#i-dots"/></svg></button>
      <div class="actions">${actions}</div>
    </article>`;
  }
  function renderStats() {
    el.stToday.textContent = todayCount;
    const minutes = calledN >= 3 ? Math.max(0, Math.round(calledSum / calledN / 60)) : AVG;
    el.stAvg.textContent = minutes;
    $("stAvgUnit").textContent = t("minLong");
  }
  function render() {
    const prep = orders.filter((o) => o.status === "waiting").sort((a, b) => a.created - b.created);
    const called = orders.filter((o) => o.status === "ringing" || o.status === "onway").sort((a, b) => (a.status === "onway") - (b.status === "onway") || (a.called || 0) - (b.called || 0));
    const done = orders.filter((o) => o.status === "done" && inDay(o.done)).sort((a, b) => (b.done || 0) - (a.done || 0)).slice(0, 30);
    el.listPrep.innerHTML = prep.length ? prep.map(cardHTML).join("") : `<div class="empty"><b>${esc(t("emptyPrepT"))}</b>${esc(t("emptyPrepP"))}</div>`;
    el.listCalled.innerHTML = called.length ? called.map(cardHTML).join("") : `<div class="empty"><b>${esc(t("emptyCalledT"))}</b>${esc(t("emptyCalledP"))}</div>`;
    el.cPrep.textContent = prep.length;
    el.cCalled.textContent = called.length;
    el.chips.innerHTML = done.length ? done.map((o) => `<button type="button" class="chip" data-id="${o.id}" title="${esc(t("returnIt"))}"><b class="num-font">${esc(o.number)}</b><span>${ago(Date.now() - (o.done || Date.now()))}</span><svg><use href="#i-undo"/></svg></button>`).join("") : `<span class="none">${esc(t("noneDone"))}</span>`;
    recompute();
    renderStats();
  }
  let openDay = businessStartMs();
  setInterval(() => {
    const next = businessStartMs();
    if (next === openDay) return;
    openDay = next;
    orders = orders.filter((o) => o.status !== "done" || inDay(o.done));
    render();
  }, 60000);
  setInterval(() => {
    document.querySelectorAll(".timer[data-since]").forEach((n) => {
      const ms = Date.now() - Number(n.dataset.since);
      n.textContent = n.dataset.ago ? t("calledAgo", { m: ago(ms) }) : fmtElapsed(ms);
      const card = n.closest(".card");
      if (!n.dataset.ago && card) card.classList.toggle("late", ms > AVG * 60e3);
    });
  }, 1000);

  function upsert(raw, chimeOnway) {
    const o = norm(raw);
    if (!o || !mine(o)) return;
    if (pending.has(o.id)) return;
    const prev = orders.find((x) => x.id === o.id);
    const prevStatus = prev && prev.status;
    if (prev) {
      const token = prev.token;
      Object.assign(prev, o);
      if (token) prev.token = token;
    } else orders.push(o);
    if (chimeOnway && o.status === "onway" && prevStatus !== "onway") chime();
    render();
  }
  function bounceLogin() {
    location.reload();
  }
  async function reload() {
    const res = await fetch("/api/orders?board=1&restaurant_id=" + encodeURIComponent(BOOT.restaurantId), { credentials: "same-origin" });
    if (res.status === 401) { bounceLogin(); return; }
    if (!res.ok) return;
    const rows = await res.json();
    const fresh = (Array.isArray(rows) ? rows : []).map(norm).filter((o) => o && mine(o) && !pending.has(o.id));
    const locals = [];
    pending.forEach((job, id) => {
      const cur = orders.find((o) => o.id === id);
      if (job.kind !== "delete" && cur) locals.push(cur);
    });
    orders = fresh.concat(locals.filter((o) => !fresh.some((f) => f.id === o.id)));
    render();
  }

  async function issue() {
    if (!value || duplicate() || el.issue.disabled) return;
    const number = value, name = el.fName.value.trim(), note = el.fNote.value.trim();
    el.issue.disabled = true;
    const res = await fetch("/api/orders", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ order_number: number, customer_name: name, notes: note, restaurant_id: Number(BOOT.restaurantId) }),
    });
    if (res.status === 401) { bounceLogin(); return; }
    if (res.status === 429) { toast(t("tooFast")); renderDisplay(); return; }
    if (res.status === 402) { toast(t("needPoints")); renderDisplay(); return; }
    if (res.status === 403) { toast(t("paused")); renderDisplay(); return; }
    if (res.status === 409) { await reload(); value = number; renderDisplay(); return; }
    if (!res.ok) { renderDisplay(); return; }
    const raw = await res.json();
    const created = norm(raw);
    if (!created || !mine(created) || !created.token) { renderDisplay(); return; }
    value = ""; el.fName.value = ""; el.fNote.value = ""; el.extra.hidden = true; el.extraToggle.textContent = t("addExtra");
    upsert(raw, false);
    renderDisplay();
    openQr(created);
  }
  async function ring(id) {
    const res = await fetch(`/api/orders/${id}/ring`, { method: "POST", credentials: "same-origin" });
    if (res.status === 401) { bounceLogin(); return; }
    if (res.ok) upsert(await res.json(), false);
    else await reload();
  }
  async function resetOrder(id) {
    const o = orders.find((x) => x.id === id); if (!o || !inDay(o.done)) return;
    const snapshot = Object.assign({}, o);
    o.status = "waiting";
    render();
    const res = await fetch(`/api/orders/${id}/waiting`, { method: "POST", credentials: "same-origin" });
    if (res.ok) upsert(await res.json(), false);
    toast(t("toastReturned", { n: snapshot.number }), async () => {
      const back = await fetch(`/api/orders/${id}/done`, { method: "POST", credentials: "same-origin" });
      if (back.ok) upsert(await back.json(), false);
      else { Object.assign(o, snapshot); render(); }
    });
  }

  const pending = new Map();
  const leaving = new Map();
  let toastTimer = null, undoFn = null;
  function toast(msg, undo) {
    el.toastText.textContent = msg;
    undoFn = undo || null;
    el.toastUndo.hidden = !undo;
    el.toast.hidden = true; void el.toast.offsetWidth; el.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.toast.hidden = true; undoFn = null; }, 5000);
  }
  el.toastUndo.addEventListener("click", () => { if (undoFn) undoFn(); undoFn = null; el.toast.hidden = true; });
  function leave(id, then) {
    const card = document.querySelector(`.card[data-id="${id}"]`);
    if (!card) return then();
    card.classList.add("leaving");
    const timer = setTimeout(() => { leaving.delete(id); then(); }, 260);
    leaving.set(id, timer);
  }
  function cancelLeave(id) {
    const timer = leaving.get(id);
    if (!timer) return;
    clearTimeout(timer);
    leaving.delete(id);
  }
  function schedule(id, kind, snapshot) {
    const prev = pending.get(id);
    if (prev) clearTimeout(prev.timer);
    const timer = setTimeout(() => { flush(id); }, 5000);
    pending.set(id, { kind, snapshot, timer });
  }
  async function flush(id) {
    const job = pending.get(id);
    if (!job) return;
    pending.delete(id);
    const url = job.kind === "delete" ? `/api/orders/${id}` : `/api/orders/${id}/done`;
    const method = job.kind === "delete" ? "DELETE" : "POST";
    try {
      const res = await fetch(url, { method, credentials: "same-origin" });
      if (!res.ok) { await reload(); return; }
      if (job.kind === "done") upsert(await res.json(), false);
      else { orders = orders.filter((o) => o.id !== id); render(); }
    } catch (_) { await reload(); }
  }
  function undoPending(id) {
    const job = pending.get(id);
    if (!job) return false;
    clearTimeout(job.timer);
    pending.delete(id);
    cancelLeave(id);
    if (job.kind === "delete") {
      if (!orders.some((o) => o.id === id)) orders.push(job.snapshot);
    } else {
      const o = orders.find((x) => x.id === id);
      if (o) Object.assign(o, job.snapshot);
      else orders.push(job.snapshot);
    }
    render();
    return true;
  }
  function pickUp(id) {
    const o = orders.find((x) => x.id === id); if (!o) return;
    const snapshot = Object.assign({}, o);
    leave(id, () => { o.status = "done"; o.done = Date.now(); render(); });
    schedule(id, "done", snapshot);
    toast(t("toastPicked", { n: o.number }), () => undoPending(id));
  }
  function remove(id) {
    const i = orders.findIndex((x) => x.id === id); if (i < 0) return;
    const snapshot = Object.assign({}, orders[i]);
    leave(id, () => { orders = orders.filter((x) => x.id !== id); render(); });
    schedule(id, "delete", snapshot);
    toast(t("toastDeleted", { n: snapshot.number }), () => undoPending(id));
  }
  addEventListener("pagehide", () => {
    pending.forEach((job, id) => {
      const url = job.kind === "delete" ? `/api/orders/${id}` : `/api/orders/${id}/done`;
      fetch(url, { method: job.kind === "delete" ? "DELETE" : "POST", credentials: "same-origin", keepalive: true });
      clearTimeout(job.timer);
    });
    pending.clear();
  });

  document.querySelector(".board").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (chip) return resetOrder(Number(chip.dataset.id));
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const id = Number(b.closest(".card").dataset.id);
    if (b.dataset.act === "ring") ring(id);
    if (b.dataset.act === "pick") pickUp(id);
    if (b.dataset.act === "more") openPop(b, id);
  });

  let popId = null;
  function openPop(anchor, id) {
    popId = id;
    const r = anchor.getBoundingClientRect();
    el.pop.hidden = false;
    const w = el.pop.offsetWidth;
    const left = document.documentElement.dir === "rtl" ? r.left : r.right - w;
    el.pop.style.left = Math.max(8, Math.min(left, innerWidth - w - 8)) + "px";
    el.pop.style.top = Math.min(r.bottom + 6, innerHeight - el.pop.offsetHeight - 8) + "px";
  }
  el.pop.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    const o = orders.find((x) => x.id === popId);
    el.pop.hidden = true;
    if (!o) return;
    if (b.dataset.act === "qr") openQr(o);
    if (b.dataset.act === "delete") remove(o.id);
  });
  document.addEventListener("click", (e) => {
    if (!el.pop.hidden && !e.target.closest("#pop") && !e.target.closest('[data-act="more"]')) el.pop.hidden = true;
    if (!el.menu.hidden && !e.target.closest("#menu") && !e.target.closest("#menuBtn")) { el.menu.hidden = true; el.menuBtn.setAttribute("aria-expanded", "false"); }
  });
  el.menuBtn.addEventListener("click", () => { el.menu.hidden = !el.menu.hidden; el.menuBtn.setAttribute("aria-expanded", String(!el.menu.hidden)); });
  $("soundBtn").textContent = t(soundOn ? "mSoundOn" : "mSoundOff");
  $("soundBtn").addEventListener("click", () => {
    soundOn = !soundOn;
    try { localStorage.setItem(storeKey, soundOn ? "1" : "0"); } catch (_) {}
    $("soundBtn").textContent = t(soundOn ? "mSoundOn" : "mSoundOff");
  });

  let qrOrder = null, qrTimer = null;
  function openQr(o) {
    if (!o || !o.token || !mine(o)) return;
    qrOrder = o;
    var pagerUrl = String(BOOT.baseUrl || "").replace(/\/$/, "") + "/pager/" + o.token;
    el.qrBox.innerHTML = `<img alt="QR code" src="/api/qr/${encodeURIComponent(o.token)}?host=${encodeURIComponent(pagerUrl)}">`;
    el.qrNo.textContent = o.number;
    el.qrWait.hidden = false;
    el.qrWait.innerHTML = `<span class="spinner"></span><span>${esc(t("waitScan"))}</span>`;
    el.qrOverlay.hidden = false;
    $("qrDone").focus();
  }
  function qrScanned() {
    if (el.qrBox.querySelector(".scanned")) return;
    el.qrBox.insertAdjacentHTML("beforeend", `<div class="scanned"><svg><use href="#i-check-big"/></svg>${esc(t("scannedOk"))}</div>`);
    el.qrWait.innerHTML = "";
    clearTimeout(qrTimer);
    qrTimer = setTimeout(closeQr, 1000);
  }
  function closeQr() { clearTimeout(qrTimer); el.qrOverlay.hidden = true; qrOrder = null; }
  $("qrDone").addEventListener("click", closeQr);
  $("qrPrint").addEventListener("click", () => window.print());
  el.qrOverlay.addEventListener("click", (e) => { if (e.target === el.qrOverlay) closeQr(); });

  let ctx = null;
  function chime() {
    if (!soundOn) return;
    try {
      ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
      [880, 1320].forEach((f, i) => {
        const o = ctx.createOscillator(), g = ctx.createGain(), s = ctx.currentTime + i * 0.14;
        o.type = "sine"; o.frequency.value = f; o.connect(g); g.connect(ctx.destination);
        g.gain.setValueAtTime(0, s); g.gain.linearRampToValueAtTime(0.12, s + 0.01); g.gain.exponentialRampToValueAtTime(0.0001, s + 0.5);
        o.start(s); o.stop(s + 0.55);
      });
    } catch (_) {}
  }
  function setLive(ok) {
    el.live.classList.toggle("off", !ok);
    el.live.querySelector("span").textContent = t(ok ? "live" : "offline");
  }

  let socket = null, retry = 1000, opened = false, generation = 0;
  const reloadKey = "paiger." + String(BOOT.slug || BOOT.restaurantId || "cashier") + ".wsReloadAt";
  function eventRestaurant(msg) {
    const raw = msg.restaurant_id != null ? msg.restaurant_id : (msg.order && msg.order.restaurant_id);
    if (raw == null || raw === "") return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  }
  function showLost() {
    setLive(false);
    const box = $("wsLost");
    if (!box) return;
    $("wsLostText").textContent = t("wsLost");
    $("wsLostRefresh").textContent = t("wsRefresh");
    box.hidden = false;
  }
  function recover() {
    let last = 0;
    try { last = Number(sessionStorage.getItem(reloadKey) || 0); } catch (_) {}
    if (!last || Date.now() - last > 60000) {
      try { sessionStorage.setItem(reloadKey, String(Date.now())); } catch (_) {}
      location.reload();
      return;
    }
    showLost();
  }
  function rejectedClose(ev, didOpen) {
    const code = Number(ev.code);
    if (code === 4401 || code === 4403 || code === 4404) return true;
    return !didOpen && (code === 1006 || code === 1002 || code === 1008 || code === 1005);
  }
  function connect(path) {
    if (!path && !BOOT.slug) return;
    const mineGen = ++generation;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const target = path || ("/ws/cashier/" + encodeURIComponent(BOOT.slug));
    const current = new WebSocket(`${proto}://${location.host}${target}`);
    socket = current;
    let didOpen = false;
    current.addEventListener("open", () => {
      if (mineGen !== generation) return;
      didOpen = true;
      setLive(true);
      const lost = $("wsLost");
      if (lost) lost.hidden = true;
      retry = 1000;
      if (opened) reload();
      opened = true;
    });
    current.addEventListener("message", (ev) => {
      if (mineGen !== generation) return;
      let msg; try { msg = JSON.parse(ev.data); } catch (_) { return; }
      if (msg.event === "hello") {
        if (msg.build && String(msg.build) !== String(BOOT.build || "")) recover();
        return;
      }
      const rid = eventRestaurant(msg);
      if (rid == null || rid !== Number(BOOT.restaurantId)) return;
      if (msg.event === "customer_connected") {
        const id = Number(msg.order_id);
        if (!Number.isFinite(id)) return;
        scannedIds.add(id);
        const o = orders.find((x) => x.id === id && mine(x));
        if (o) o.scanned = true;
        render();
        if (qrOrder && mine(qrOrder) && qrOrder.id === id) qrScanned();
      } else if (msg.event === "order_deleted") {
        const id = Number(msg.order_id);
        if (!pending.has(id)) { orders = orders.filter((o) => o.id !== id); render(); }
      } else if (msg.event === "customer_onway") upsert(msg.order, true);
      else if (msg.order) upsert(msg.order, false);
    });
    current.addEventListener("close", (ev) => {
      if (mineGen !== generation) return;
      if (rejectedClose(ev, didOpen)) { recover(); return; }
      setLive(false);
      setTimeout(() => { if (mineGen === generation) connect(); }, retry);
      retry = Math.min(retry * 2, 10000);
    });
  }
  $("wsLostRefresh").addEventListener("click", () => location.reload());
  window.paigerCashier = {
    connect: connect,
    reloadKey: reloadKey,
    get socket() { return socket; },
  };

  $("soundBtn").textContent = t(soundOn ? "mSoundOn" : "mSoundOff");
  reload().then(connect);
})();
