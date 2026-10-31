#!/usr/bin/env python3
import re

with open("app/templates/pager.html", "r", encoding="utf-8") as f:
    html = f.read()

# 1. Remove flash item from permission popup
perm_flash_item = r'<div class="perm-item">\s*<div class="perm-item-icon">⚡</div>\s*<div class="perm-item-text">\s*<strong>فلاش الكاميرا الخلفي</strong>\s*<span>وميض حقيقي من فلاش الهاتف للفت الانتباه</span>\s*</div>\s*</div>'
html = re.sub(perm_flash_item, '', html)

# 2. Remove hidden camera video element
html = re.sub(r'<!-- Hidden Camera Video Element to keep hardware pipeline alive for physical torch -->\s*<video id="cameraPreview"[^>]*></video>', '', html)

# 3. Remove httpsNoticeCard
html = re.sub(r'<div class="https-notice-card" id="httpsNoticeCard"[^>]*>.*?</div>', '', html, flags=re.DOTALL)

# 4. Remove torch variables and functions
torch_engine_pattern = r'// 1\. Hardware Camera Flashlight \(Torch LED\) Engine.*?// 2\. Synthesizer'
html = re.sub(torch_engine_pattern, '// 2. Synthesizer', html, flags=re.DOTALL)

# 5. Remove startTorchStrobe() and stopTorchStrobe() calls
html = html.replace('startTorchStrobe();', '// flash removed')
html = html.replace('stopTorchStrobe();', '// flash removed')
html = html.replace('initCameraTorch();', '// flash removed')

with open("app/templates/pager.html", "w", encoding="utf-8") as f:
    f.write(html)

print("Flash completely stopped and removed from pager.html!")

