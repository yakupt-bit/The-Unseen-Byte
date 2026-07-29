"""
Script için 8 başlık adayı üretir, içlerinden EN GÜÇLÜ olanı seçer ve
titles.json'a kaydeder.

NOT: YouTube'un native "Test & Compare" (A/B testing) özelliği sadece
YouTube Studio arayüzünden (desktop, elle) kullanılabiliyor, API
üzerinden erişilemiyor; ayrıca YouTube Partner Program (YPP) üyeliği
gerektiriyor. Kanal bu eşiklere ulaşana kadar tek başlık üretmek daha
mantıklı - bu yüzden A/B akışı kaldırıldı, sistem artık doğrudan en
güçlü tek başlığı seçip kullanıyor.

Kullanım:
    python scripts/generate_titles.py --script script.md --out titles.json
"""
import argparse
import json
import os

import anthropic

MODEL_CREATIVE = "claude-sonnet-4-6"
MODEL_UTILITY = "claude-haiku-4-5-20251001"
BRAND_SUFFIX = " | The Unseen Byte"


def call_claude(client, prompt, model, max_tokens=800):
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in response.content if b.type == "text")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    with open(args.script, "r", encoding="utf-8") as f:
        script = f.read()

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    gen_prompt = f"""Bu script için 8 farklı YouTube başlığı öner.
ÖNEMLİ: Başlıkların TAMAMI İNGİLİZCE olmalı, tek bir Türkçe kelime bile
kullanma - kanal İngilizce ve global bir kitleye hitap ediyor.

Her biri merak açığı yaratmalı (bilgiyi tam vermeden merak uyandırmalı),
abartılı/yalan olmamalı, 60 karakteri geçmemeli, tık tuzağı olmamalı.

KRİTİK KURAL - başlık kapağın CEVABI değil GELİŞMESİDİR:
Kapak görseli izleyiciye bir soru/gizem sunar (örn. bir nesne, bir
çelişkili durum). Başlık bu sorunun CEVABINI VERMEZ, sadece konunun
BAĞLAMINI/GELİŞMESİNİ ekler - somut bir çelişki, rakam veya anomali
içerir (ör. "sold for $X", "hidden for Y years", "no one noticed").
Asıl cevap/sonuç SADECE videoyu izleyince ortaya çıkmalı. Başlık,
kapaktaki merakı KAPATMAMALI, bir adım daha derinleştirmeli.

Global çapta kanıtlanmış gizem/belgesel kanallarından çıkarılan
kalıpları kullan, adaylar bu FARKLI YAKLAŞIMLARI temsil etsin:
1. Soru formatı ("Is X Really Y?", "Why Does X Happen?")
2. "Ne oldu" gizem çerçevesi ("What Really Happened to X")
3. Güçlü iddia + merak açığı ("The Real Reason X Never Y")
4. Sayı/liste formatı ("X Things You Didn't Know About Y")
5. Doğrudan izleyiciye hitap eden meydan okuma tarzı

SCRIPT:
{script}

Çıktı SADECE JSON dizi, İngilizce başlıklarla: ["title1", "title2", ...]"""

    raw_candidates = call_claude(client, gen_prompt, MODEL_CREATIVE)
    cleaned = raw_candidates.replace("```json", "").replace("```", "").strip()
    candidates = json.loads(cleaned)

    rank_prompt = f"""Aşağıdaki İngilizce YouTube başlık adaylarından EN
GÜÇLÜ tek bir tanesini seç. Kriterler: merak açığı gücü (cevabı
vermeden gelişmeyi vermesi), netlik, özgünlük hissi, tık tuzağı
olmaması.

ADAYLAR: {json.dumps(candidates, ensure_ascii=False)}

Çıktı SADECE JSON (başlık İngilizce kalacak, gerekçe Türkçe olabilir):
{{"selected": "title", "reason": "gerekçe"}}"""

    raw_rank = call_claude(client, rank_prompt, MODEL_UTILITY, max_tokens=300)
    cleaned_rank = raw_rank.replace("```json", "").replace("```", "").strip()
    result = json.loads(cleaned_rank)

    final_title = result["selected"]
    if not final_title.endswith(BRAND_SUFFIX):
        final_title += BRAND_SUFFIX

    output = {"selected": [final_title], "reasons": [result["reason"]]}

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Seçilen başlık: {final_title}  ({result['reason']})")


if __name__ == "__main__":
    main()
