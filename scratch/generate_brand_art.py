#!/usr/bin/env python3
import base64
import math
from PIL import Image, ImageDraw, ImageFont

def create_rounded_rect(draw, xy, radius, fill=None, outline=None, width=1):
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=fill, outline=outline, width=width)

def draw_burger(draw, cx, cy, scale=1.0, is_ready=False):
    # scale = 1.0 => burger width ~240px, height ~180px
    w = int(220 * scale)
    hw = w // 2
    
    # 1. Top Bun
    # (Golden amber gradient or solid)
    top_bun_color = (245, 175, 45) if not is_ready else (255, 195, 60)
    top_bun_shadow = (210, 140, 25)
    
    # Bun curve
    draw.chord([cx - hw, cy - int(95*scale), cx + hw, cy - int(15*scale)], 180, 360, fill=top_bun_color, outline=top_bun_shadow, width=2)
    # Sesame seeds on top bun
    seed_color = (255, 245, 210)
    seeds = [(-45, -60), (-15, -75), (20, -70), (50, -55), (-30, -40), (10, -45), (35, -35), (-5, -25)]
    for sx, sy in seeds:
        draw.ellipse([cx + int(sx*scale), cy + int(sy*scale), cx + int((sx+7)*scale), cy + int((sy+4)*scale)], fill=seed_color)

    # 2. Lettuce Layer (Wavy fresh green)
    lettuce_color = (46, 204, 113)
    lettuce_y = cy - int(14*scale)
    # Series of overlapping rounded lobes
    for lx in range(cx - hw - 8, cx + hw + 8, int(22*scale)):
        draw.ellipse([lx, lettuce_y, lx + int(28*scale), lettuce_y + int(18*scale)], fill=lettuce_color)

    # 3. Melted Cheese Layer (Golden yellow triangle drips)
    cheese_color = (255, 215, 0)
    cheese_y = cy - int(2*scale)
    draw.rectangle([cx - hw + 6, cheese_y, cx + hw - 6, cheese_y + int(12*scale)], fill=cheese_color)
    # Drips
    draw.polygon([(cx - int(60*scale), cheese_y + int(10*scale)), (cx - int(45*scale), cheese_y + int(28*scale)), (cx - int(30*scale), cheese_y + int(10*scale))], fill=cheese_color)
    draw.polygon([(cx + int(25*scale), cheese_y + int(10*scale)), (cx + int(42*scale), cheese_y + int(32*scale)), (cx + int(58*scale), cheese_y + int(10*scale))], fill=cheese_color)

    # 4. Grilled Patty (Dark savory brown)
    patty_color = (105, 50, 22)
    patty_shadow = (75, 30, 12)
    create_rounded_rect(draw, [cx - hw + 2, cy + int(10*scale), cx + hw - 2, cy + int(38*scale)], radius=int(12*scale), fill=patty_color, outline=patty_shadow, width=2)
    # Grill marks
    for gx in [-60, -20, 20, 60]:
        draw.line([cx + int(gx*scale) - int(8*scale), cy + int(14*scale), cx + int(gx*scale) + int(12*scale), cy + int(34*scale)], fill=(60, 20, 10), width=int(3*scale))

    # 5. Bottom Bun (Golden rounded base)
    bot_bun_color = (240, 165, 38)
    bot_bun_shadow = (200, 130, 20)
    create_rounded_rect(draw, [cx - hw + 8, cy + int(38*scale), cx + hw - 8, cy + int(64*scale)], radius=int(14*scale), fill=bot_bun_color, outline=bot_bun_shadow, width=2)


def generate_waiting_art():
    size = (512, 512)
    im = Image.new("RGBA", size, (14, 17, 30, 255))
    draw = ImageDraw.Draw(im)

    # Background subtle radial glow
    for r in range(250, 40, -15):
        alpha = int(45 * (1.0 - r / 250.0))
        draw.ellipse([256 - r, 210 - r, 256 + r, 210 + r], fill=(65, 55, 125, alpha))

    # Outer decorative luxury border
    create_rounded_rect(draw, [18, 18, 494, 494], radius=38, outline=(255, 181, 71, 140), width=2)
    create_rounded_rect(draw, [24, 24, 488, 488], radius=32, outline=(255, 255, 255, 30), width=1)

    # Draw Central Burger
    draw_burger(draw, 256, 205, scale=1.08, is_ready=False)

    # Load Fonts
    try:
        font_arabic = ImageFont.truetype("/System/Library/Fonts/GeezaPro.ttc", 36)
        font_eng = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
        font_sub = ImageFont.truetype("/System/Library/Fonts/GeezaPro.ttc", 21)
    except:
        font_arabic = ImageFont.load_default()
        font_eng = font_arabic
        font_sub = font_arabic

    # Restaurant Title: "مطعم البرجر الذهبي"
    title_ar = "مطعم البرجر الذهبي"
    draw.text((256, 335), title_ar, font=font_arabic, fill=(255, 255, 255, 255), anchor="mm")

    # Subtitle English: "GOLDEN BURGER"
    draw.text((256, 380), "★ GOLDEN BURGER ★", font=font_eng, fill=(255, 195, 60, 230), anchor="mm")

    # Bottom Pill Status Badge: "⏳ بيجر الطلبات الذكي • قيد التحضير"
    create_rounded_rect(draw, [60, 415, 452, 465], radius=24, fill=(35, 42, 68, 220), outline=(139, 123, 255, 120), width=1)
    draw.text((256, 440), "⏳ بيجر الطلبات الذكي • قيد التحضير", font=font_sub, fill=(225, 235, 255, 255), anchor="mm")

    return im


