"""
Script'i sahnelere ayırır, her sahne için Wiro API (openai/gpt-image-2)
ile görsel üretir ve scenes/ klasörüne numaralı olarak kaydeder.

Basitleştirilmiş sahne bölme mantığı: her paragrafı bir sahne kabul
eder. Gerçek kullanımda kendi sahne-bölme kuralına göre uyarlaman
gerekir (örn. cümle sayısına veya saniyeye göre).

Kullanım:
    python scripts/generate_scenes.py --script script.md --out scenes/
"""
import argparse
import os

from wiro_client import run_model, download_output

STYLE_GUIDE = (
    "cinematic 3D animation style, warm lighting, tech/gaming themed, "
    "consistent character design across scenes, 16:9"
)


def split_into_scenes(script_text: str):
    paragraphs = [p.strip() for p in script_text.split("\n\n") if p.strip()]
    return paragraphs


def generate_image(prompt: str, out_path: str):
    full_prompt = f"{STYLE_GUIDE}. Scene: {prompt}"
    task = run_model("openai", "gpt-image-2", {
        "prompt": full_prompt,
        "resolution": "1k",
        "ratio": "16:9",
        "quality": "medium",
        "samples": 1,
    })
    download_output(task, out_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    with open(args.script, "r", encoding="utf-8") as f:
        script_text = f.read()

    scenes = split_into_scenes(script_text)

    for i, scene_text in enumerate(scenes, start=1):
        out_path = os.path.join(args.out, f"scene_{i:03d}.png")
        generate_image(scene_text, out_path)
        print(f"Sahne {i}/{len(scenes)} oluşturuldu -> {out_path}")


if __name__ == "__main__":
    main()
