"""
Script'i sahnelere ayırır. Her sahne için ÖNCE Claude ile ham anlatım
metnini güvenli, tamamen görsel bir sahne tarifine çevirir (isim/marka/
gerçek kişi gibi güvenlik reddi tetikleyebilecek unsurları temizler,
insan yüzünü minimize eder), SONRA o tarifi Wiro API (openai/gpt-image-2)
ile görsele çevirir.

Kullanım:
    python scripts/generate_scenes.py --script script.md --out scenes/
"""
import argparse
import os
import random

import anthropic

from wiro_client import run_model, download_output

STYLE_GUIDE = (
    "cinematic documentary B-roll style, warm dramatic lighting, "
    "tech/gaming themed, atmospheric and detailed, 16:9"
)

MODEL = "claude-sonnet-4-6"

MAX_SCENES = 20


def split_into_scenes(script_text: str):
    paragraphs = [p.strip() for p in script_text.split("\n\n") if p.strip()]
    if len(paragraphs) <= MAX_SCENES:
        return paragraphs

    merged = []
    group_size = -(-len(paragraphs) // MAX_SCENES)
    for i in range(0, len(paragraphs), group_size):
        merged.append("\n\n".join(paragraphs[i:i + group_size]))
    return merged


def to_safe_visual_prompt(client, narration_paragraph: str) -> str:
    prompt = f"""Aşağıdaki YouTube anlatım cümlesini, bir görsel üretim
AI'sine gönderilecek KISA (1-2 cümle) bir SAHNE TARİFİNE çevir.

Kurallar:
- Gerçek kişi, marka, oyun adı, şirket adı KULLANMA - bunun yerine
  soyut/temsili görseller tarif et.
- INSAN YUZU COK AZ KULLAN. Öncelik sırası: (1) eğer konu bir nesne,
  yer, ekran, kavram ise SADECE onu tarif et, insan hiç olmasın.
  (2) konu gerçekten bir kişiyi/insan davranışını gerektiriyorsa,
  yüzü göstermeyen temsili bir tasvir kullan: silüet, arkadan çekim,
  sadece eller, uzak/genel plan, karanlıkta siluet gibi. Yakın plan
  net yüz gösterme.
- Sadece GÖRSEL unsurları anlat: kompozisyon, nesneler, ortam, ışık.
  Anlatının kendisini veya iddiaları tekrar etme.
  Şiddet, silah, kan içeren hiçbir şey yazma.
- İngilizce yaz.

ANLATIM CÜMLESİ:
{narration_paragraph}

SADECE sahne tarifini yaz, başka bir şey ekleme."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in response.content if b.type == "text").strip()


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


FALLBACK_PROMPTS = [
    "a moody abstract technology visualization, flowing data streams, deep blue and purple gradient, no people, no text",
    "a dramatic close-up of glowing circuit board patterns, warm amber lighting, macro photography style, no people, no text",
    "an atmospheric server room corridor with soft blue light trails, cinematic depth of field, no people, no text",
    "a stack of retro gaming cartridges and consoles on a wooden desk, warm dramatic side lighting, no people, no text",
    "an abstract network of glowing connected nodes on a dark background, cinematic and mysterious, no people, no text",
]


def generate_image_with_fallback(client, narration_paragraph: str, out_path: str):
    safe_prompt = to_safe_visual_prompt(client, narration_paragraph)
    try:
        generate_image(safe_prompt, out_path)
        return
    except RuntimeError as e:
        if "safety system" not in str(e).lower():
            raise

    print("  UYARI: güvenlik reddi, daha soyut bir tarifle tekrar deniyorum...")
    try:
        stricter_prompt = (
            "a completely abstract, symbolic visual representation (no "
            "literal depiction) inspired by this idea, purely artistic "
            f"shapes/colors/lighting only: {narration_paragraph[:150]}"
        )
        even_safer = to_safe_visual_prompt(client, stricter_prompt)
        generate_image(even_safer, out_path)
        return
    except RuntimeError as e:
        if "safety system" not in str(e).lower():
            raise

    print("  UYARI: ikinci deneme de reddedildi, çeşitli jenerik görsellerden biriyle devam ediyorum...")
    fallback_prompt = random.choice(FALLBACK_PROMPTS)
    generate_image(fallback_prompt, out_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    with open(args.script, "r", encoding="utf-8") as f:
        script_text = f.read()

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    scenes = split_into_scenes(script_text)

    for i, scene_text in enumerate(scenes, start=1):
        out_path = os.path.join(args.out, f"scene_{i:03d}.png")
        generate_image_with_fallback(client, scene_text, out_path)
        print(f"Sahne {i}/{len(scenes)} oluşturuldu -> {out_path}")


if __name__ == "__main__":
    main()