def generate_ringing_art():
    size = (512, 512)
    # Vibrant dark wine / ruby alert background
    im = Image.new("RGBA", size, (38, 8, 16, 255))
    draw = ImageDraw.Draw(im)

    # Radial Alert Glow
    for r in range(255, 40, -12):
        alpha = int(70 * (1.0 - r / 255.0))
        draw.ellipse([256 - r, 200 - r, 256 + r, 200 + r], fill=(255, 50, 85, alpha))

    # Outer alert glowing border
    create_rounded_rect(draw, [18, 18, 494, 494], radius=38, outline=(255, 77, 109, 220), width=4)
    create_rounded_rect(draw, [24, 24, 488, 488], radius=32, outline=(54, 217, 160, 160), width=2)

    # Draw Burger
    draw_burger(draw, 256, 195, scale=1.05, is_ready=True)

    # Top floating alert pill: "🚨 جاهز الآن 🚨"
    create_rounded_rect(draw, [140, 40, 372, 85], radius=22, fill=(255, 77, 109, 230), outline=(255, 255, 255, 200), width=2)
    try:
        font_pill = ImageFont.truetype("/System/Library/Fonts/GeezaPro.ttc", 24)
        font_arabic = ImageFont.truetype("/System/Library/Fonts/GeezaPro.ttc", 36)
        font_sub = ImageFont.truetype("/System/Library/Fonts/GeezaPro.ttc", 22)
    except:
        font_pill = ImageFont.load_default()
        font_arabic = font_pill
        font_sub = font_pill

    draw.text((256, 62), "🚨 طلبك جاهز للاستلام! 🚨", font=font_pill, fill=(255, 255, 255, 255), anchor="mm")

    # Brand Title
    draw.text((256, 335), "مطعم البرجر الذهبي", font=font_arabic, fill=(255, 255, 255, 255), anchor="mm")

    # Bottom Call-To-Action Pill: "🏃‍♂️ تفضل إلى الكاونتر للاستلام الآن"
    create_rounded_rect(draw, [45, 395, 467, 465], radius=26, fill=(54, 217, 160, 240), outline=(255, 255, 255, 200), width=2)
    draw.text((256, 430), "🏃‍♂️ تفضل إلى الكاونتر للاستلام الآن", font=font_sub, fill=(5, 38, 26, 255), anchor="mm")

    return im

if __name__ == "__main__":
    im_wait = generate_waiting_art()
    im_wait.convert("RGB").save("app/static/img/brand-logo.png", "PNG", optimize=True)
    
    im_ring = generate_ringing_art()
    im_ring.convert("RGB").save("app/static/img/brand-ready.png", "PNG", optimize=True)
    
    # Also save high-res 512 PWA icons
    im_wait.convert("RGB").save("app/static/img/icon-512.png", "PNG", optimize=True)

    # Encode to base64
    with open("app/static/img/brand-logo.png", "rb") as f:
        b64_logo = base64.b64encode(f.read()).decode("utf-8")
    with open("app/static/img/brand-ready.png", "rb") as f:
        b64_ready = base64.b64encode(f.read()).decode("utf-8")

    js_content = f'window.BRAND_LOGO_B64 = "data:image/png;base64,{b64_logo}";\nwindow.BRAND_READY_B64 = "data:image/png;base64,{b64_ready}";\n'
    with open("app/static/img/logos_b64.js", "w", encoding="utf-8") as f:
        f.write(js_content)

    print("Artwork generated successfully!")
    print("Logo b64 len:", len(b64_logo))
    print("Ready b64 len:", len(b64_ready))

