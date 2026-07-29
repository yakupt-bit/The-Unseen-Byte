"""
Seçilen tek başlık için TEK bir kapak (thumbnail) üretir (Wiro API,
openai/gpt-image-2) ve üzerine metin bindirir (PIL).

ÖNEMLİ (kapak-başlık ilişkisi):
Kapak, başlığın bir tekrarı DEĞİLDİR. Kapak, izleyiciye ham bir
SORU/GİZEM sunar (görsel + 3-5 kelimelik kışkırtıcı metin); başlık bu
sorunun bağlamını/gelişmesini verir ama cevabı vermez; videonun kendisi
asıl cevabı verir. Bu yüzden kapak için Claude'dan başlıktan bağımsız,
daha ham ve daha az bilgi veren bir "hook" konsepti üretiliyor.

NOT: YouTube'un native A/B testi (Test & Compare) API'den erişilemiyor
ve YPP üyeliği gerektiriyor, bu yüzden sistem artık çoklu kapak yerine
tek, en güçlü kapağı üretiyor (bkz. generate_titles.py).

Kullanım:
    python scripts/generate_thumbnail.py --titles titles.json --out-dir output/thumbnails/
    python scripts/generate_thumbnail.py --titles titles.json --out-dir output/thumbnails/ --script script.md
"""
import argparse
import json
import os
import random
import textwrap

import anthropic
from PIL import Image, ImageDraw, ImageFont

from wiro_client import run_model, download_output

MODEL_CREATIVE = "claude-sonnet-4-6"

THUMBNAIL_STYLE = (
    "bold high-contrast digital illustration, dramatic lighting, "
    "single clear focal point, vivid saturated colors (red/yellow/dark "
    "accents work well), tech/gaming aesthetic, leaves empty space in "
    "one corner for text overlay, no existing text in the image, 16:9, "
    "composed so a viewer's eye is immediately drawn to one specific "
    "detail"
)

FONT_PATH = "assets/fonts/Anton-Regular.ttf"
ANNOTATION_COLOR = (235, 45, 45)


def call_claude(client, prompt, model=MODEL_CREATIVE, max_tokens=600):
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in response.content if b.type == "text")


def generate_thumbnail_concept(client, title: str, script_excerpt: str) -> dict:
    """
    Başlıktan BAĞIMSIZ bir kapak konsepti üretir: bir görsel açıklama
    (image prompt) ve çok kısa bir kışkırtıcı metin (hook text).
    Kapak, başlığın vereceği bağlamı/gelişmeyi VERMEMELİDİR - sadece
    ham soruyu/gizemi ortaya koymalıdır.
    """
    prompt = f"""Bir YouTube kapak görseli (thumbnail) konsepti üret.

KESİN KURAL: Bu kapak, aşağıdaki başlıkla AYNI bilgiyi VERMEMELİ.
Başlık zaten konunun gelişmesini/bağlamını açıklıyor. Kapağın görevi
SADECE ham bir soru/gizem/çelişki sunmak - izleyici "bu ne, ne oluyor"
desin, başlıktaki bilgiyi henüz bilmesin.

BAŞLIK (kapakta bunu tekrar etme, bundan bağımsız düşün): {title}

SCRIPT'TEN KISA ALINTI (konunun özünü anlamak için, kapak metnine
doğrudan kopyalama): {script_excerpt[:800]}

Üret:
1. "visual_prompt": İngilizce, somut bir SAHNE/NESNE/AN tarifi (ör. bir
   nesnenin garip bir detayı, açıklanamayan bir an). Kişi/karakter
   isimlerinden kaçın, jenerik ve görsel olarak net olsun.
2. "hook_text": İngilizce, TÜM BÜYÜK HARF, EN FAZLA 5 KELİME, soru
   işareti kullanmadan da merak uyandıran kışkırtıcı bir ifade
   (ör. "THE PART NO ONE EXPLAINS", "HIDDEN FOR 30 YEARS"). Başlıktaki
   kelimeleri birebir tekrarlama.

Çıktı SADECE JSON: {{"visual_prompt": "...", "hook_text": "..."}}"""

    raw = call_claude(client, prompt)
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Güvenli düşüş: jenerik bir konsept
        return {
            "visual_prompt": "a mysterious object under dramatic lighting, "
                              "close-up on one strange unexplained detail",
            "hook_text": "WHAT NO ONE EXPLAINS",
        }


