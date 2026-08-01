"""
Yayın öncesi niş doğrulama.

Amaç: script.md ve title.json üretildikten sonra, içeriğin kanalın
niş tanımına (prompts/niche.md) gerçekten uyup uymadığını ayrı bir
Claude çağrısıyla kontrol eder. Uymuyorsa süreç DURDURULUR - voiceover,
görsel üretimi, upload gibi pahalı/geri alınamaz adımlara hiç geçilmez.

Kullanım:
    python scripts/niche_check.py --script script.md --titles titles.json

Çıkış kodu:
    0 -> niş uyumlu, workflow devam edebilir
    1 -> niş UYUMSUZ, workflow burada durmalı
"""
import argparse
import json
import os
import sys
import time

import anthropic

MODEL_UTILITY = "claude-haiku-4-5-20251001"
MAX_RETRIES = 4
RETRY_BASE_DELAY = 5

RETRYABLE_EXCEPTIONS = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,
    anthropic.RateLimitError,
)


def load_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def call_claude(client, prompt, model=MODEL_UTILITY, max_tokens=400):
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(b.text for b in response.content if b.type == "text")
        except RETRYABLE_EXCEPTIONS as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                print(f"  UYARI: Claude API geçici hata ({type(e).__name__}), "
                      f"{delay}sn sonra tekrar deneniyor "
                      f"(deneme {attempt + 1}/{MAX_RETRIES})...")
                time.sleep(delay)
    raise last_error


def check_niche_fit(client, niche, title, script):
    prompt = f"""Sen bu YouTube kanalının editoryal bekçisisin. Görevin,
üretilen içeriğin kanalın nişine GERÇEKTEN uyup uymadığını sert bir
şekilde denetlemek. Amaç bekçilik yapmak, nazik olmak değil.

KANALIN NİŞ TANIMI:
{niche}

ÜRETİLEN BAŞLIK:
{title}

ÜRETİLEN SCRIPT (ilk 800 karakter):
{script[:800]}

Soru: Bu içerik, yukarıdaki niş tanımına GERÇEKTEN uyuyor mu?

Kurallar:
- Sadece yüzeysel bir kelime örtüşmesi ("hardware" gibi çift anlamlı
  kelimeler) yeterli değil - konunun ÖZÜ niş ile uyumlu olmalı.
- Niş "teknoloji/oyun donanımı tarihi" ise; nörobilim, psikoloji,
  insan biyolojisi, genel "self-help" gibi konular UYUMSUZ sayılır,
  başlıkta teknoloji kelimesi geçse bile.
- Şüphedeysen (yüzde 50-50 gibi duruyorsa) UYUMSUZ say - riski
  yayınlamamak yayınlamaktan daha ucuzdur.

Çıktı SADECE şu JSON formatında olsun, başka hiçbir şey yazma:
{{"fits_niche": true/false, "confidence": 1-10 arası tam sayı, "reason": "kısa gerekçe"}}
"""
    raw = call_claude(client, prompt)
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Parse edilemezse güvenli tarafta kal: uyumsuz say, insan baksın
        return {"fits_niche": False, "confidence": 0,
                "reason": f"JSON parse edilemedi, ham yanıt: {raw[:200]}"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True, help="script.md yolu")
    parser.add_argument("--titles", required=True, help="titles.json yolu")
    args = parser.parse_args()

    niche = load_text("prompts/niche.md")
    script = load_text(args.script)

    with open(args.titles, "r", encoding="utf-8") as f:
        titles_data = json.load(f)
    # generate_titles.py'nin çıktı formatına göre uyarlanabilir
    title = titles_data.get("selected_title") or titles_data.get("title") or str(titles_data)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    result = check_niche_fit(client, niche, title, script)

    print(f"Niş kontrolü sonucu: {json.dumps(result, ensure_ascii=False)}")

    if not result.get("fits_niche", False):
        print(f"\n❌ DURDURULDU: İçerik niş tanımına uymuyor.")
        print(f"   Gerekçe: {result.get('reason', 'belirtilmemiş')}")
        print(f"   Güven skoru: {result.get('confidence', '?')}/10")
        sys.exit(1)

    print(f"\n✅ Niş uyumlu (güven: {result.get('confidence', '?')}/10), devam ediliyor.")
    sys.exit(0)


if __name__ == "__main__":
    main()

