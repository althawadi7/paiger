#!/usr/bin/env python3
import re

with open("app/templates/pager.html", "r", encoding="utf-8") as f:
    html = f.read()

# 1. Clean up broken CSS keyframes at lines ~48-51
html = re.sub(r'@keyframes shake\{[^}]+\}\s*\n\s*50%\{opacity:0\.88\}\s*\n\s*100%\{opacity:0\}\s*\n\}', '@keyframes shake{\n  0%,100%{transform:none}\n  25%{transform:translateX(-3px) rotate(-.5deg)}\n  75%{transform:translateX(3px) rotate(.5deg)}\n}', html)

# 2. Remove .top-lock-section, .lock-clock, .lock-date CSS
css_old = r'/\* Top Clock & Date \*/\s*\.top-lock-section\{[^}]+\}\s*\.lock-clock\{[^}]+\}\s*\.lock-date\{[^}]+\}'
html = re.sub(css_old, '/* Top section removed */', html)

# 3. Add styles for Permission Modal Popup
perm_css = """
/* Modern Force Permission Modal Popup */
.permission-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(8, 10, 18, 0.88);
  -webkit-backdrop-filter: blur(24px);
  backdrop-filter: blur(24px);
  z-index: 10000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  opacity: 1;
  visibility: visible;
  transition: opacity 0.35s ease, visibility 0.35s;
}
.permission-backdrop.hide {
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
}
.permission-sheet {
  width: 100%;
  max-width: 380px;
  background: rgba(22, 27, 46, 0.96);
  border: 1.5px solid rgba(255, 255, 255, 0.18);
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.7), 0 0 40px rgba(139, 123, 255, 0.25);
  border-radius: 32px;
  padding: 28px 22px 24px;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  animation: modal-pop 0.35s cubic-bezier(0.175, 0.885, 0.32, 1.275);
}
@keyframes modal-pop {
  0% { transform: scale(0.9) translateY(20px); opacity: 0; }
  100% { transform: scale(1) translateY(0); opacity: 1; }
}
.permission-icon-wrap {
  position: relative;
  width: 72px;
  height: 72px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.permission-bell-glow {
  font-size: 38px;
  width: 68px;
  height: 68px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(139, 123, 255, 0.4) 0%, rgba(139, 123, 255, 0.08) 70%);
  border: 1.5px solid rgba(139, 123, 255, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: bell-pulse 2s infinite ease-in-out;
}
@keyframes bell-pulse {
  0%, 100% { transform: scale(1); box-shadow: 0 0 20px rgba(139, 123, 255, 0.4); }
  50% { transform: scale(1.08); box-shadow: 0 0 35px rgba(139, 123, 255, 0.7); }
}
.permission-title {
  font-size: 21px;
  font-weight: 700;
  color: #ffffff;
  margin-top: -4px;
}
.permission-desc {
  font-size: 13.5px;
  color: #cbd5e1;
  line-height: 1.6;
  margin-top: -6px;
}
.permission-features {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 6px 0;
  text-align: right;
}
.perm-item {
  display: flex;
  align-items: center;
  gap: 12px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.09);
  padding: 10px 14px;
  border-radius: 16px;
}
.perm-item-icon {
  font-size: 20px;
  flex-shrink: 0;
}
.perm-item-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.perm-item-text strong {
  font-size: 13px;
  color: #ffffff;
  font-weight: 600;
}
.perm-item-text span {
  font-size: 11.5px;
  color: #94a3b8;
}
.permission-accept-btn {
  width: 100%;
  height: 54px;
  border-radius: 18px;
  background: linear-gradient(135deg, #36d9a0, #10b981);
  color: #03261a;
  font-size: 15.5px;
  font-weight: 700;
  font-family: inherit;
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  box-shadow: 0 8px 25px rgba(54, 217, 160, 0.45);
  transition: transform 0.15s, box-shadow 0.2s;
  margin-top: 4px;
}
.permission-accept-btn:active {
  transform: scale(0.97);
}
"""

# Insert permission modal CSS right before </style>
html = html.replace('</style>', perm_css + '\n</style>')

# 4. Remove the top clock section in HTML:
clock_html = r'<!-- Top Real Clock & Arabic Date -->\s*<div class="top-lock-section">.*?</div>\s*</div>'
html = re.sub(clock_html, '', html, flags=re.DOTALL)

# 5. Remove armBtn and onwayBtn from inside .widget-box
html = re.sub(r'<button class="widget-btn" id="armBtn">.*?</button>', '', html)
html = re.sub(r'<button class="widget-btn green" id="onwayBtn" hidden>.*?</button>', '', html)

