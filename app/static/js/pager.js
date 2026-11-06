/**
 * Web Pager - Ultra Client Controller
 * Background Lock-Screen Widget & Resilient Audio Engine
 */

class PagerClient {
  constructor(token, initialOrder) {
    this.token = token;
    this.order = initialOrder;
    this.isArmed = false;
    this.isRinging = false;
    this.isAcknowledged = false;
    this.vibrateInterval = null;
    this.ws = null;

    this.initDOMElements();
    this.bindEvents();
    this.initAudioElements();
    this.updateUI(this.order);
    this.connectWebSocket();
    this.startPollingFallback();

    // Check if previously armed in this browser session
    if (sessionStorage.getItem("pager_armed_" + this.token)) {
      this.armDevice(true);
    }
  }

  initDOMElements() {
    this.bodyEl = document.getElementById("pagerBody");
    this.orderNumEl = document.getElementById("orderNumber");
    this.indicatorEl = document.getElementById("deviceIndicator");
    this.statusTitleEl = document.getElementById("statusTitle");
    this.statusDescEl = document.getElementById("statusDesc");
    this.connPill = document.getElementById("connPill");
    this.armSheet = document.getElementById("armSheet");
    this.armBtn = document.getElementById("armBtn");
    this.claimBtn = document.getElementById("claimBtn");
    this.testSoundBtn = document.getElementById("testSoundBtn");

    // Timeline steps
    this.step1 = document.getElementById("step1");
    this.step2 = document.getElementById("step2");
    this.step3 = document.getElementById("step3");

    // Chips
    this.chipAudio = document.getElementById("chipAudio");
    this.chipVibrate = document.getElementById("chipVibrate");
    this.chipLock = document.getElementById("chipLock");
  }

  initAudioElements() {
    // Hidden background audio element for Lock-Screen media session
    this.audioEl = document.createElement("audio");
    this.audioEl.setAttribute("playsinline", "true");
    this.audioEl.setAttribute("webkit-playsinline", "true");
    this.audioEl.loop = true;
    this.audioEl.src = "/static/audio/silent.wav";
    document.body.appendChild(this.audioEl);

    // One-shot chime audio
    this.chimeEl = new Audio("/static/audio/chime.wav");
  }

  bindEvents() {
    this.armBtn.addEventListener("click", () => this.armDevice(false));
    
    if (this.claimBtn) {
      this.claimBtn.addEventListener("click", () => this.acknowledgeOrder());
    }

    if (this.testSoundBtn) {
      this.testSoundBtn.addEventListener("click", () => this.testBuzzerSound());
    }
  }

  /**
   * Arm Device: Crucial for iOS Safari & Android
   * Opens background audio session, sets Lock-Screen widget, and unlocks haptics
   */
  async armDevice(silentArm = false) {
    try {
      this.isArmed = true;
      sessionStorage.setItem("pager_armed_" + this.token, "true");

      // 1. Start audio playback on user gesture to register lock-screen media session
      try {
        this.audioEl.volume = 0.05;
        this.audioEl.src = "/static/audio/silent.wav";
        await this.audioEl.play();
        this.chipAudio.classList.add("ready");
      } catch (e) {
        console.warn("Audio element play error:", e);
      }

      // 2. Setup iOS & Android Lock-Screen Media Widget
      this.updateMediaSession(false);

      // 3. Test Vibration
      if ("vibrate" in navigator) {
        try {
          navigator.vibrate([100, 50, 100]);
          this.chipVibrate.classList.add("ready");
        } catch (e) {}
      }

      // 4. Request Notifications (if supported and in secure context)
      if (window.isSecureContext && "Notification" in window) {
        try {
          if (Notification.permission !== "granted") {
            Notification.requestPermission().catch(() => {});
          }
        } catch (e) {}
      }

      // 5. Screen Wake Lock (if supported)
      if (window.isSecureContext && "wakeLock" in navigator) {
        try {
          await navigator.wakeLock.request("screen");
          this.chipLock.classList.add("ready");
        } catch (e) {}
      }

      // 6. Play friendly welcome chime if not silent arm
      if (!silentArm) {
        try {
          this.chimeEl.play().catch(() => {});
        } catch (e) {}
      }

      // Close modal
      if (this.armSheet) {
        this.armSheet.style.display = "none";
      }

      // If already READY, trigger ringing immediately
      if (this.order && this.order.status === "READY") {
        this.triggerReadyAlert();
      }
    } catch (err) {
      console.error("Arming caught error:", err);
      // Ensure modal is closed regardless
      if (this.armSheet) this.armSheet.style.display = "none";
    }
  }

