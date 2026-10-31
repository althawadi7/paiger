#!/usr/bin/env python3
import re

with open("app/static/img/logos_b64.js", "r", encoding="utf-8") as f:
    b64_js = f.read()

with open("app/templates/pager.html", "r", encoding="utf-8") as f:
    html = f.read()

# Replace window.BRAND_LOGO_B64 and window.BRAND_READY_B64 block in pager.html
b64_pattern = r'window\.BRAND_LOGO_B64\s*=\s*"[^"]+";\s*window\.BRAND_READY_B64\s*=\s*"[^"]+";'
html = re.sub(b64_pattern, b64_js.strip(), html)

# Now update updateMediaSession in pager.html to be richer and pass both b64 and absolute url
old_session_code = r'// 5\. iOS & Android Lock Screen Media Session\s*function updateMediaSession\(isRinging\)\{.*?navigator\.mediaSession\.setActionHandler\("pause"'

new_session_code = """// 5. iOS & Android Lock Screen Media Session
function updateMediaSession(isRinging){
  if(!("mediaSession" in navigator)) return;

  var logoB64 = isRinging ? window.BRAND_READY_B64 : window.BRAND_LOGO_B64;
  var staticUrl = window.location.origin + (isRinging ? "/static/img/brand-ready.png" : "/static/img/brand-logo.png");

  try {
    var custPart = order.name ? (" (" + order.name + ")") : "";
    var artList = [];
    if(logoB64) {
      artList.push({ src: logoB64, sizes: "512x512", type: "image/png" });
    }
    artList.push({ src: staticUrl, sizes: "512x512", type: "image/png" });

    if(isRinging){
      navigator.mediaSession.metadata = new MediaMetadata({
        title: "🚨 طلبك #" + order.num + custPart + " جاهز للاستلام!",
        artist: "مطعم البرجر الذهبي • Golden Burger 🍔",
        album: "تفضل إلى الكاونتر للاستلام الآن 🏃‍♂️",
        artwork: artList
      });
    } else {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: "⏳ طلبك #" + order.num + custPart + " • قيد التحضير",
        artist: "مطعم البرجر الذهبي • Golden Burger 🍔",
        album: "بيجر الطلبات الذكي • سنهتز ونرن فور أن يجهز طلبك",
        artwork: artList
      });
    }

    navigator.mediaSession.playbackState = "playing";

    // Fix lock screen scrubber so it looks clean
    if("setPositionState" in navigator.mediaSession){
      try {
        navigator.mediaSession.setPositionState({
          duration: 3600,
          playbackRate: 1,
          position: 0
        });
      } catch(e){}
    }

    // Hide unwanted playlist skip buttons
    ['previoustrack', 'nexttrack', 'seekbackward', 'seekforward'].forEach(function(act){
      try { navigator.mediaSession.setActionHandler(act, null); } catch(e){}
    });

    // Tap on Lock Screen Play / Pause button
    navigator.mediaSession.setActionHandler("pause\""""

html = re.sub(old_session_code, new_session_code, html, flags=re.DOTALL)

with open("app/templates/pager.html", "w", encoding="utf-8") as f:
    f.write(html)

print("Updated pager.html with high-res brand artwork and enhanced MediaSession!")

