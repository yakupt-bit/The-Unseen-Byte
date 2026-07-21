"""
Haftalık konu araştırması yapar, Claude API'ye prompts/research_prompt.md
şablonunu gönderir ve sonucu facts.json olarak kaydeder.

Elle bir TOPIC_HINT verilmezse, niche.md'deki 4 alt-nişten (Gaming
Psychology / Tech & Gaming History / Industry Secrets / Hardware &
Science Myths) GÜNE GÖRE DÖNEREK biri otomatik seçilir - böylece art
arda çalıştırmalarda hep aynı konu tekrarlanmaz. Rotasyon takvim
gününe (ordinal date) bağlı, kalıcı bir durum dosyasına ihtiyaç yok.

Kullanım:
    python scripts/research.py --out facts.json
"""
import argparse
import datetime
import json
import os

import anthropic

SUB_NICHES = [
    "Gaming Psychology - how games affect the brain, addiction science, competition and reward mechanics",
    "Tech & Gaming History - forgotten hardware/software stories, cancelled projects, unknown industry turning points",
    "Industry Secrets - behind-the-scenes of gaming/tech companies, production processes, lesser-known decisions",
    "Hardware & Science Myths - hardware legends, technical misconceptions, debunking or confirming with science",
]


def pick_sub_niche() -> str:
    idx = datetime.date.today().toordinal() % len(SUB_NICHES)
    return SUB_NICHES[idx]


def load_prompt(topic_hint: str) -> str:
    with open("prompts/research_prompt.md", "r", encoding="utf-8") as f:
        template = f.read()
    return template.replace("{TOPIC_HINT}", topic_hint)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    topic_hint = os.environ.get("TOPIC_HINT", "").strip()
    if not topic_hint:
        topic_hint = pick_sub_niche()
        print(f"Konu ipucu verilmedi, otomatik alt-niş seçildi: {topic_hint}")

    prompt = load_prompt(topic_hint)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = "".join(
        block.text for block in response.content if block.type == "text"
    )

    cleaned = raw_text.replace("```json", "").replace("```", "").strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        data = {"raw": cleaned}

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Araştırma tamamlandı -> {args.out}")


if __name__ == "__main__":
    main()