def generate_background(prompt: str, out_path: str):
    full_prompt = f"{THUMBNAIL_STYLE}. Subject: {prompt}"
    try:
        task = run_model("openai", "gpt-image-2", {
            "prompt": full_prompt,
            "resolution": "1k",
            "ratio": "16:9",
            "quality": "medium",
            "samples": 1,
        })
        download_output(task, out_path)
    except RuntimeError as e:
        if "safety system" in str(e).lower():
            print("  UYARI: kapak için güvenlik reddi, jenerik tarifle yeniden deniyorum...")
            fallback_prompt = (
                f"{THUMBNAIL_STYLE}. Subject: an abstract, dramatic "
                "technology-themed scene, glowing shapes, no people, no text"
            )
            task = run_model("openai", "gpt-image-2", {
                "prompt": fallback_prompt,
                "resolution": "1k",
                "ratio": "16:9",
                "quality": "medium",
                "samples": 1,
            })
            download_output(task, out_path)
        else:
            raise


def draw_annotation(draw, img_w, img_h, avoid_bottom_frac=0.45):
    style = random.choice(["circle", "arrow"])
    safe_top = int(img_h * 0.08)
    safe_bottom = int(img_h * (1 - avoid_bottom_frac))
    safe_left = int(img_w * 0.45)
    safe_right = int(img_w * 0.92)

    cx = random.randint(safe_left, safe_right)
    cy = random.randint(safe_top, safe_bottom)

    if style == "circle":
        r = random.randint(int(img_w * 0.06), int(img_w * 0.09))
        width = max(4, int(img_w * 0.007))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ANNOTATION_COLOR, width=width)
    else:
        length = int(img_w * 0.12)
        angle_choices = [(-1, 1), (1, 1), (-1, -1)]
        dx, dy = random.choice(angle_choices)
        x0, y0 = cx - dx * length, cy - dy * length
        x1, y1 = cx, cy
        width = max(5, int(img_w * 0.008))
        draw.line([x0, y0, x1, y1], fill=ANNOTATION_COLOR, width=width)
        head_size = int(img_w * 0.02)
        draw.polygon([
            (x1, y1),
            (x1 - dx * head_size - dy * head_size, y1 - dy * head_size + dx * head_size),
            (x1 - dx * head_size + dy * head_size, y1 - dy * head_size - dx * head_size),
        ], fill=ANNOTATION_COLOR)


def overlay_text(image_path: str, hook_text: str, out_path: str):
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")

    draw_annotation(draw, img.width, img.height)

    max_text_width = int(img.width * 0.92)
    max_text_height = int(img.height * 0.38)
    x_margin = int(img.width * 0.04)
    bottom_margin = int(img.height * 0.05)

    short_text = hook_text.strip().upper()

    font_size = int(img.height * 0.14)
    min_font_size = int(img.height * 0.05)

    while font_size > min_font_size:
        font = ImageFont.truetype(FONT_PATH, font_size)
        avg_char_w = font.getbbox("A")[2] - font.getbbox("A")[0]
        wrap_width = max(6, max_text_width // max(avg_char_w, 1))
        wrapped = textwrap.fill(short_text, width=wrap_width)

        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=10)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        if text_w <= max_text_width and text_h <= max_text_height:
            break
        font_size -= 4
    else:
        font = ImageFont.truetype(FONT_PATH, min_font_size)
        wrapped = textwrap.fill(short_text, width=14)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=10)
        text_h = bbox[3] - bbox[1]

    x = x_margin
    y = img.height - bottom_margin - text_h
    pad = 16
    draw.rectangle(
        [x - pad, y - pad, x + (bbox[2] - bbox[0]) + pad, y + text_h + pad],
        fill=(0, 0, 0, 140),
    )

    for dx in (-3, -1, 0, 1, 3):
        for dy in (-3, -1, 0, 1, 3):
            draw.multiline_text((x + dx, y + dy), wrapped, font=font,
                                 fill=(0, 0, 0, 255), spacing=10)
    draw.multiline_text((x, y), wrapped, font=font, fill=(255, 255, 255, 255), spacing=10)

    img.save(out_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--titles", required=True, help="generate_titles.py çıktısı")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--script", required=False, default="script.md",
                         help="Kapak konsepti için bağlam olarak kullanılacak script dosyası")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    with open(args.titles, "r", encoding="utf-8") as f:
        titles_data = json.load(f)

    title = titles_data["selected"][0]

    script_excerpt = ""
    if os.path.exists(args.script):
        with open(args.script, "r", encoding="utf-8") as f:
            script_excerpt = f.read()

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    concept = generate_thumbnail_concept(client, title, script_excerpt)

    raw_path = os.path.join(args.out_dir, "raw_1.png")
    generate_background(concept["visual_prompt"], raw_path)

    final_path = os.path.join(args.out_dir, "thumbnail_1.png")
    overlay_text(raw_path, concept["hook_text"], final_path)
    print(f"Kapak hazır -> {final_path}  "
          f"(hook: \"{concept['hook_text']}\", başlık: \"{title}\")")


if __name__ == "__main__":
    main()
