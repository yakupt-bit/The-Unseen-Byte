"""
titles.json'daki 3 başlığın her biri için AYRI bir kapak (thumbnail)
üretir (Wiro API, openai/gpt-image-2) ve üzerine metin bindirir (PIL).
Sonuç: 3 farklı kapak dosyası - YouTube Studio'nun native A/B Testing
özelliğine elle yüklenmek üzere hazırlanır (bkz. generate_titles.py).

Kırmızı daire/ok vurgusu KODLA (PIL) garantili şekilde ekleniyor -
görsel üretim modelinin bunu otomatik eklemesini garanti edemeyiz.

Kullanım:
    python scripts/generate_thumbnail.py --titles titles.json --out-dir output/thumbnails/
"""
import argparse
import json
import os
import random
import textwrap

from PIL import Image, ImageDraw, ImageFont

from wiro_client import run_model, download_output

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


def overlay_text(image_path: str, text: str, out_path: str):
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")

    draw_annotation(draw, img.width, img.height)

    max_text_width = int(img.width * 0.92)
    max_text_height = int(img.height * 0.38)
    x_margin = int(img.width * 0.04)
    bottom_margin = int(img.height * 0.05)

    words = text.split()
    short_text = " ".join(words[:6]).upper()

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
    parser.add_argument("--titles", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    with open(args.titles, "r", encoding="utf-8") as f:
        titles_data = json.load(f)

    for i, title in enumerate(titles_data["selected"], start=1):
        raw_path = os.path.join(args.out_dir, f"raw_{i}.png")
        generate_background(title, raw_path)

        final_path = os.path.join(args.out_dir, f"thumbnail_{i}.png")
        overlay_text(raw_path, title, final_path)
        print(f"Kapak {i}/3 hazır -> {final_path}  (\"{title}\")")


if __name__ == "__main__":
    main()