  /**
   * Updates Lock Screen Widget info (MediaSession API)
   * This displays on iPhone lock screen and Android notification tray
   */
  updateMediaSession(isReady) {
    if (!("mediaSession" in navigator)) return;

    try {
      const orderNum = this.order ? this.order.order_number : "";
      if (isReady) {
        navigator.mediaSession.metadata = new MediaMetadata({
          title: ` طلبك #${orderNum} جاهز للاستلام الآن`,
          artist: "مطعم البرجر الذهبي",
          album: "توجه إلى كاونتر الاستلام لأخذ وجبتك",
          artwork: [
            { src: "/static/img/icon-512.png", sizes: "512x512", type: "image/png" }
          ]
        });
      } else {
        navigator.mediaSession.metadata = new MediaMetadata({
          title: `⏳ طلبك #${orderNum} - قيد التحضير في المطبخ`,
          artist: "مطعم البرجر الذهبي",
          album: "سننبهك فور جهوزية الطلب حتى لو هاتفك مقفل",
          artwork: [
            { src: "/static/img/icon-512.png", sizes: "512x512", type: "image/png" }
          ]
        });
      }

      // Handle media keys so the lock screen widget stays interactive
      navigator.mediaSession.setActionHandler("play", () => {});
      navigator.mediaSession.setActionHandler("pause", () => this.acknowledgeOrder());
      navigator.mediaSession.setActionHandler("stop", () => this.acknowledgeOrder());
    } catch (e) {
      console.warn("MediaSession update error:", e);
    }
  }

  /**
   * Start Ringing & Vibration Alarm
   */
  triggerReadyAlert() {
    if (this.isAcknowledged) return;
    this.isRinging = true;

    // 1. Play loud buzzer audio on loop (works in background & locked screen)
    try {
      this.audioEl.src = "/static/audio/buzzer.wav";
      this.audioEl.volume = 1.0;
      this.audioEl.loop = true;
      this.audioEl.play().catch(err => {
        console.warn("Buzzer play attempt:", err);
      });
    } catch (e) {
      console.warn("Audio switch error:", e);
    }

    // 2. Update Lock Screen Widget
    this.updateMediaSession(true);

    // 3. Continuous vibration loop (Android)
    clearInterval(this.vibrateInterval);
    if ("vibrate" in navigator) {
      navigator.vibrate([400, 200, 400, 200, 800]);
      this.vibrateInterval = setInterval(() => {
        if (this.isRinging) {
          navigator.vibrate([400, 200, 400, 200, 800]);
        }
      }, 2000);
    }

    // 4. Notification API fallback
    if (window.isSecureContext && "Notification" in window && Notification.permission === "granted") {
      try {
        new Notification(` طلبك #${this.order.order_number} جاهز للاستلام`, {
          body: "يرجى التوجه إلى كاونتر الاستلام لاستلام وجبتك",
          icon: "/static/img/icon-512.png",
          requireInteraction: true
        });
      } catch (e) {}
    }
  }

  stopAlert() {
    this.isRinging = false;
    clearInterval(this.vibrateInterval);
    try {
      this.audioEl.pause();
      this.audioEl.currentTime = 0;
    } catch (e) {}
  }

