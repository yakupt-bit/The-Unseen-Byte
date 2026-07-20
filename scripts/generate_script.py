"""
Tam otomatik script üretimi + kalite kontrol döngüsü.

Akış:
  1. Claude, facts.json + niche.md kullanarak taslak script yazar
  2. İkinci bir Claude çağrısı script'i eleştirir (hook gücü, kaynak
     kullanımı, evergreen kuralına uyum, doğallık) ve 1-10 puan verir
  3. Puan 7'nin altındaysa, eleştiriyi kullanarak script yeniden yazılır
     (en fazla 2 tur, sonsuz döngüye girmesin)

Kullanım:
    python scripts/generate_script.py --facts facts.json --out script.md
    python scripts/generate_script.py --facts facts.json --out script.md --test
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


def write_script(client, niche, facts_json, trend_summary="", test_mode=False):
    template = load_text("prompts/script_prompt.md")
    prompt = template.replace("{NICHE}", niche).replace("{FACTS}", facts_json)
    prompt += trend_summary
    if test_mode:
        prompt += (
            "\n\nTEST MODU: Bu bir pipeline testi, gerçek yayın değil. "
            "Script'i SADECE 120-180 kelime uzunluğunda yaz (yaklaşık "
            "45-60 saniyelik video), 3-4 kısa paragraf halinde. Hook ve "
            "ton kuralları hâlâ geçerli, sadece çok daha kısa olsun."
        )
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
        return {"score": 10, "feedback": ""}


def revise_script(client, niche, script, feedback):
    prompt = f"""Aşağıdaki script'i şu geri
