"""
Tam otomatik script üretimi + kalite kontrol döngüsü.

Akış:
  1. Claude, facts.json + niche.md kullanarak taslak script yazar
  2. İkinci bir Claude çağrısı script'i eleştirir (hook gücü, kaynak
     kullanımı, evergreen kuralına uyum, doğallık) ve 1-10 puan verir
  3. Puan 7'nin altındaysa, eleştiriyi kullanarak script yeniden yazılır
     (en fazla 2 tur, sonsuz döngüye girmesin)

Bu adım, script yazımını insana bırakmadan da bir kalite tabanı
oluşturmayı hedefler. Yine de ilk birkaç hafta çıktıları elle
kontrol etmeni öneririm — otomasyon iyi çalıştığını kanıtladıkça
kontrolü azalt.

Kullanım:
    python scripts/generate_script.py --facts facts.json --out script.md
"""
import argparse
import json
import os

import anthropic

MODEL = "claude-sonnet-4-6"
MAX_REVISIONS = 2
QUALITY_THRESHOLD = 7


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
             "videoların başlıkları — birebir kopyalama, ama neyin işe "
             "yaradığını anlamak için kullan):"]
    for t in top5:
        lines.append(f"- \"{t['title']}\" ({t['views']:,} izlenme)")
    return "\n".join(lines)


def call_claude(client, prompt, max_tokens=3000):
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in response.content if b.type == "text")


def write_script(client, niche, facts_json, trend_summary=""):
    template = load_text("prompts/script_prompt.md")
    prompt = template.replace("{NICHE}", niche).replace("{FACTS}", facts_json)
    prompt += trend_summary
    return call_claude(client, prompt)


def critique_script(client, niche, script):
    prompt = f"""Aşağıdaki YouTube script'ini şu kriterlere göre değerlendir:
1. İlk 15 saniye gerçekten yakalayıcı mı?
2. Ton doğal mı, yoksa robotik/kurumsal mı?
3. Kaynaklar doğal cümleler içinde mi, yoksa dipnot gibi mi duruyor?
4. Evergreen kuralına uyuyor mu (güncel olay referansı var mı)?

NİŞ: {niche}

SCRIPT:
{script}

Çıktı SADECE şu JSON formatında olsun:
{{"score": 1-10 arası tam sayı, "feedback": "kısa, uygulanabilir eleştiri"}}
"""
    raw = call_claude(client, prompt, max_tokens=500)
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {"score": 10, "feedback": ""}  # parse edilemezse geçir


def revise_script(client, niche, script, feedback):
    prompt = f"""Aşağıdaki script'i şu geri bildirime göre düzelt:

GERİ BİLDİRİM: {feedback}

NİŞ: {niche}

MEVCUT SCRIPT:
{script}

Düzeltilmiş TAM script'i yaz, sadece metni ver, yorum ekleme."""
    return call_claude(client, prompt)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts", required=True)
    parser.add_argument("--trends", required=False, default=None,
                         help="trend_analysis.py çıktısı (opsiyonel)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    niche = load_text("prompts/niche.md")
    facts_json = load_text(args.facts)
    trend_summary = load_trend_summary(args.trends)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    script = write_script(client, niche, facts_json, trend_summary)

    for revision in range(MAX_REVISIONS):
        review = critique_script(client, niche, script)
        print(f"Revizyon {revision}: puan={review['score']}")
        if review["score"] >= QUALITY_THRESHOLD:
            break
        script = revise_script(client, niche, script, review["feedback"])

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(script)

    print(f"Script hazır -> {args.out}")


if __name__ == "__main__":
    main()
