/**
 * Cashier Dashboard Controller - Luxury Dark UI
 */

class CashierController {
  constructor(baseUrl) {
    this.baseUrl = baseUrl;
    this.orders = [];
    this.ws = null;
    this.currentQrToken = null;

    this.initDOMElements();
    this.bindEvents();
    this.loadOrders();
    this.connectWebSocket();
  }

  initDOMElements() {
    this.orderForm = document.getElementById("orderForm");
    this.orderNumInput = document.getElementById("orderNumInput");
    this.custNameInput = document.getElementById("custNameInput");
    this.notesInput = document.getElementById("notesInput");
    this.ordersList = document.getElementById("ordersList");

    // Stats
    this.statWaiting = document.getElementById("statWaiting");
    this.statReady = document.getElementById("statReady");
    this.statDone = document.getElementById("statDone");

    // Modal
    this.qrModal = document.getElementById("qrModal");
    this.modalQrImg = document.getElementById("modalQrImg");
    this.modalOrderNum = document.getElementById("modalOrderNum");
    this.modalLink = document.getElementById("modalLink");
    this.closeModalBtn = document.getElementById("closeModalBtn");
    this.copyLinkBtn = document.getElementById("copyLinkBtn");
    this.openTestBtn = document.getElementById("openTestBtn");
  }

  bindEvents() {
    this.orderForm.addEventListener("submit", (e) => this.handleCreateOrder(e));
    this.closeModalBtn.addEventListener("click", () => this.closeQrModal());
    this.copyLinkBtn.addEventListener("click", () => this.copyCurrentLink());
    this.openTestBtn.addEventListener("click", () => this.openCurrentTestPager());

    // Close modal on overlay click
    this.qrModal.addEventListener("click", (e) => {
      if (e.target === this.qrModal) this.closeQrModal();
    });
  }

  async loadOrders() {
    try {
      const res = await fetch("/api/orders");
      if (res.ok) {
        this.orders = await res.json();
        this.renderOrders();
        this.updateStats();
      }
    } catch (err) {
      console.error("Error loading orders:", err);
    }
  }

  async handleCreateOrder(e) {
    e.preventDefault();
    const orderNumber = this.orderNumInput.value.trim();
    if (!orderNumber) return;

    const payload = {
      order_number: orderNumber,
      customer_name: this.custNameInput.value.trim(),
      notes: this.notesInput.value.trim()
    };

    try {
      const res = await fetch("/api/orders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      if (res.ok) {
        const newOrder = await res.json();
        this.custNameInput.value = "";
        this.notesInput.value = "";
        this.suggestNextOrderNumber(orderNumber);
        
        await this.loadOrders();
        this.showQrModal(newOrder.token, newOrder.order_number);
      }
    } catch (err) {
      alert("حدث خطأ أثناء إنشاء الطلب: " + err.message);
    }
  }

  suggestNextOrderNumber(prev) {
    const num = parseInt(prev.replace(/\D/g, ""));
    if (!isNaN(num)) {
      this.orderNumInput.value = (num + 1).toString();
    } else {
      this.orderNumInput.value = "";
    }
  }

  async callOrder(orderId) {
    try {
      await fetch(`/api/orders/${orderId}/call`, { method: "POST" });
      await this.loadOrders();
    } catch (err) {
      console.error(err);
    }
  }

  async repeatCallOrder(orderId) {
    try {
      await fetch(`/api/orders/${orderId}/repeat_call`, { method: "POST" });
      await this.loadOrders();
    } catch (err) {
      console.error(err);
    }
  }

  async completeOrder(orderId) {
    try {
      await fetch(`/api/orders/${orderId}/complete`, { method: "POST" });
      await this.loadOrders();
    } catch (err) {
      console.error(err);
    }
  }

  async resetOrder(orderId) {
    try {
      await fetch(`/api/orders/${orderId}/reset`, { method: "POST" });
      await this.loadOrders();
    } catch (err) {
      console.error(err);
    }
  }

  async deleteOrder(orderId) {
    if (!confirm("هل أنت متأكد من حذف هذا الطلب؟")) return;
    try {
      await fetch(`/api/orders/${orderId}`, { method: "DELETE" });
      await this.loadOrders();
    } catch (err) {
      console.error(err);
    }
  }

  showQrModal(token, orderNumber) {
    this.currentQrToken = token;
    this.modalOrderNum.textContent = `#${orderNumber}`;
    this.modalQrImg.src = `/api/qr/${token}?host=${encodeURIComponent(this.baseUrl)}&t=${Date.now()}`;
    const url = `${this.baseUrl}/pager/${token}`;
    this.modalLink.value = url;
    this.qrModal.style.display = "flex";
  }