# 6. Add Permission Popup Modal right inside <body>
perm_modal_markup = """
  <!-- Modern Force Permission Modal Popup -->
  <div class="permission-backdrop" id="permissionModal">
    <div class="permission-sheet">
      <div class="permission-icon-wrap">
        <div class="permission-bell-glow">🔔</div>
      </div>
      <h2 class="permission-title">تفعيل تنبيهات البيجر</h2>
      <p class="permission-desc">فضلاً اسمح بالصوت والتنبيهات لنتمكن من مناداتك فور أن يجهز طلبك:</p>
      
      <div class="permission-features">
        <div class="perm-item">
          <div class="perm-item-icon">🔊</div>
          <div class="perm-item-text">
            <strong>رنين عالي واهتزاز</strong>
            <span>تنبيه قوي عند جهوزية طلبك للاستلام</span>
          </div>
        </div>
        <div class="perm-item">
          <div class="perm-item-icon">⚡</div>
          <div class="perm-item-text">
            <strong>فلاش الكاميرا الخلفي</strong>
            <span>وميض حقيقي من فلاش الهاتف للفت الانتباه</span>
          </div>
        </div>
        <div class="perm-item">
          <div class="perm-item-icon">📱</div>
          <div class="perm-item-text">
            <strong>إشعار شاشة القفل</strong>
            <span>تنبيه مباشر حتى لو كانت شاشة الجوال مقفلة</span>
          </div>
        </div>
      </div>

      <button class="permission-accept-btn" id="permissionAcceptBtn">
        السماح بالتنبيهات وتفعيل البيجر 🚀
      </button>
    </div>
  </div>
"""
html = html.replace('<body id="pageBody">', '<body id="pageBody">\n' + perm_modal_markup)

# 7. Update JavaScript logic
# Replace references to armBtn and onwayBtn
js_replace_target = """var pageBody = document.getElementById("pageBody");
var armBtn = document.getElementById("armBtn");
var onwayBtn = document.getElementById("onwayBtn");"""

js_replace_new = """var pageBody = document.getElementById("pageBody");
var permissionModal = document.getElementById("permissionModal");
var permissionAcceptBtn = document.getElementById("permissionAcceptBtn");"""

html = html.replace(js_replace_target, js_replace_new)

# Update arming logic from armBtn to permissionAcceptBtn
arm_logic_target = """// 7. User tap to Arm (Unlocks AudioContext, MediaSession, WakeLock, Vibration & Camera Flash)
armBtn.onclick = async function(){
  isArmed = true;

  // A. Unlock Web Audio Context
  try {
    ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
    if(ctx.state === "suspended") await ctx.resume();
  } catch(e){}

  // B. Start background silent audio to establish permanent MediaSession
  try {
    mainAudio.src = "/static/audio/silent.wav";
    mainAudio.loop = true;
    mainAudio.volume = 1.0;
    await mainAudio.play();
  } catch(e){}

  // C. Update Lock Screen Notification Widget with Base64 Logo
  updateMediaSession(false);

  // D. Play pleasant chime sound confirming audio is active
  try {
    var chime = new Audio("/static/audio/chime.wav");
    chime.play().catch(function(){});
  } catch(e){}

  // E. Initialize Camera Torch LED
  initCameraTorch();
  requestNotificationPermissionAndSubscribe();

  // F. Test vibration burst
  try {
    if(navigator.vibrate) navigator.vibrate([200, 100, 200]);
  } catch(e){}

  // G. Request Screen WakeLock if in secure context
  if(window.isSecureContext && "wakeLock" in navigator){
    try { navigator.wakeLock.request("screen"); } catch(e){}
  }

  updateUI();
};

// 8. Customer confirms arrival
onwayBtn.onclick = async function(){
  order.status = "onway";
  stopRingingAlarm();
  updateUI();

  if(ws && ws.readyState === WebSocket.OPEN){
    try { ws.send(JSON.stringify({action: "onway"})); } catch(e){}
  }
  try {
    await fetch("/api/orders/" + order.id + "/onway", {method: "POST"});
  } catch(e){}
};"""

