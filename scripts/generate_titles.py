"""
Script için 8 başlık adayı üretir, en güçlü 3'ünü seçer ve
titles.json'a kaydeder. Bu 3 başlık, YouTube Studio'nun native
"A/B Testing (Test & Compare)" özelliğine elle yüklenmek üzere
hazırlanır — kazananı biz değil, YouTube'un gerçek izleyici verisi
seçer (izlenme süresi payına göre). Bu adım YouTube API üzerinden
otomatik kurulamıyor (Studio-only özellik), video yüklendikten sonra
Studio > İçerik > ilgili video > A/B Testing bölümüne bu 3 başlığı
elle eklemen gerekiyor.

Kullanım:
    python scripts/generate_titles.py --script script.md --out titles.json
"""
import argparse
import json
import os

import anthropic

MODEL = "claude-sonnet-4-6"
NUM_VARIANTS = 3


def call_claude(client, prompt, max_tokens=800):
    response = client.messages.create(
        model=MODEL,
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
Her biri merak açığı yaratmalı (bilgiyi tam vermeden merak uyandırmalı),
abartılı/yalan olmamalı, 60 karakteri geçmemeli, tık tuzağı olmamalı.
Adaylar birbirinden GERÇEKTEN farklı açılardan yaklaşmalı (biri soru
formatı, biri iddia formatı, biri sayı/liste formatı gibi) — böylece
A/B testinde anlamlı bir karşılaştırma olur.

SCRIPT:
{script}

Çıktı SADECE JSON dizi: ["başlık1", "başlık2", ...]"""

    raw_candidates = call_claude(client, gen_prompt)
    cleaned = raw_candidates.replace("```json", "").replace("```", "").strip()
    candidates = json.loads(cleaned)

    rank_prompt = f"""Aşağıdaki YouTube başlık adaylarından en güçlü
{NUM_VARIANTS} tanesini seç. Kriterler: merak açığı gücü, netlik,
özgünlük hissi, VE birbirinden farklı yaklaşımlar olması (aynı kalıbın
tekrarı olmasın — A/B testi anlamlı olsun diye).

ADAYLAR: {json.dumps(candidates, ensure_ascii=False)}

Çıktı SADECE JSON: {{"selected": ["başlık1", "başlık2", "başlık3"], "reasons": ["gerekçe1", "gerekçe2", "gerekçe3"]}}"""

    raw_rank = call_claude(client, rank_prompt, max_tokens=500)
    cleaned_rank = raw_rank.replace("```json", "").replace("```", "").strip()
    result = json.loads(cleaned_rank)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("Seçilen 3 başlık (YouTube A/B testine yükle):")
    for title, reason in zip(result["selected"], result["reasons"]):
        print(f"  - {title}  ({reason})")


if __name__ == "__main__":
    main()

