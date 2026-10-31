#!/usr/bin/env python3
import re

with open("app/templates/pager.html", "r", encoding="utf-8") as f:
    html = f.read()

# 1. Clean broken CSS keyframes at lines 47-52
broken_css = r'@keyframes shake\{[^}]+\}\s*\n\s*50%\{opacity:0\.88\}\s*\n\s*100%\{opacity:0\}\s*\n\}'
html = re.sub(broken_css, '@keyframes shake{\n  0%,100%{transform:none}\n  25%{transform:translateX(-3px) rotate(-.5deg)}\n  75%{transform:translateX(3px) rotate(.5deg)}\n}', html)

# 2. Update body CSS to be centered vertically and horizontally
body_css_old = r'body\{[^}]+padding:calc\(16px[^}]+\}'
body_css_new = """body{
  width:100%;
  min-height:100dvh;
  background:radial-gradient(110% 70% at 50% 15%, #382c7e, transparent 65%), radial-gradient(100% 60% at 10% 85%, #641544, transparent 65%), #090b13;
  color:#fff;
  font-family:"Readex Pro",system-ui,-apple-system,sans-serif;
  display:flex;
  flex-direction:column;
  justify-content:center;
  align-items:center;
  padding:max(24px, env(safe-area-inset-top)) 16px max(28px, env(safe-area-inset-bottom));
  overflow-y:auto;
  -webkit-overflow-scrolling:touch;
  transition:background 0.4s ease;
  position:relative;
}"""
html = re.sub(body_css_old, body_css_new, html)

# 3. Add .pager-main-center styles and update .widget-box styles
widget_old = r'/\* Center Widget \*/\s*\.widget-box\{[^}]+\}'
widget_new = """/* Center Main Container */
.pager-main-center{
  width:100%;
  max-width:420px;
  margin:auto;
  display:flex;
  flex-direction:column;
  align-items:center;
  justify-content:center;
  gap:16px;
  position:relative;
  z-index:2;
}

/* Center Widget Box */
.widget-box{
  width:100%;
  margin:0;
  padding:26px 22px;
  border-radius:36px;
  background:rgba(18, 22, 38, 0.85);
  border:1.5px solid rgba(255,255,255,.16);
  -webkit-backdrop-filter:blur(32px);
  backdrop-filter:blur(32px);
  box-shadow:0 30px 75px rgba(0,0,0,.6), 0 0 45px rgba(139,123,255,.12);
  display:flex;
  flex-direction:column;
  gap:14px;
  position:relative;
  transition:border-color 0.3s, box-shadow 0.3s;
}
body.ring .widget-box{
  border-color:rgba(255, 77, 109, 0.7);
  box-shadow:0 30px 75px rgba(0,0,0,.7), 0 0 50px rgba(255, 77, 109, 0.35);
}"""
html = re.sub(widget_old, widget_new, html)

# 4. Polish Queue cards styles
queue_css_old = r'/\* Queue Ahead & Estimated Wait Time Styling \*/.*?\.pulse-dot\{'
queue_css_new = """/* Queue Ahead & Estimated Wait Time Styling */
.queue-container {
  width: 100%;
  margin: 12px 0 4px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.queue-cards-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  width: 100%;
}
.queue-card {
  background: rgba(255, 255, 255, 0.07);
  border: 1.5px solid rgba(255, 255, 255, 0.14);
  border-radius: 24px;
  padding: 16px 12px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  gap: 6px;
  -webkit-backdrop-filter: blur(20px);
  backdrop-filter: blur(20px);
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.25);
  transition: transform 0.2s, background 0.2s;
}
.queue-icon-circle {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: rgba(54, 217, 160, 0.16);
  border: 1.5px solid rgba(54, 217, 160, 0.35);
  color: #36d9a0;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 2px;
}
.queue-label {
  font-size: 13px;
  font-weight: 600;
  color: #94a3b8;
}
.queue-val {
  font-size: 34px;
  font-weight: 800;
  color: #ffffff;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
}
.queue-val.time-val {
  font-size: 17.5px;
  font-weight: 700;
  line-height: 1.35;
  color: #f8fafc;
}
.queue-update-pill {
  width: 100%;
  background: rgba(54, 217, 160, 0.1);
  border: 1px solid rgba(54, 217, 160, 0.28);
  border-radius: 16px;
  padding: 11px 14px;
  font-size: 12.5px;
  color: #36d9a0;
  text-align: center;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}
.pulse-dot{"""
html = re.sub(queue_css_old, queue_css_new, html, flags=re.DOTALL)

# 5. Wrap the widget and bottom hint in <main class="pager-main-center">
markup_old = r'<!-- The Actual Pager Widget Box -->\s*<div class="widget-box">(.*?)</div>\s*<!-- Bottom Connection Status -->\s*<div class="bottom-bar-hint">(.*?)</div>'

markup_new = """<!-- Centered Screen Wrapper -->
  <main class="pager-main-center">
    <!-- The Actual Pager Widget Box -->
    <div class="widget-box">\\1</div>

    <!-- Bottom Connection Status -->
    <div class="bottom-bar-hint">\\2</div>
  </main>"""

html = re.sub(markup_old, markup_new, html, flags=re.DOTALL)

with open("app/templates/pager.html", "w", encoding="utf-8") as f:
    f.write(html)

print("pager.html successfully updated with centered layout and luxury styling!")