arm_logic_new = """// 7. Force Permission Modal Accept
async function activateArming(){
  isArmed = true;

  // Hide Permission Modal smoothly
  if (permissionModal) permissionModal.classList.add("hide");

  // A. Unlock Web Audio Context
  try {
    ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
    if(ctx.state === "suspended") await ctx.resume();
  } catch(e){}

  // B. Start background silent audio to establish permanent MediaSession
  try {
    mainAudio.src = "/static/audio/silent.wav";
    mainAudio.loop = true;
    mainAudio.volume = 1.0;
    await mainAudio.play();
  } catch(e){}

  // C. Update Lock Screen Notification Widget with Base64 Logo
  updateMediaSession(false);

  // D. Play pleasant chime sound confirming audio is active
  try {
    var chime = new Audio("/static/audio/chime.wav");
    chime.play().catch(function(){});
  } catch(e){}

  // E. Initialize Camera Torch LED
  initCameraTorch();
  requestNotificationPermissionAndSubscribe();

  // F. Test vibration burst
  try {
    if(navigator.vibrate) navigator.vibrate([200, 100, 200]);
  } catch(e){}

  // G. Request Screen WakeLock if in secure context
  if(window.isSecureContext && "wakeLock" in navigator){
    try { navigator.wakeLock.request("screen"); } catch(e){}
  }

  updateUI();
}

if (permissionAcceptBtn) {
  permissionAcceptBtn.onclick = activateArming;
}

// 8. Customer confirms arrival (via Top Notification Banner button)
async function confirmArrival(){
  order.status = "onway";
  stopRingingAlarm();
  if (pushBanner) pushBanner.classList.remove("show");
  updateUI();

  if(ws && ws.readyState === WebSocket.OPEN){
    try { ws.send(JSON.stringify({action: "onway"})); } catch(e){}
  }
  try {
    await fetch("/api/orders/" + order.id + "/onway", {method: "POST"});
  } catch(e){}
}

if (bannerActionBtn) {
  bannerActionBtn.onclick = confirmArrival;
}"""

html = html.replace(arm_logic_target, arm_logic_new)

# Update updateUI() to remove armBtn/onwayBtn manipulation
ui_old = """  armBtn.hidden = isArmed || isDone;
  onwayBtn.hidden = !(isArmed && isRinging);

  var m = {
    waiting: ['طلبك قيد التحضير', 'سنهتز ونرن فور أن يجهز'],
    ringing: ['طلبك جاهز! 🎉', 'تفضل إلى الكاونتر للاستلام'],
    onway: ['أبلغنا الكاشير', 'الكاشير يعلم أنك قادم'],
    done: ['تم الاستلام', 'بالعافية! شكراً لزيارتك']
  }[order.status] || ['قيد الانتظار', ''];

  var ok = isArmed || isDone;
  wsEl.textContent = ok ? m[0] : 'اضغط لتفعيل الصوت';
  wuEl.textContent = ok ? m[1] : 'انقر على الزر أدناه لتفعيل الصوت والاهتزاز والفلاش';"""

ui_new = """  // If order is done, dismiss permission modal if still open
  if (isDone && permissionModal) {
    permissionModal.classList.add("hide");
  }

  var m = {
    waiting: ['طلبك قيد التحضير', 'سنهتز ونرن فور أن يجهز'],
    ringing: ['طلبك جاهز! 🎉', 'تفضل إلى الكاونتر للاستلام'],
    onway: ['أبلغنا الكاشير', 'الكاشير يعلم أنك قادم'],
    done: ['تم الاستلام', 'بالعافية! شكراً لزيارتك']
  }[order.status] || ['قيد الانتظار', ''];

  wsEl.textContent = m[0];
  wuEl.textContent = m[1];"""

html = html.replace(ui_old, ui_new)

# Update clock() function in JS:
old_clock_fn = """// Real-time Clock in Arabic
function clock(){
  var d = new Date();
  document.getElementById("clock").textContent = d.toLocaleTimeString('en-GB', {hour:'2-digit', minute:'2-digit'});
  document.getElementById("wt").textContent = d.toLocaleTimeString('ar', {hour:'2-digit', minute:'2-digit'});
  document.getElementById("date").textContent = d.toLocaleDateString('ar', {weekday:'long', day:'numeric', month:'long'});
}
clock();
setInterval(clock, 1000);"""

new_clock_fn = """// Real-time Clock for Widget Header
function clock(){
  var d = new Date();
  var wtEl = document.getElementById("wt");
  if (wtEl) wtEl.textContent = d.toLocaleTimeString('ar', {hour:'2-digit', minute:'2-digit'});
}
clock();
setInterval(clock, 1000);"""

html = html.replace(old_clock_fn, new_clock_fn)

# Replace MediaSession handlers referencing onwayBtn
html = html.replace('if(onwayBtn && !onwayBtn.hidden) onwayBtn.click();', 'confirmArrival();')

with open("app/templates/pager.html", "w", encoding="utf-8") as f:
    f.write(html)

print("pager.html successfully updated!")

