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

import anthropic

from wiro_client import run_model, download_output

STYLE_GUIDE = (
    "cinematic documentary B-roll style, warm dramatic lighting, "
    "tech/gaming themed, atmospheric and detailed, 16:9"
)

MODEL = "claude-sonnet-4-6"


def split_into_scenes(script_text: str):
    paragraphs = [p.strip() for p in script_text.split("\n\n") if p.strip()]
    return paragraphs


def to_safe_visual_prompt(client, narration_paragraph: str) -> str:
    """Ham anlatım cümlesini güvenli, soyut, tamamen görsel bir sahne
    tarifine çevirir. Gerçek isim/marka/kişi/olay YOK, sadece görsel
    unsurlar (kompozisyon, nesneler, atmosfer) olsun.

    ONEMLI: Insan yuzu MUMKUN OLDUGUNCA AZ kullanilir. Konu bir nesne/
    yer/kavramsa SADECE onunla ilgili gorsel tarif et."""
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


def generate_image_with_fallback(client, narration_paragraph: str, out_path: str):
    safe_prompt = to_safe_visual_prompt(client, narration_paragraph)
    try:
        generate_image(safe_prompt, out_path)
    except RuntimeError as e:
        if "safety system" in str(e).lower():
            print("  UYARI: güvenlik reddi, jenerik tarifle yeniden deniyorum...")
            fallback_prompt = "an abstract, atmospheric technology-themed background, soft glowing shapes, no people, no text"
            generate_image(fallback_prompt, out_path)
        else:
            raise


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
