"""
Tam otomatik script üretimi + kalite kontrol döngüsü.

Akış:
  1. Claude, facts.json + niche.md kullanarak taslak script yazar
  2. İkinci bir Claude çağrısı script'i eleştirir (hook gücü, kaynak
     kullanımı, evergreen kuralına uyum, doğallık, İLK ÖNİZLEME-ÖDÜLÜ VE
     PERİYODİK ÖDÜL RİTMİ) ve 1-10 puan verir
  3. Puan 7'nin altındaysa, eleştiriyi kullanarak script yeniden yazılır
     (en fazla 2 tur, sonsuz döngüye girmesin)
  4. Son olarak, script.md'ye yazmadan önce her türlü markdown başlığı/
     zaman damgası temizlenir

RETENTION GÜNCELLEMESİ: Ortalama izlenme süresinin videonun ilk
2-2.5 dakikasında düştüğü gözlemlendi. Buna karşılık script_prompt.md'ye
(1) hook'tan hemen sonra sonun kısa bir önizlemesi + ilk 90 saniyede
küçük bir "ilk ödül" bulgusu, (2) videonun geri kalanında YAKLAŞIK HER
2-2.5 DAKİKADA BİR tekrar eden küçük mini-reveal'lar kuralı eklendi.
critique_script bu ikisinin script'te GERÇEKTEN uygulanıp uygulanmadığını
da artık kontrol ediyor (bkz. kriter 7-8).

Her Claude API çağrısı geçici hatalara (500, rate limit, bağlantı
kopması) karşı otomatik olarak yeniden dener (bkz. call_claude).

Kullanım:
    python scripts/generate_script.py --facts facts.json --out script.md
    python scripts/generate_script.py --facts facts.json --out script.md --test
"""
import argparse
import json
import os
import re
import time

import anthropic

MODEL_CREATIVE = "claude-sonnet-4-6"
MODEL_UTILITY = "claude-haiku-4-5-20251001"
MAX_REVISIONS = 2
QUALITY_THRESHOLD = 7
SCRIPT_MAX_TOKENS = 8000

MAX_RETRIES = 4
RETRY_BASE_DELAY = 5  # saniye, üstel: 5, 10, 20, 40

RETRYABLE_EXCEPTIONS = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,
    anthropic.RateLimitError,
)

TONE_AND_STYLE_RULES = """

TON VE STİL KURALLARI (mutlaka uygula):
- Resmi bir rapor gibi değil, meraklı ve bilgili bir arkadaşın anlattığı
  gibi yaz. Kısa, doğal, konuşma diline yakın cümleler kur.
- Gerektiğinde belirsizliği açıkça kabul et: "Kaynaklar burada tam
  örtüşmüyor", "Bunun tam olarak nasıl olduğu hâlâ net değil" gibi
  ifadeler kullanmaktan çekinme - bu, sahte kesinlikten daha güvenilir
  durur ve daha insan hissettirir.
- Somutlaştırıcı referanslar kullan: "arşiv kayıtlarına göre",
  "koleksiyoncular arasında bilinen bir ayrıntı" gibi ifadelerle
  anlatıyı köklendir.
- MERAK AÇIĞINI videonun genelinde koru: en can alıcı bilgiyi/sonucu
  ortaya doğru veya sona doğru ver, başlarda her şeyi açıklama.
  Video, başlıkta/kapakta sorulan sorunun CEVABI olmalı.
"""