  acknowledgeOrder() {
    this.isAcknowledged = true;
    this.stopAlert();
    this.claimBtn.style.display = "none";
    this.statusTitleEl.textContent = " تم تأكيد الاستلام";
    this.statusDescEl.textContent = "شكراً لك نحن بانتظارك عند الكاونتر.";

    // Send acknowledge to server
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ action: "acknowledge" }));
    }
  }

  testBuzzerSound() {
    // User triggered test
    const testAudio = new Audio("/static/audio/buzzer.wav");
    testAudio.volume = 1.0;
    testAudio.play().catch(e => alert("يرجى الضغط على زر تفعيل البيجر أولاً لتفعيل الصوت."));
    if ("vibrate" in navigator) {
      navigator.vibrate([300, 100, 300]);
    }
  }

  updateUI(order) {
    this.order = order;
    this.orderNumEl.textContent = order.order_number;

    this.bodyEl.classList.remove("state-waiting", "state-ready", "state-completed");
    this.step1.className = "step-node active";
    this.step2.className = "step-node";
    this.step3.className = "step-node";

    if (order.status === "WAITING") {
      this.stopAlert();
      this.isAcknowledged = false;
      this.bodyEl.classList.add("state-waiting");
      this.step2.className = "step-node active";
      this.indicatorEl.textContent = "قيد التحضير ⏳";
      this.statusTitleEl.textContent = "طلبك قيد التحضير في المطبخ";
      this.statusDescEl.textContent = "سيهتز هاتفك ويرن البيجر بقوة فور جهوزية الوجبة حتى لو أغلقت الشاشة.";
      if (this.claimBtn) this.claimBtn.style.display = "none";
      this.updateMediaSession(false);
    } 
    else if (order.status === "READY") {
      this.bodyEl.classList.add("state-ready");
      this.step2.className = "step-node active";
      this.step3.className = "step-node active ready-step";
      this.indicatorEl.textContent = "جاهز للاستلام ";
      this.statusTitleEl.textContent = " وجبتك جاهزة للاستلام الآن";
      this.statusDescEl.textContent = "تفضل إلى كاونتر الاستلام لاستلام طلبك.";
      if (this.claimBtn && !this.isAcknowledged) {
        this.claimBtn.style.display = "flex";
      }
      this.triggerReadyAlert();
    } 
    else if (order.status === "COMPLETED") {
      this.stopAlert();
      this.bodyEl.classList.add("state-completed");
      this.step2.className = "step-node active";
      this.step3.className = "step-node active";
      this.indicatorEl.textContent = "تم الاستلام ";
      this.statusTitleEl.textContent = "تم استلام الطلب بالعافية";
      this.statusDescEl.textContent = "شكراً لزيارتك لمطعمنا، نتمنى لك وجبة شهية.";
      if (this.claimBtn) this.claimBtn.style.display = "none";
    }
  }

  connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/pager/${this.token}`;

    try {
      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = () => {
        this.connPill.innerHTML = '<span class="status-dot"></span> متصل لحظياً';
        this.connPill.style.color = "#38bdf8";
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.order) {
            this.updateUI(data.order);
          }
          if (data.event === "order_repeated_call") {
            this.isAcknowledged = false;
            this.triggerReadyAlert();
          }
        } catch (e) {
          console.error("WS parse error:", e);
        }
      };

      this.ws.onclose = () => {
        this.connPill.innerHTML = ' جاري الاتصال...';
        this.connPill.style.color = "#f59e0b";
        setTimeout(() => this.connectWebSocket(), 3000);
      };

      this.ws.onerror = () => {
        this.ws.close();
      };
    } catch (e) {
      setTimeout(() => this.connectWebSocket(), 4000);
    }
  }

  // Fallback Polling every 3 seconds to guarantee receipt even if WebSocket drops
  startPollingFallback() {
    setInterval(async () => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        try {
          const res = await fetch("/api/orders");
          if (res.ok) {
            const list = await res.json();
            const curr = list.find(o => o.token === this.token);
            if (curr && (!this.order || curr.status !== this.order.status)) {
              this.updateUI(curr);
            }
          }
        } catch (e) {}
      }
    }, 3000);
  }
}
