"""
titles.json'daki 3 başlığın her biri için AYRI bir kapak (thumbnail)
üretir (Wiro API, openai/gpt-image-2) ve üzerine metin bindirir (PIL).
Sonuç: 3 farklı kapak dosyası — YouTube Studio'nun native A/B Testing
özelliğine elle yüklenmek üzere hazırlanır (bkz. generate_titles.py).

Kullanım:
    python scripts/generate_thumbnail.py --titles titles.json --out-dir output/thumbnails/
"""
import argparse
import json
import os
import textwrap

from PIL import Image, ImageDraw, ImageFont

from wiro_client import run_model, download_output

THUMBNAIL_STYLE = (
    "bold high-contrast digital illustration, dramatic lighting, "
    "single clear focal point, vivid saturated colors, tech/gaming "
    "aesthetic, leaves empty space in one corner for text overlay, "
    "no existing text in the image, 16:9"
)

FONT_PATH = "assets/fonts/Anton-Regular.ttf"


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


def overlay_text(image_path: str, text: str, out_path: str):
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)

    words = text.split()
    short_text = " ".join(words[:5]).upper()
    wrapped = textwrap.fill(short_text, width=14)

    font_size = int(img.height * 0.12)
    font = ImageFont.truetype(FONT_PATH, font_size)

    x, y = int(img.width * 0.05), int(img.height * 0.65)
    for dx in (-4, -2, 0, 2, 4):
        for dy in (-4, -2, 0, 2, 4):
            draw.multiline_text((x + dx, y + dy), wrapped, font=font,
                                 fill="black", spacing=10)
    draw.multiline_text((x, y), wrapped, font=font, fill="white", spacing=10)

    img.save(out_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--titles", required=True, help="generate_titles.py çıktısı (3 başlık)")
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