  closeQrModal() {
    this.qrModal.style.display = "none";
    this.currentQrToken = null;
  }

  copyCurrentLink() {
    if (this.modalLink.value) {
      navigator.clipboard.writeText(this.modalLink.value);
      alert("تم نسخ الرابط! يمكنك إرساله أو فتحه للتجربة.");
    }
  }

  openCurrentTestPager() {
    if (this.currentQrToken) {
      window.open(`/pager/${this.currentQrToken}`, "_blank");
    }
  }

  renderOrders() {
    if (!this.orders.length) {
      this.ordersList.innerHTML = `
        <div style="grid-column: 1 / -1; text-align: center; padding: 50px; color: #64748b;">
          <h3>لا توجد طلبات نشطة حالياً</h3>
          <p style="font-size: 13px; margin-top: 6px;">قم بإنشاء طلب جديد من القائمة الجانبية للبدء فوراً.</p>
        </div>
      `;
      return;
    }

    this.ordersList.innerHTML = this.orders.map(order => {
      let stateClass = "";
      let tagBadge = "";
      let actionButtons = "";

      if (order.status === "READY") {
        stateClass = "ready-state";
        tagBadge = `<span class="tag-status tag-ready">🔔 يرن الآن</span>`;
        actionButtons = `
          <button class="btn-ctrl btn-ctrl-repeat" onclick="cashier.repeatCallOrder(${order.id})" title="تكرار الرنين">
            🔁 تكرار التنبيه
          </button>
          <button class="btn-ctrl btn-ctrl-complete" onclick="cashier.completeOrder(${order.id})" title="تم التسليم">
            ✅ تم الاستلام
          </button>
        `;
      } else if (order.status === "WAITING") {
        stateClass = "waiting-state";
        tagBadge = `<span class="tag-status tag-waiting">⏳ قيد التحضير</span>`;
        actionButtons = `
          <button class="btn-ctrl btn-ctrl-call" onclick="cashier.callOrder(${order.id})" title="تشغيل البيجر">
            🔔 رنين البيجر
          </button>
        `;
      } else if (order.status === "COMPLETED") {
        stateClass = "completed-state";
        tagBadge = `<span class="tag-status tag-completed">🎉 تم الاستلام</span>`;
        actionButtons = `
          <button class="btn-ctrl btn-ctrl-icon" style="flex:1;" onclick="cashier.resetOrder(${order.id})" title="إعادة للانتظار">
            ↩️ إعادة للانتظار
          </button>
        `;
      }

      return `
        <div class="order-card ${stateClass}">
          <div>
            <div class="card-header-line">
              <div class="order-badge-title">#${order.order_number}</div>
              ${tagBadge}
            </div>

            <div class="order-details-meta" style="margin-top: 10px;">
              ${order.customer_name ? `<div>👤 <strong>${order.customer_name}</strong></div>` : ""}
              ${order.notes ? `<div>📝 ${order.notes}</div>` : ""}
              <div style="font-size: 11px; color: #64748b; margin-top: 6px;">
                ⏱️ رن: ${order.call_count || 0} مرات | ${new Date(order.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </div>
            </div>
          </div>

          <div>
            <div class="order-card-buttons">
              ${actionButtons}
              <button class="btn-ctrl btn-ctrl-icon" onclick="cashier.showQrModal('${order.token}', '${order.order_number}')" title="عرض QR Code">
                📱
              </button>
              <button class="btn-ctrl btn-ctrl-icon" onclick="cashier.deleteOrder(${order.id})" title="حذف">
                🗑️
              </button>
            </div>
          </div>
        </div>
      `;
    }).join("");
  }

  updateStats() {
    const waiting = this.orders.filter(o => o.status === "WAITING").length;
    const ready = this.orders.filter(o => o.status === "READY").length;
    const done = this.orders.filter(o => o.status === "COMPLETED").length;

    this.statWaiting.textContent = waiting;
    this.statReady.textContent = ready;
    this.statDone.textContent = done;
  }

  connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/cashier`;

    try {
      this.ws = new WebSocket(wsUrl);

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.event === "customer_acknowledged") {
            this.playCashierDing();
            alert(`🏃‍♂️ العميل صاحب الطلب #${data.order?.order_number || ''} في طريقه للاستلام الآن!`);
          }
          this.loadOrders();
        } catch (e) {
          console.error(e);
        }
      };

      this.ws.onclose = () => {
        setTimeout(() => this.connectWebSocket(), 3000);
      };
    } catch (err) {
      setTimeout(() => this.connectWebSocket(), 4000);
    }
  }

  playCashierDing() {
    try {
      const audio = new Audio("/static/audio/chime.wav");
      audio.play().catch(() => {});
    } catch (e) {}
  }
}