def strip_meta_formatting(text: str) -> str:
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append(line)
            continue
        if stripped.startswith("#"):
            continue
        if re.match(r"^(Section|Chapter|Part)\s*\d*\s*:?\s*$", stripped, re.I):
            continue
        if re.match(r"^\[?\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}\]?$", stripped):
            continue
        stripped = re.sub(r"\[?\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}\]?", "", stripped)
        stripped = re.sub(r"^#+\s*", "", stripped)
        cleaned_lines.append(stripped)
    text = "\n".join(cleaned_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def load_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_trend_summary(trend_path):
    if not trend_path or not os.path.exists(trend_path):
        return ""
    with open(trend_path, "r", encoding="utf-8") as f:
        trends = json.load(f)
    top5 = trends[:5]
    lines = ["\n\nGÜNCEL TREND REFERANSI (bu nişte şu an gerçekten izlenen "
             "videoların başlıkları - birebir kopyalama, ama neyin işe "
             "yaradığını anlamak için kullan):"]
    for t in top5:
        lines.append(f"- \"{t['title']}\" ({t['views']:,} izlenme)")
    return "\n".join(lines)


def call_claude(client, prompt, model=MODEL_CREATIVE, max_tokens=3000):
    """Claude'a istek atar; geçici hatalarda (500/rate limit/bağlantı)
    üstel bekleme ile otomatik olarak yeniden dener."""
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


def write_script(client, niche, facts_json, trend_summary="", test_mode=False):
    template = load_text("prompts/script_prompt.md")
    prompt = template.replace("{NICHE}", niche).replace("{FACTS}", facts_json)
    prompt += trend_summary
    prompt += TONE_AND_STYLE_RULES
    if test_mode:
        prompt += (
            "\n\nTEST MODU: Bu bir pipeline testi, gerçek yayın değil. "
            "Script'i SADECE 120-180 kelime uzunluğunda yaz (yaklaşık "
            "45-60 saniyelik video), 3-4 kısa paragraf halinde. Hook ve "
            "ton kuralları hâlâ geçerli, sadece çok daha kısa olsun. "
            "Yine de sonunda kısa, samimi bir kapanış/abone çağrısı "
            "cümlesi olsun (1 cümle yeterli)."
        )
        return call_claude(client, prompt, max_tokens=1000)
    return call_claude(client, prompt, max_tokens=SCRIPT_MAX_TOKENS)


def critique_script(client, niche, script):
    prompt = f"""Aşağıdaki YouTube script'ini şu kriterlere göre değerlendir:
1. İlk 15 saniye gerçekten yakalayıcı mı?
2. Ton doğal mı, yoksa robotik/kurumsal mı? Samimi, konuşma diline
   yakın mı, yoksa "yapay zeka odunu" gibi mi duruyor?
3. Kaynaklar doğal cümleler içinde mi, yoksa dipnot gibi mi duruyor?
4. Evergreen kuralına uyuyor mu (güncel olay referansı var mı)?
5. Script, en can alıcı bilgiyi/sonucu erkenden mi veriyor, yoksa
   merak açığını sona doğru mu koruyor?
6. Script'te markdown başlığı (#), bölüm etiketi veya zaman damgası
   ([0:45-4:00] gibi) VAR MI? Varsa mutlaka feedback'te belirt, bunlar
   asla olmamalı.
7. RETENTION - AÇILIŞ ÖNİZLEMESİ: Hook'tan (ilk 15sn) hemen sonra,
   script DOĞRUDAN soyut bir arka plan/tarihçe/tanım anlatımına mı
   geçiyor (KÖTÜ - izleyici kaybı riski yüksek), yoksa hook'tan sonraki
   birkaç cümlede sonun kısa bir önizlemesi/ipucu var mı VE ilk ~90
   saniye içinde somut, küçük bir ilk bulgu/"aha" anı veriliyor mu
   (İYİ)? Yoksa mutlaka feedback'te belirt.
8. RETENTION - PERİYODİK ÖDÜL RİTMİ: Script'in TAMAMI boyunca (sadece
   başında değil), yaklaşık her 300-350 kelimede bir (≈2-2.5 dakika)
   küçük bir mini-reveal/şaşırtıcı detay/alt-merak açığı çözümü var mı,
   yoksa bazı bölümler (özellikle ortada) uzun, düz, ödülsüz bir bağlam/
   dolgu bloğu gibi mi duruyor? Düz/ödülsüz uzun bir bölüm varsa mutlaka
   feedback'te hangi bölüm olduğunu belirt.

NİŞ: {niche}

SCRIPT:
{script}

Çıktı SADECE şu JSON formatında olsun:
{{"score": 1-10 arası tam sayı, "feedback": "kısa, uygulanabilir eleştiri"}}
"""
    raw = call_claude(client, prompt, model=MODEL_UTILITY, max_tokens=500)
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {"score": 10, "feedback": ""}


def revise_script(client, niche, script, feedback):
    prompt = f"""Aşağıdaki script'i şu geri bildirime göre düzelt:

GERİ BİLDİRİM: {feedback}

NİŞ: {niche}

MEVCUT SCRIPT:
{script}
{TONE_AND_STYLE_RULES}

Düzeltilmiş TAM script'i yaz, sadece metni ver, yorum ekleme.
ÖNEMLİ: Script %100 İngilizce olmalı, Türkçe kelime kullanma.
ÖNEMLİ: Markdown başlığı (#), bölüm etiketi, zaman damgası OLMASIN."""
    return call_claude(client, prompt, max_tokens=SCRIPT_MAX_TOKENS)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts", required=True)
    parser.add_argument("--trends", required=False, default=None,
                         help="trend_analysis.py çıktısı (opsiyonel)")
    parser.add_argument("--test", action="store_true",
                         help="Hızlı test modu: çok kısa script üretir (~150 kelime)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    niche = load_text("prompts/niche.md")
    facts_json = load_text(args.facts)
    trend_summary = load_trend_summary(args.trends)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    script = write_script(client, niche, facts_json, trend_summary, test_mode=args.test)

    for revision in range(MAX_REVISIONS):
        review = critique_script(client, niche, script)
        print(f"Revizyon {revision}: puan={review['score']}")
        if review["score"] >= QUALITY_THRESHOLD:
            break
        script = revise_script(client, niche, script, review["feedback"])

    script = strip_meta_formatting(script)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(script)

    print(f"Script hazır -> {args.out}")


if __name__ == "__main__":
    main()
